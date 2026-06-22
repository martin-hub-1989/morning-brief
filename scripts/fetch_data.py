#!/usr/bin/env python3
"""
从同花顺 EDB MCP API 拉取最新日频数据，验证后写入 SQLite 数据库。

用法:
  python3 scripts/fetch_data.py                          # 拉取所有需要更新的日频序列
  python3 scripts/fetch_data.py --dry-run                # 干跑，不写库
  python3 scripts/fetch_data.py --series trend:USDCNY    # 仅拉取单个序列
  python3 scripts/fetch_data.py --verbose                # 详细输出
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, date, timedelta
from pathlib import Path

from lib import (
    ROOT, DEFAULT_DB, MCP_CONFIG, log, load_json, open_db,
    get_validation_dates, values_match,
)

DEFAULT_MAPPING = ROOT / "config" / "edb_mapping.json"
WIND_MAPPING_PATH = ROOT / "config" / "wind_mapping.json"

# ── Wind CLI (for EDB → Wind fallback) ─────────────────────────────────

_WIND_SKILL_DIR = Path(os.environ.get(
    "WIND_SKILL_DIR",
    str(Path.home() / ".claude" / "skills" / "wind-mcp-skill")
))

# Global counter for Wind API calls (shared across modules)
WIND_CALL_COUNT = 0


def _call_wind_cli(server_type, tool_name, params, timeout=30):
    """Call Wind MCP CLI. Returns parsed data or None."""
    global WIND_CALL_COUNT
    params_json = json.dumps(params, ensure_ascii=False)
    cmd = ["node", "scripts/cli.mjs", "call", server_type, tool_name, params_json]
    WIND_CALL_COUNT += 1

    try:
        result = subprocess.run(
            cmd, cwd=str(_WIND_SKILL_DIR),
            capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        log(f"Wind CLI timeout after {timeout}s", "WARN")
        return None
    except Exception as e:
        log(f"Wind CLI error: {e}", "WARN")
        return None

    if result.returncode != 0:
        return None

    try:
        outer = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None

    if outer.get("isError"):
        return None

    content = outer.get("content", [])
    if not content:
        return None

    try:
        inner = json.loads(content[0]["text"])
    except (json.JSONDecodeError, KeyError, IndexError):
        return None

    if inner.get("error"):
        return None

    return inner.get("data")


def _fetch_wind_kline(windcode, begin_date, end_date):
    """Fetch daily kline from Wind, return [[date_str, close], ...] sorted ascending."""
    params = {
        "windcode": windcode,
        "begin_date": begin_date.strftime("%Y%m%d"),
        "end_date": end_date.strftime("%Y%m%d")
    }
    data = _call_wind_cli("index_data", "get_index_kline", params)
    if not data:
        return None
    rows = data.get("rows", [])
    if not rows:
        return None
    points = []
    for row in rows:
        if len(row) < 3:
            continue
        raw_date = row[-1][:8]  # _DATE column: yyyyMMdd
        try:
            date_str = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
        except IndexError:
            continue
        try:
            close_val = float(row[2])  # MATCH column
        except (ValueError, TypeError):
            continue
        points.append([date_str, close_val])
    points.sort(key=lambda x: x[0])
    return points


def _fetch_wind_economic(metric_ids_str, indicator_filter, begin_date, end_date):
    """Fetch economic data from Wind, return [[date_str, value], ...] sorted ascending."""
    params = {
        "metricIdsStr": metric_ids_str,
        "freq": "日",
        "beginDate": begin_date.strftime("%Y%m%d"),
        "endDate": end_date.strftime("%Y%m%d")
    }
    data = _call_wind_cli("economic_data", "get_economic_data", params)
    if not data:
        return None

    dates = data.get("date", [])
    indicators = data.get("indicatorInfo", [])
    if not dates or not indicators:
        return None

    # Match by indicator_filter
    target = None
    for ind in indicators:
        name = ind.get("name", "")
        if indicator_filter.lower() in name.lower():
            target = ind
            break
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


def _try_wind_fallback(series_id, wind_mappings, begin_date, end_date):
    """
    Attempt to fetch a series from Wind MCP as fallback when EDB fails.
    Returns dict with 'points' key (same format as EDB data) or None.
    """
    wm = wind_mappings.get(series_id)
    if not wm:
        return None

    method = wm.get("method", "")
    log(f"{series_id}: EDB failed, trying Wind fallback ({method})...", "WARN")

    try:
        if method == "kline":
            points = _fetch_wind_kline(wm["windcode"], begin_date, end_date)
        elif method == "economic":
            points = _fetch_wind_economic(
                wm["metricIdsStr"], wm["indicator_filter"], begin_date, end_date
            )
        else:
            log(f"{series_id}: unknown Wind method '{method}'", "WARN")
            return None

        if points:
            log(f"{series_id}: Wind fallback OK — {len(points)} points, "
                f"latest={points[-1][0]}={points[-1][1]}", "OK")
            return {
                "points": points,
                "source": "wind_fallback"
            }
        else:
            log(f"{series_id}: Wind fallback returned no data", "WARN")
            return None
    except Exception as e:
        log(f"{series_id}: Wind fallback error: {e}", "ERROR")
        return None


# ── EDB API ──────────────────────────────────────────────────────────

def extract_jwe_token():
    """从 ~/.claude/mcp.json 提取同花顺 EDB 的 JWE Bearer Token。"""
    if not MCP_CONFIG.exists():
        raise FileNotFoundError(f"MCP config not found: {MCP_CONFIG}")
    cfg = load_json(MCP_CONFIG)
    servers = cfg.get("mcpServers", {})
    edb = servers.get("hexin-ifind-ds-edb-mcp")
    if not edb:
        raise KeyError("hexin-ifind-ds-edb-mcp not found in mcp.json")
    token = edb.get("headers", {}).get("Authorization")
    if not token:
        raise KeyError("Authorization header not found in hexin-ifind-ds-edb-mcp config")
    return token


def call_edb(token, query, base_url, timeout=30):
    """
    调用同花顺 EDB MCP API。返回解析后的 data 列表：
    [[date_str, value], ...] 或 None（失败时）。
    """
    body = {
        "jsonrpc": "2.0",
        "id": hash(query) % 10000,
        "method": "tools/call",
        "params": {
            "name": "get_edb_data",
            "arguments": {"query": query}
        }
    }
    req = urllib.request.Request(
        base_url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.URLError as e:
        log(f"Network error: {e}", "ERROR")
        return None
    except Exception as e:
        log(f"HTTP error: {e}", "ERROR")
        return None

    try:
        outer = json.loads(raw)
    except json.JSONDecodeError:
        log("Invalid JSON response", "ERROR")
        return None

    # 外层 JSON-RPC
    result = outer.get("result")
    if not result:
        log(f"EDB error: {outer.get('error', 'unknown')}", "ERROR")
        return None

    content = result.get("content", [])
    if not content:
        log("EDB returned empty content", "WARN")
        return None

    # 内层 JSON（text 字段是 JSON 字符串）
    try:
        inner = json.loads(content[0]["text"])
    except (json.JSONDecodeError, KeyError, IndexError):
        log("Failed to parse inner EDB response", "ERROR")
        return None

    if inner.get("code") != 1:
        log(f"EDB business error: {inner.get('msg', 'unknown')}", "ERROR")
        return None

    datas = inner.get("data", {}).get("datas", [])
    if not datas:
        return None  # 无数据不报错，静默跳过

    # 提取 [[date, value], ...]
    first = datas[0]
    edb_data = first.get("data", {})
    points = edb_data.get("data", [])
    if not points:
        return None

    # 也提取 index_id 和 indicator_name 用于缓存
    columns = edb_data.get("columns", [])
    attrs = edb_data.get("attrs", {})
    index_id = None
    indicator_name = None
    for col_name, attr in attrs.items():
        index_id = attr.get("index_id")
        indicator_name = col_name
        break

    # 按日期升序排列（EDB 返回降序）
    sorted_points = sorted(
        [[str(p[0]), float(p[1])] for p in points if len(p) >= 2 and p[1] is not None],
        key=lambda x: x[0]
    )
    return {
        "points": sorted_points,
        "index_id": index_id,
        "indicator_name": indicator_name,
        "columns": columns
    }


# ── database ─────────────────────────────────────────────────────────

def get_target_series(conn, series_filter=None):
    """
    查询需要更新的序列（日频 + 月频中有 EDB 映射的）。
    返回 [(series_id, display_name, unit, last_date, source_code), ...]
    """
    today = date.today().isoformat()
    if series_filter:
        cur = conn.execute(
            """SELECT s.series_id, s.display_name, s.unit, MAX(o.date) as last_date, s.source_code
               FROM series s
               LEFT JOIN observations o ON o.series_id = s.series_id
               WHERE s.active = 1 AND s.series_id = ?
               GROUP BY s.series_id""",
            (series_filter,)
        )
    else:
        cur = conn.execute(
            """SELECT s.series_id, s.display_name, s.unit, MAX(o.date) as last_date, s.source_code
               FROM series s
               LEFT JOIN observations o ON o.series_id = s.series_id
               WHERE s.active = 1
               GROUP BY s.series_id
               HAVING last_date < ? OR last_date IS NULL""",
            (today,)
        )
    return cur.fetchall()


# ── validation ────────────────────────────────────────────────────────

def validate_series(conn, series_id, fetched_points, validation_config):
    """
    比较拉取值与数据库 validation_dates 的值。
    返回 (status, message):
      "ok"       — 全部匹配或无需验证
      "partial"  — 至少一个验证日期匹配
      "fail"     — 所有验证日期都不匹配
    """
    vdates = get_validation_dates(conn, series_id)
    if not vdates:
        return "ok", "no existing data to validate"

    # 过滤掉 DB 中值为 0 的验证日期（已知占位符）
    vdates = [(vd, vv) for vd, vv in vdates if float(vv) != 0.0]

    # 建立 fetched dict
    fetched_dict = {}
    for p in fetched_points:
        fetched_dict[p[0]] = p[1]

    matches = 0
    mismatches = []
    for vd, vv in vdates:
        fv = fetched_dict.get(vd)
        if fv is None:
            mismatches.append(f"{vd}: not in fetched data")
            continue
        if values_match(vv, fv, validation_config):
            matches += 1
        else:
            mismatches.append(f"{vd}: DB={vv} vs fetched={fv}")

    if matches == len(vdates):
        return "ok", f"all {matches} validation dates match"
    elif matches > 0:
        return "partial", f"{matches}/{len(vdates)} match; mismatches: {'; '.join(mismatches)}"
    else:
        return "fail", f"all validation dates mismatch: {'; '.join(mismatches)}"


# ── main fetch logic ──────────────────────────────────────────────────

def fetch_and_update(db_path, mapping_path, dry_run=False, verbose=False,
                     series_filter=None, max_series=None):
    # ---- load configs ----
    mapping_cfg = load_json(mapping_path)
    fetch_cfg = mapping_cfg["fetch"]
    validation_cfg = mapping_cfg["validation"]
    mappings = mapping_cfg["mappings"]
    token = extract_jwe_token()
    base_url = fetch_cfg["base_url"]

    # ---- load Wind fallback mappings ----
    wind_mappings = {}
    if WIND_MAPPING_PATH.exists():
        wind_cfg = load_json(WIND_MAPPING_PATH)
        wind_mappings = wind_cfg.get("mappings", {})
    fallback_begin_date = date.today() - timedelta(days=90)  # 90-day lookback for fallback

    with open_db(db_path) as conn:

        # ---- identify targets ----
        target_rows = get_target_series(conn, series_filter)
        if not target_rows:
            log("All series are up to date. Nothing to fetch.", "OK")
            return {"series_fetched": 0, "obs_inserted": 0, "failures": []}

        log(f"Found {len(target_rows)} series needing update")

        # build fetch list with mapping lookup
        fetch_list = []
        skipped_no_mapping = []
        skipped_by_reason = []
        for sid, name, unit, last_date, source_code in target_rows:
            m = mappings.get(sid)
            if not m:
                skipped_no_mapping.append(sid)
                continue
            if m.get("skip_reason"):
                skipped_by_reason.append((sid, m["skip_reason"]))
                continue
            fetch_list.append({
                "series_id": sid,
                "display_name": name,
                "unit": unit,
                "last_date": last_date,
                "edb_query": m["edb_query"],
                "category": m.get("category", ""),
                "notes": m.get("notes", ""),
                "skip_validation": m.get("skip_validation", False)
            })

        if skipped_no_mapping:
            log(f"Skipped {len(skipped_no_mapping)} series without EDB mapping: {skipped_no_mapping}", "WARN")
        if skipped_by_reason:
            for sid, reason in skipped_by_reason:
                log(f"Skipped {sid}: {reason}", "WARN")

        if max_series:
            fetch_list = fetch_list[:max_series]
            log(f"Limited to first {max_series} series for testing")

        log(f"Fetching {len(fetch_list)} series from EDB...")

        # ---- fetch loop ----
        results = {}  # series_id -> fetched data dict
        fetch_errors = []

        for i, item in enumerate(fetch_list):
            sid = item["series_id"]
            query = item["edb_query"]

            # 动态拼接日期窗口，确保返回足够历史数据用于验证
            full_query = f"{query} 最近60个交易日"

            if verbose:
                log(f"[{i+1}/{len(fetch_list)}] {sid} ← '{full_query}'")

            # retry loop
            data = None
            for attempt in range(fetch_cfg["max_retries"] + 1):
                data = call_edb(token, full_query, base_url, fetch_cfg["request_timeout_seconds"])
                if data is not None:
                    break
                if attempt < fetch_cfg["max_retries"]:
                    backoff = fetch_cfg["retry_backoff_seconds"][attempt]
                    log(f"Retry {attempt+1}/{fetch_cfg['max_retries']} for {sid} in {backoff}s...", "WARN")
                    time.sleep(backoff)

            if data is None:
                # Try Wind MCP fallback
                wind_data = _try_wind_fallback(
                    sid, wind_mappings, fallback_begin_date, date.today()
                )
                if wind_data:
                    results[sid] = wind_data
                else:
                    fetch_errors.append({"series_id": sid, "reason": "fetch_failed"})
                    log(f"{sid}: fetch failed after retries (EDB + Wind)", "ERROR")
            elif not data["points"]:
                # Try Wind MCP fallback for empty EDB data
                wind_data = _try_wind_fallback(
                    sid, wind_mappings, fallback_begin_date, date.today()
                )
                if wind_data:
                    results[sid] = wind_data
                else:
                    fetch_errors.append({"series_id": sid, "reason": "empty_data"})
                    if verbose:
                        log(f"{sid}: no data returned (EDB + Wind)", "WARN")
            else:
                results[sid] = data
                if verbose:
                    log(f"{sid}: got {len(data['points'])} points, "
                        f"latest={data['points'][-1][0]}={data['points'][-1][1]}", "OK")
                # cache index_id if available
                if data.get("index_id") and verbose:
                    log(f"  index_id={data['index_id']} indicator={data.get('indicator_name', '')}")

            # small delay between calls
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
                continue  # already logged as fetch error

            if item.get("skip_validation"):
                validated.append(item)
                if verbose:
                    log(f"{sid}: validate=skipped (skip_validation set)", "OK")
                continue

            status, msg = validate_series(conn, sid, data["points"], validation_cfg)
            if verbose or status == "fail":
                log(f"{sid}: validate={status} — {msg}",
                    "ERROR" if status == "fail" else ("WARN" if status == "partial" else "OK"))

            if status == "ok":
                validated.append(item)
            elif status == "partial":
                partial.append(item)
            elif status == "fail":
                # Try Wind fallback on validation failure
                if data.get("source") != "wind_fallback":  # don't double-fallback
                    wind_data = _try_wind_fallback(
                        sid, wind_mappings, fallback_begin_date, date.today()
                    )
                    if wind_data:
                        # Validate Wind data too
                        w_status, w_msg = validate_series(
                            conn, sid, wind_data["points"], validation_cfg
                        )
                        log(f"{sid}: Wind fallback validate={w_status} — {w_msg}",
                            "WARN" if w_status != "ok" else "OK")
                        if w_status == "ok":
                            results[sid] = wind_data
                            validated.append(item)
                            continue
                        elif w_status == "partial":
                            results[sid] = wind_data
                            partial.append(item)
                            continue
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
            new_points = [p for p in data["points"] if not last_date or p[0] > last_date]

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

                # 更新 series 表的 update_method
                method = 'wind_mcp_fallback' if data.get("source") == "wind_fallback" else 'edb_mcp'
                conn.execute(
                    "UPDATE series SET update_method = ?, updated_at = ? WHERE series_id = ?",
                    (method, imported_at, sid)
                )
                series_updated += 1

        if not dry_run and obs_inserted > 0:
            conn.commit()
            log(f"Committed {obs_inserted} new observations for {series_updated} series", "OK")
        elif dry_run:
            log(f"[DRY RUN] Would insert {obs_inserted} observations for {series_updated} series", "WARN")

        # ---- summary ----
        all_errors = fetch_errors + failed
        wind_fallback_count = sum(
            1 for v in results.values() if v.get("source") == "wind_fallback"
        )
        summary = {
            "timestamp": imported_at,
            "dry_run": dry_run,
            "series_targeted": len(target_rows),
            "series_fetched": len(results),
            "series_validated": len(validated),
            "series_partial": len(partial),
            "series_failed": len(all_errors),
            "obs_inserted": obs_inserted,
            "wind_fallback_used": wind_fallback_count,
            "wind_api_calls": WIND_CALL_COUNT,
            "failures": all_errors
        }

        # write summary JSON
        summary_path = ROOT / "data" / "fetch_summary.json"
        if not dry_run:
            summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

        return summary


# ── CLI ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fetch daily data from 同花顺 EDB API")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to SQLite database")
    parser.add_argument("--mapping", default=str(DEFAULT_MAPPING), help="Path to EDB mapping config")
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

    log("Martin Morning Brief — fetch_data.py")
    log(f"DB: {args.db}")
    log(f"Mapping: {args.mapping}")
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

    # final report
    print()
    log("=== Fetch Summary ===")
    log(f"Targeted: {summary['series_targeted']} series")
    log(f"Fetched:  {summary['series_fetched']} series")
    log(f"Passed:   {summary['series_validated']} ok + {summary['series_partial']} partial")
    log(f"Failed:   {summary['series_failed']} series")
    log(f"New obs:  {summary['obs_inserted']} observations")
    if summary.get("wind_fallback_used", 0) > 0:
        log(f"Wind fallback: {summary['wind_fallback_used']} series (EDB→Wind auto-switch)", "OK")
    if summary.get("wind_api_calls", 0) > 0:
        log(f"Wind API calls: {summary['wind_api_calls']} (fallback)")

    if summary["failures"]:
        log("Failures:", "WARN")
        for f in summary["failures"]:
            log(f"  {f['series_id']}: {f['reason']}", "WARN")

    if summary["series_failed"] > 0:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
