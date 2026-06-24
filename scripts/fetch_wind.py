#!/usr/bin/env python3
"""
从 Wind MCP 拉取数据，补充 THS EDB 无法覆盖或数据源不一致的序列。

用法:
  python3 scripts/fetch_wind.py                           # 拉取所有 Wind 映射的序列
  python3 scripts/fetch_wind.py --dry-run                 # 干跑，不写库
  python3 scripts/fetch_wind.py --series trend:300收益    # 仅拉取单个序列
  python3 scripts/fetch_wind.py --verbose                 # 详细输出
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from lib import (
    ROOT, DEFAULT_DB, log, load_json, open_db,
    get_validation_dates, values_match,
)

DEFAULT_MAPPING = ROOT / "config" / "wind_mapping.json"
WIND_SKILL_DIR = Path(os.environ.get(
    "WIND_SKILL_DIR",
    str(Path.home() / ".claude" / "skills" / "wind-mcp-skill")
))

# Global counter for Wind API calls
WIND_CALL_COUNT = 0


# ── Wind CLI ─────────────────────────────────────────────────────────

def call_wind_cli(server_type, tool_name, params, timeout=30):
    """
    调用 Wind MCP CLI，返回解析后的 Python 对象或 None。
    params 是 dict，自动序列化为 JSON 字符串。
    """
    global WIND_CALL_COUNT
    params_json = json.dumps(params, ensure_ascii=False)
    cmd = ["node", "scripts/cli.mjs", "call", server_type, tool_name, params_json]
    WIND_CALL_COUNT += 1

    try:
        result = subprocess.run(
            cmd,
            cwd=str(WIND_SKILL_DIR),
            capture_output=True,
            text=True,
            timeout=timeout
        )
    except subprocess.TimeoutExpired:
        log(f"Wind CLI timeout after {timeout}s", "ERROR")
        return None
    except Exception as e:
        log(f"Wind CLI error: {e}", "ERROR")
        return None

    if result.returncode != 0:
        # Try to extract error info
        try:
            err = json.loads(result.stdout) if result.stdout.strip() else {}
            error_info = err.get("error", {})
            code = error_info.get("code", "UNKNOWN")
            msg = error_info.get("message", result.stderr[:200] if result.stderr else "no stderr")
        except (json.JSONDecodeError, ValueError, KeyError):
            msg = result.stderr[:200] if result.stderr else result.stdout[:200]
        log(f"Wind CLI exit={result.returncode}: {msg}", "ERROR")
        return None

    try:
        outer = json.loads(result.stdout)
    except json.JSONDecodeError:
        log("Wind CLI returned invalid JSON", "ERROR")
        return None

    if outer.get("isError"):
        log(f"Wind MCP error: {outer}", "ERROR")
        return None

    content = outer.get("content", [])
    if not content:
        return None

    try:
        inner = json.loads(content[0]["text"])
    except (json.JSONDecodeError, KeyError, IndexError):
        log("Failed to parse Wind inner response", "ERROR")
        return None

    if inner.get("error"):
        log(f"Wind business error: {inner['error']}", "ERROR")
        return None

    return inner.get("data")


# ── K-line fetch ─────────────────────────────────────────────────────

def fetch_kline(windcode, begin_date, end_date):
    """
    调用 index_data.get_index_kline，返回 [[date_str, close_value], ...] 升序排列。
    """
    params = {
        "windcode": windcode,
        "begin_date": begin_date.strftime("%Y%m%d"),
        "end_date": end_date.strftime("%Y%m%d")
    }
    data = call_wind_cli("index_data", "get_index_kline", params)
    if not data:
        return None

    # data 结构: {columns: [...], rows: [[...], ...], windcode: "..."}
    rows = data.get("rows", [])
    if not rows:
        return None

    # MATCH (收盘价) 列索引通常是 2
    points = []
    for row in rows:
        if len(row) < 3:
            continue
        raw_date = row[-1][:8]  # _DATE 列: yyyyMMdd
        # 转换为 ISO 格式 yyyy-MM-dd
        try:
            date_str = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
        except IndexError:
            continue
        try:
            close_val = float(row[2])  # MATCH 列
        except (ValueError, TypeError):
            continue
        points.append([date_str, close_val])

    # 按日期升序排列
    points.sort(key=lambda x: x[0])
    return points


# ── Economic data fetch ──────────────────────────────────────────────

def fetch_economic(metric_ids_str, indicator_filter, begin_date, end_date):
    """
    调用 economic_data.get_economic_data，返回 [[date_str, value], ...] 升序排列。
    从返回的多个指标中按 indicator_filter（名称包含匹配）筛选。
    """
    params = {
        "metricIdsStr": metric_ids_str,
        "freq": "日",
        "beginDate": begin_date.strftime("%Y%m%d"),
        "endDate": end_date.strftime("%Y%m%d")
    }
    data = call_wind_cli("economic_data", "get_economic_data", params)
    if not data:
        return None

    # data 结构: {date: [...], indicatorInfo: [{name, code, data: [...]}, ...]}
    dates = data.get("date", [])
    indicators = data.get("indicatorInfo", [])
    if not dates or not indicators:
        return None

    # 筛选匹配的指标：优先精确名匹配，再退回包含匹配（substring）
    # Wind NL 查询返回的候选集不确定，substring 匹配可能误中名字含 filter
    # 但实为其他口径的指标（如 DXY 的 filter "美元指数" 误中值 36.4 的变体）。
    # 精确名匹配优先可避免此类误匹配。
    target = None
    fl = indicator_filter.lower()
    for ind in indicators:  # 1st pass: exact name match
        if ind.get("name", "").lower() == fl:
            target = ind
            break
    if not target:  # 2nd pass: substring match
        for ind in indicators:
            if fl in ind.get("name", "").lower():
                target = ind
                break

    # 如果没精确匹配，取第一个有数据的
    if not target:
        for ind in indicators:
            vals = [v for v in ind.get("data", []) if v is not None]
            if vals:
                target = ind
                break

    if not target:
        return None

    values = target.get("data", [])
    points = []
    for d, v in zip(dates, values):
        if v is not None:
            try:
                raw_date = str(d)
                date_str = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
                points.append([date_str, float(v)])
            except (ValueError, TypeError, IndexError):
                continue

    points.sort(key=lambda x: x[0])
    return points


# ── Valuation fetch ──────────────────────────────────────────────────

def fetch_valuation(wind_query):
    """
    调用 index_data.get_index_fundamentals，返回 {pe, pb, dy} 或 None。
    按列名匹配（非位置），因为 Wind 返回的列顺序不固定。
    """
    question = f"{wind_query}最新PE TTM和PB LF和股息率"
    data = call_wind_cli("index_data", "get_index_fundamentals", {"question": question})
    if not data:
        return None

    # data 结构可能是 {"data": [...]} 或直接是 [...]
    rows_list = data.get("data", data)
    if isinstance(rows_list, dict):
        rows_list = rows_list.get("data", [])
    if not rows_list:
        return None

    first_result = rows_list[0]
    columns = first_result.get("columns", [])
    rows = first_result.get("rows", [])
    if not columns or not rows:
        return None

    # 按列名查找值
    col_names = [c.get("name", "") for c in columns]
    row = rows[0]

    result = {}
    for idx, name in enumerate(col_names):
        if idx >= len(row):
            continue
        val = row[idx]
        if val is None:
            continue
        try:
            val = float(val)
        except (ValueError, TypeError):
            continue

        # 匹配 PE TTM（多种列名格式）
        if ("市盈率" in name or "PE" in name.upper()) and "TTM" in name.upper():
            if "pe" not in result:
                result["pe"] = val
        # 匹配 PB LF
        elif ("市净率" in name or "PB" in name.upper()) and ("LF" in name.upper() or "最新" in name):
            if "pb" not in result:
                result["pb"] = val
        # 匹配 股息率
        elif "股息率" in name and "TTM" in name.upper():
            if "dy" not in result:
                result["dy"] = val

    # 如果没找到 TTM 版本，用"最新"版本兜底
    for idx, name in enumerate(col_names):
        if idx >= len(row):
            continue
        val = row[idx]
        if val is None:
            continue
        try:
            val = float(val)
        except (ValueError, TypeError):
            continue
        if "pe" not in result and "最新" in name and "市盈率" in name:
            result["pe"] = val
        if "pb" not in result and "最新" in name and "市净率" in name:
            result["pb"] = val
        if "dy" not in result and "最新" in name and "股息率" in name:
            result["dy"] = val

    if not result:
        return None
    return result


# ── database ─────────────────────────────────────────────────────────

def get_target_series(conn, series_filter=None):
    """查询有 Wind 映射且需要更新的序列。"""
    today = date.today().isoformat()
    if series_filter:
        cur = conn.execute(
            """SELECT s.series_id, s.display_name, s.unit, MAX(o.date) as last_date
               FROM series s
               LEFT JOIN observations o ON o.series_id = s.series_id
               WHERE s.active = 1 AND s.series_id = ?
               GROUP BY s.series_id""",
            (series_filter,)
        )
    else:
        cur = conn.execute(
            """SELECT s.series_id, s.display_name, s.unit, MAX(o.date) as last_date
               FROM series s
               LEFT JOIN observations o ON o.series_id = s.series_id
               WHERE s.active = 1
               GROUP BY s.series_id
               HAVING last_date < ? OR last_date IS NULL""",
            (today,)
        )
    return cur.fetchall()


# ── validation (Wind-specific logic, kept here due to forward-fill handling) ─

def validate_series(conn, series_id, fetched_points, validation_config, category=None):
    """比较拉取值与数据库 validation_dates 的值。"""
    vdates = get_validation_dates(conn, series_id)
    if not vdates:
        return "ok", "no existing data to validate"

    # 过滤零值和前向填补值（DB 中连续相同值视为填补）
    vdates_filtered = []
    for vd, vv in vdates:
        if float(vv) == 0.0:
            continue
        vdates_filtered.append((vd, vv))

    # 如果过滤后只剩一条但两条原始值相同，可能是前向填补
    if len(vdates) == 2 and len(vdates_filtered) <= 1:
        pass  # 不额外处理，交给后续逻辑

    vdates = vdates_filtered

    fetched_dict = {}
    for p in fetched_points:
        fetched_dict[p[0]] = p[1]

    # 将不在 fetched 数据中的日期标记跳过（可能是 DB 前向填补）
    overlapping = [(vd, vv) for vd, vv in vdates if vd in fetched_dict]
    skipped_dates = [vd for vd, vv in vdates if vd not in fetched_dict]

    if not overlapping:
        if skipped_dates:
            return "ok", f"no overlapping dates to validate (DB dates {skipped_dates} not in Wind data, likely pre-filled)"
        return "ok", "no existing data to validate"

    matches = 0
    mismatches = []
    for vd, vv in overlapping:
        fv = fetched_dict[vd]
        if values_match(vv, fv, validation_config, category):
            matches += 1
        else:
            mismatches.append(f"{vd}: DB={vv} vs Wind={fv}")

    if skipped_dates:
        mismatch_note = f"; skipped pre-filled: {skipped_dates}" if not mismatches else f"; skipped: {skipped_dates}"
    else:
        mismatch_note = ""

    if matches == len(overlapping):
        return "ok", f"all {matches} validation dates match" + mismatch_note
    elif matches > 0:
        return "partial", f"{matches}/{len(overlapping)} match; mismatches: {'; '.join(mismatches)}" + mismatch_note
    else:
        return "fail", f"all validation dates mismatch: {'; '.join(mismatches)}" + mismatch_note


# ── main fetch logic ──────────────────────────────────────────────────

def fetch_and_update(db_path, mapping_path, dry_run=False, verbose=False,
                     series_filter=None, max_series=None):
    # ---- load configs ----
    mapping_cfg = load_json(mapping_path)
    fetch_cfg = mapping_cfg["fetch"]
    validation_cfg = mapping_cfg["validation"]
    mappings = mapping_cfg["mappings"]

    today = date.today()
    begin_date = today - timedelta(days=fetch_cfg["lookback_calendar_days"])

    with open_db(db_path) as conn:

        # ---- identify targets ----
        target_rows = get_target_series(conn, series_filter)
        if not target_rows:
            log("All Wind-mapped series are up to date. Nothing to fetch.", "OK")
            return {"series_fetched": 0, "obs_inserted": 0, "failures": []}

        # Build fetch list with mapping lookup
        fetch_list = []
        skipped_no_mapping = []
        for sid, name, unit, last_date in target_rows:
            m = mappings.get(sid)
            if not m:
                skipped_no_mapping.append(sid)
                continue
            fetch_list.append({
                "series_id": sid,
                "display_name": name,
                "unit": unit,
                "last_date": last_date,
                "method": m["method"],
                "windcode": m.get("windcode"),
                "metricIdsStr": m.get("metricIdsStr"),
                "indicator_filter": m.get("indicator_filter"),
                "category": m.get("category", ""),
                "skip_validation": m.get("skip_validation", False),
                "notes": m.get("notes", "")
            })

        if skipped_no_mapping and verbose:
            log(f"Skipped {len(skipped_no_mapping)} series without Wind mapping (handled by THS EDB)", "INFO")

        if max_series:
            fetch_list = fetch_list[:max_series]

        kline_count = sum(1 for f in fetch_list if f["method"] == "kline")
        econ_count = sum(1 for f in fetch_list if f["method"] == "economic")
        log(f"Fetching {len(fetch_list)} series from Wind ({kline_count} K-line + {econ_count} economic)...")

        # ---- fetch loop ----
        results = {}
        fetch_errors = []

        for i, item in enumerate(fetch_list):
            sid = item["series_id"]

            if verbose:
                if item["method"] == "kline":
                    log(f"[{i+1}/{len(fetch_list)}] {sid} ← K-line {item['windcode']}")
                else:
                    log(f"[{i+1}/{len(fetch_list)}] {sid} ← economic '{item['metricIdsStr']}'")

            # retry loop
            data = None
            for attempt in range(fetch_cfg["max_retries"] + 1):
                if item["method"] == "kline":
                    data = fetch_kline(item["windcode"], begin_date, today)
                else:
                    data = fetch_economic(
                        item["metricIdsStr"], item["indicator_filter"],
                        begin_date, today
                    )
                if data is not None:
                    break
                if attempt < fetch_cfg["max_retries"]:
                    time.sleep(3)

            if data is None:
                fetch_errors.append({"series_id": sid, "reason": "fetch_failed"})
                log(f"{sid}: fetch failed", "ERROR")
            elif not data:
                fetch_errors.append({"series_id": sid, "reason": "empty_data"})
                if verbose:
                    log(f"{sid}: no data returned", "WARN")
            else:
                results[sid] = data
                if verbose:
                    log(f"{sid}: got {len(data)} points, "
                        f"latest={data[-1][0]}={data[-1][1]}", "OK")

            # delay between calls (Wind credits cost, be conservative)
            if i < len(fetch_list) - 1:
                time.sleep(fetch_cfg["delay_between_calls_seconds"])

        log(f"Fetched: {len(results)} success, {len(fetch_errors)} errors")

        # ---- validate ----
        validated = []
        partial = []
        failed = []

        for item in fetch_list:
            sid = item["series_id"]
            data = results.get(sid)
            if data is None:
                continue

            category = item.get("category", "")
            skip_val = item.get("skip_validation", False)
            status, msg = validate_series(conn, sid, data, validation_cfg, category)
            # skip_validation: Wind is the authoritative primary source for this series
            # (migrated from EDB). Still run validation + log mismatches for audit, but
            # do not block the update — Wind data overrides a potentially stale DB seed.
            if verbose or status == "fail" or (skip_val and status != "ok"):
                tag = " (skipped, Wind-authoritative)" if skip_val and status != "ok" else ""
                log(f"{sid}: validate={status}{tag} — {msg}",
                    "ERROR" if (status == "fail" and not skip_val) else ("WARN" if status != "ok" else "OK"))

            if status == "ok" or skip_val:
                validated.append(item)
            elif status == "partial":
                partial.append(item)
            else:
                failed.append({"series_id": sid, "reason": f"validation_failed: {msg}"})

        log(f"Validated: {len(validated)} ok, {len(partial)} partial, {len(failed)} failed")

        # ---- insert ----
        imported_at = datetime.now().isoformat(timespec="seconds")
        obs_inserted = 0
        series_updated = 0

        for item in validated + partial:
            sid = item["series_id"]
            data = results.get(sid)
            if not data:
                continue

            last_date = item["last_date"]
            new_points = [p for p in data if not last_date or p[0] > last_date]

            if not new_points:
                continue

            if verbose:
                log(f"{sid}: inserting {len(new_points)} new observations "
                    f"({new_points[0][0]} → {new_points[-1][0]})")

            if not dry_run:
                consecutive_errors = 0
                MAX_CONSECUTIVE_ERRORS = 5

                for date_str, value in new_points:
                    try:
                        conn.execute(
                            """INSERT INTO observations (series_id, date, value, as_of_date, imported_at)
                               VALUES (?, ?, ?, ?, ?)
                               ON CONFLICT(series_id, date) DO UPDATE SET
                                   value=excluded.value,
                                   as_of_date=excluded.as_of_date,
                                   imported_at=excluded.imported_at""",
                            (sid, date_str, float(value), date_str, imported_at)
                        )
                        consecutive_errors = 0
                    except Exception as e:
                        consecutive_errors += 1
                        log(f"{sid} insert error at {date_str}: {e}", "ERROR")
                        if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                            log(f"{sid}: {MAX_CONSECUTIVE_ERRORS} consecutive insert errors, skipping remaining", "ERROR")
                            break
                        continue
                    obs_inserted += 1

                conn.execute(
                    "UPDATE series SET update_method = 'wind_mcp', updated_at = ? WHERE series_id = ?",
                    (imported_at, sid)
                )
                series_updated += 1

        if not dry_run and obs_inserted > 0:
            conn.commit()
            log(f"Committed {obs_inserted} new observations for {series_updated} series", "OK")
        elif dry_run:
            log(f"[DRY RUN] Would insert {obs_inserted} observations for {series_updated} series", "WARN")

        # ---- valuation (separate phase: one Wind call → three series) ----
        valuation_cfg = mapping_cfg.get("valuation", {}).get("indices", {})
        val_fetched = 0
        val_ok = 0
        val_partial = 0
        val_failed = 0
        val_inserted = 0
        val_errors = []

        if valuation_cfg and not series_filter:
            today_str = today.isoformat()
            log(f"Fetching {len(valuation_cfg)} valuation indices from Wind...")
            val_items = list(valuation_cfg.items())
            if max_series:
                val_items = val_items[:max_series]

            for i, (index_name, vcfg) in enumerate(val_items):
                wind_query = vcfg["wind_query"]
                pe_sid = vcfg["pe_series"]
                pb_sid = vcfg["pb_series"]
                dy_sid = vcfg["dy_series"]

                if verbose:
                    log(f"[Val {i+1}/{len(val_items)}] {index_name} ← '{wind_query}'")

                # retry loop
                val_data = None
                for attempt in range(fetch_cfg["max_retries"] + 1):
                    val_data = fetch_valuation(wind_query)
                    if val_data is not None:
                        break
                    if attempt < fetch_cfg["max_retries"]:
                        time.sleep(3)

                if val_data is None:
                    val_errors.append({"index": index_name, "reason": "fetch_failed"})
                    log(f"{index_name}: valuation fetch failed", "ERROR")
                    if i < len(val_items) - 1:
                        time.sleep(fetch_cfg["delay_between_calls_seconds"])
                    continue

                val_fetched += 1
                if verbose:
                    pe_str = f"PE={val_data.get('pe', '?')}" if 'pe' in val_data else ""
                    pb_str = f"PB={val_data.get('pb', '?')}" if 'pb' in val_data else ""
                    dy_str = f"DY={val_data.get('dy', '?')}%" if 'dy' in val_data else ""
                    log(f"{index_name}: {pe_str} {pb_str} {dy_str}", "OK")

                # Validate and insert each metric
                for metric_key, sid in [("pe", pe_sid), ("pb", pb_sid), ("dy", dy_sid)]:
                    wind_val = val_data.get(metric_key)
                    if wind_val is None:
                        continue

                    vdates = get_validation_dates(conn, sid)
                    # Filter zero values
                    vdates = [(vd, vv) for vd, vv in vdates if float(vv) != 0.0]

                    if vdates:
                        db_date, db_val = vdates[-1]  # most recent
                        if values_match(db_val, wind_val, validation_cfg):
                            val_ok += 1
                            if verbose:
                                log(f"  {sid}: DB={db_val} vs Wind={wind_val} ✓", "OK")
                        else:
                            val_partial += 1
                            if verbose:
                                rel = abs(wind_val - float(db_val)) / abs(float(db_val)) * 100 if abs(float(db_val)) > 0.001 else 0
                                log(f"  {sid}: DB={db_val} vs Wind={wind_val} ({rel:.2f}%) ⚠", "WARN")
                    else:
                        val_ok += 1  # no DB data yet, skip validation

                    # Insert new observation if today's date not in DB
                    existing = conn.execute(
                        "SELECT COUNT(*) FROM observations WHERE series_id = ? AND date = ?",
                        (sid, today_str)
                    ).fetchone()[0]

                    if existing == 0:
                        if not dry_run:
                            try:
                                conn.execute(
                                    """INSERT INTO observations (series_id, date, value, as_of_date, imported_at)
                                       VALUES (?, ?, ?, ?, ?)
                                       ON CONFLICT(series_id, date) DO UPDATE SET
                                           value=excluded.value, as_of_date=excluded.as_of_date,
                                           imported_at=excluded.imported_at""",
                                    (sid, today_str, float(wind_val), today_str, imported_at)
                                )
                                conn.execute(
                                    "UPDATE series SET update_method = 'wind_mcp', updated_at = ? WHERE series_id = ?",
                                    (imported_at, sid)
                                )
                            except Exception as e:
                                log(f"{sid} insert error: {e}", "ERROR")
                                continue
                        val_inserted += 1
                        series_updated += 1
                        if verbose:
                            log(f"  {sid}: new obs {today_str}={wind_val}", "OK")

                if i < len(val_items) - 1:
                    time.sleep(fetch_cfg["delay_between_calls_seconds"])

            if not dry_run and val_inserted > 0:
                conn.commit()
            val_metric_failures = val_fetched * 3 - val_ok - val_partial  # metrics without data
            log(f"Valuation: {val_fetched}/{len(val_items)} indices fetched, "
                f"{val_ok} ok + {val_partial} partial (metrics), "
                f"{val_inserted} obs inserted")

        # ---- summary ----
        all_errors = fetch_errors + failed + val_errors
        trend_targeted = len(fetch_list)
        val_targeted = len(valuation_cfg) * 3 if valuation_cfg else 0  # 3 series per index
        summary = {
            "timestamp": imported_at,
            "dry_run": dry_run,
            "trend_targeted": trend_targeted,
            "trend_fetched": len(results),
            "trend_ok": len(validated),
            "trend_partial": len(partial),
            "trend_failed": len(failed),
            "val_targeted": val_targeted,
            "val_fetched": val_fetched,
            "val_ok": val_ok,
            "val_partial": val_partial,
            "val_failures": len(val_errors),
            "obs_inserted": obs_inserted + val_inserted,
            "wind_api_calls": WIND_CALL_COUNT,
            "failures": all_errors
        }

        summary_path = ROOT / "data" / "wind_fetch_summary.json"
        if not dry_run:
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

        return summary


# ── CLI ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fetch data from Wind MCP")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to SQLite database")
    parser.add_argument("--mapping", default=str(DEFAULT_MAPPING), help="Path to Wind mapping config")
    parser.add_argument("--dry-run", action="store_true", help="Fetch but do not write to database")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--series", help="Fetch only a single series_id (for testing)")
    parser.add_argument("--max-series", type=int, default=None, help="Limit to first N series (for testing)")
    args = parser.parse_args()

    if not Path(args.db).exists():
        log(f"Database not found: {args.db}", "ERROR")
        sys.exit(1)
    if not Path(args.mapping).exists():
        log(f"Mapping config not found: {args.mapping}", "ERROR")
        sys.exit(1)

    log("Martin Morning Brief — fetch_wind.py (Wind MCP)")
    log(f"DB: {args.db}")
    if args.dry_run:
        log("Mode: DRY RUN (no writes)", "WARN")

    try:
        summary = fetch_and_update(
            args.db, args.mapping,
            dry_run=args.dry_run,
            verbose=args.verbose,
            series_filter=args.series,
            max_series=args.max_series
        )
    except Exception as e:
        log(f"Fatal error: {e}", "ERROR")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(2)

    print()
    log("=== Wind Fetch Summary ===")
    log(f"Trend:   {summary['trend_targeted']} targeted, {summary['trend_fetched']} fetched, "
        f"{summary['trend_ok']} ok + {summary['trend_partial']} partial, {summary['trend_failed']} failed")
    if summary['val_targeted'] > 0:
        log(f"Valuation: {summary['val_targeted']} series ({summary['val_targeted']//3} indices), "
            f"{summary['val_fetched']} fetched, "
            f"{summary['val_ok']} ok + {summary['val_partial']} partial, {summary['val_failures']} failures")
    log(f"New obs:  {summary['obs_inserted']} observations")
    log(f"Wind API calls: {summary['wind_api_calls']}")

    if summary["failures"]:
        log("Failures:", "WARN")
        for f in summary["failures"]:
            log(f"  {f.get('series_id', f.get('index', '?'))}: {f['reason']}", "WARN")

    total_failed = summary['trend_failed'] + summary['val_failures']
    if total_failed > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
