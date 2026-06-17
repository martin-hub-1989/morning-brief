#!/usr/bin/env python3
"""
从华泰智研 MCP 拉取市场情绪和资金面数据，存入本地 SQLite。

用法:
  python3 scripts/fetch_emotion.py                    # 拉取所有数据
  python3 scripts/fetch_emotion.py --dry-run          # 干跑
  python3 scripts/fetch_emotion.py --verbose          # 详细输出
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

# Windows GBK 编码兼容：强制 stdout/stderr 使用 UTF-8
if sys.platform == 'win32':
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding='utf-8')
        except Exception:
            pass

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "morning_brief.sqlite"
MCP_CONFIG = Path.home() / ".claude" / "mcp.json"
HTSC_URL = "https://inst.htsc.com/mcp/v1/ris/htsc_research_mcp"


# ── helpers ──────────────────────────────────────────────────────────

def log(msg, level="INFO"):
    prefix = {"INFO": "  ", "WARN": "  ⚠", "ERROR": "  ✗", "OK": "  ✓"}
    print(f"{prefix.get(level, '  ')} {msg}",
          file=sys.stderr if level == "ERROR" else sys.stdout)


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── HTSC MCP HTTP ────────────────────────────────────────────────────

def extract_htsc_key():
    """从 ~/.claude/mcp.json 提取 HTSC APP KEY。"""
    if not MCP_CONFIG.exists():
        raise FileNotFoundError(f"MCP config not found: {MCP_CONFIG}")
    cfg = load_json(MCP_CONFIG)
    servers = cfg.get("mcpServers", {})
    htsc = servers.get("htsc_research_mcp")
    if not htsc:
        raise KeyError("htsc_research_mcp not found in mcp.json")
    key = htsc.get("headers", {}).get("HTSC_APP_KEY")
    if not key:
        raise KeyError("HTSC_APP_KEY not found in htsc_research_mcp config")
    return key


def call_htsc(tool_name, arguments, api_key, timeout=60):
    """
    调用华泰 MCP 工具，返回解析后的 data 字符串（Markdown）或 None。
    """
    body = {
        "jsonrpc": "2.0",
        "id": hash(tool_name) % 10000,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments
        }
    }
    req = urllib.request.Request(
        HTSC_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "HTSC_APP_KEY": api_key
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.URLError as e:
        log(f"HTSC network error: {e}", "ERROR")
        return None
    except Exception as e:
        log(f"HTSC HTTP error: {e}", "ERROR")
        return None

    # Parse SSE response: "event: message\ndata: {json}\n\n"
    for line in raw.split("\n"):
        if line.startswith("data: "):
            try:
                outer = json.loads(line[6:])
            except json.JSONDecodeError:
                continue
            if outer.get("error"):
                log(f"HTSC error: {outer['error']}", "ERROR")
                return None
            result = outer.get("result", {})
            content = result.get("content", [])
            if not content:
                return None
            try:
                inner = json.loads(content[0]["text"])
            except (json.JSONDecodeError, KeyError, IndexError):
                return None
            if inner.get("status") != "success":
                log(f"HTSC business error: {inner.get('message', 'unknown')}", "ERROR")
                return None
            return inner.get("data", "")
    return None


# ── Markdown table parsing ───────────────────────────────────────────

def parse_emotion_table(md_text):
    """解析情绪指数表格：{date_str: value}"""
    data = {}
    in_table = False
    for line in md_text.split("\n"):
        if "| 日期 | 数值 |" in line:
            in_table = True
            continue
        if in_table and line.startswith("|") and "---" not in line:
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) >= 2:
                try:
                    data[parts[0]] = float(parts[1])
                except ValueError:
                    continue
    return data


def parse_capital_flow_tables(md_text):
    """解析资金面多段表格：{section_name: [(date, value), ...]}"""
    sections = {}
    current_section = None
    for line in md_text.split("\n"):
        section_match = re.match(r"^##\s+(.+)", line)
        if section_match:
            current_section = section_match.group(1).strip()
            continue
        if current_section and re.match(r"\|\s*[\d\-]+\s*\|", line):
            parts = [p.strip() for p in line.split("|") if p.strip()]
            if len(parts) >= 2:
                try:
                    sections.setdefault(current_section, []).append(
                        (parts[0], float(parts[1]))
                    )
                except (ValueError, IndexError):
                    continue
    return sections


# ── database ─────────────────────────────────────────────────────────

SERIES_DEFS = {
    "htsc:A股情绪指数": {
        "display_name": "A股情绪指数", "sheet_name": "市场情绪",
        "frequency": "D", "unit": "percent", "source_name": "华泰智研",
    },
    "htsc:港股情绪指数": {
        "display_name": "港股情绪指数", "sheet_name": "市场情绪",
        "frequency": "D", "unit": "percent", "source_name": "华泰智研",
    },
    "htsc:散户资金净流入": {
        "display_name": "散户资金净流入", "sheet_name": "A股资金面",
        "frequency": "W", "unit": "price", "source_name": "华泰智研",
    },
    "htsc:ETF资金": {
        "display_name": "ETF资金", "sheet_name": "A股资金面",
        "frequency": "W", "unit": "price", "source_name": "华泰智研",
    },
    "htsc:融资资金": {
        "display_name": "融资资金", "sheet_name": "A股资金面",
        "frequency": "W", "unit": "price", "source_name": "华泰智研",
    },
    "htsc:公募基金": {
        "display_name": "公募基金", "sheet_name": "A股资金面",
        "frequency": "W", "unit": "price", "source_name": "华泰智研",
    },
    "htsc:产业资本减持": {
        "display_name": "产业资本减持", "sheet_name": "A股资金面",
        "frequency": "W", "unit": "price", "source_name": "华泰智研",
    },
    "htsc:一级市场": {
        "display_name": "一级市场", "sheet_name": "A股资金面",
        "frequency": "W", "unit": "price", "source_name": "华泰智研",
    },
}

# Map HTSC section names to series_ids
CAPITAL_FLOW_MAP = {
    "散户资金净流入（亿元）": "htsc:散户资金净流入",
    "产业资本减持（亿元）": "htsc:产业资本减持",
    "主动偏股公募基金(存量+新增规模)": "htsc:公募基金",
    "融资资金（亿元）": "htsc:融资资金",
    "ETF资金（亿元）": "htsc:ETF资金",
    "一级市场(亿元)": "htsc:一级市场",
}


def ensure_series(conn, imported_at):
    """确保 series 表中存在情绪/资金面序列。"""
    for sid, info in SERIES_DEFS.items():
        conn.execute(
            """INSERT OR IGNORE INTO series (
                   series_id, display_name, sheet_name, frequency, unit,
                   source_name, source_code, active, update_method, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'htsc_mcp', ?, ?)""",
            (sid, info["display_name"], info["sheet_name"], info["frequency"],
             info["unit"], info["source_name"], sid, imported_at, imported_at)
        )


def get_last_date(conn, series_id):
    """获取某序列数据库中最新日期。"""
    row = conn.execute(
        "SELECT MAX(date) FROM observations WHERE series_id = ?", (series_id,)
    ).fetchone()
    return row[0] if row and row[0] else None


# ── main fetch logic ──────────────────────────────────────────────────

def fetch_and_store(db_path, dry_run=False, verbose=False):
    api_key = extract_htsc_key()
    conn = sqlite3.connect(db_path)
    imported_at = datetime.now().isoformat(timespec="seconds")
    ensure_series(conn, imported_at)

    total_inserted = 0
    today = date.today()

    # ---- A股情绪指数 ----
    log("Fetching A股情绪指数...")
    a_last = get_last_date(conn, "htsc:A股情绪指数")
    a_start = a_last or "2020-01-01"
    md = call_htsc("get_market_emotion",
                   {"market": "A", "start_date": a_start, "end_date": today.isoformat()},
                   api_key)
    if md:
        data = parse_emotion_table(md)
        new_pts = {d: v for d, v in data.items() if not a_last or d > a_last}
        if verbose:
            log(f"A股情绪: {len(data)} pts total, {len(new_pts)} new")
        for d, v in sorted(new_pts.items()):
            if not dry_run:
                conn.execute(
                    """INSERT OR REPLACE INTO observations (series_id, date, value, as_of_date, imported_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    ("htsc:A股情绪指数", d, v, d, imported_at)
                )
            total_inserted += 1
    else:
        log("A股情绪指数 fetch failed", "ERROR")

    time.sleep(0.5)

    # ---- 港股情绪指数 ----
    log("Fetching 港股情绪指数...")
    hk_last = get_last_date(conn, "htsc:港股情绪指数")
    hk_start = hk_last or "2020-01-01"
    md = call_htsc("get_market_emotion",
                   {"market": "HK", "start_date": hk_start, "end_date": today.isoformat()},
                   api_key)
    if md:
        data = parse_emotion_table(md)
        new_pts = {d: v for d, v in data.items() if not hk_last or d > hk_last}
        if verbose:
            log(f"港股情绪: {len(data)} pts total, {len(new_pts)} new")
        for d, v in sorted(new_pts.items()):
            if not dry_run:
                conn.execute(
                    """INSERT OR REPLACE INTO observations (series_id, date, value, as_of_date, imported_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    ("htsc:港股情绪指数", d, v, d, imported_at)
                )
            total_inserted += 1
    else:
        log("港股情绪指数 fetch failed", "ERROR")

    time.sleep(0.5)

    # ---- A股资金面 ----
    log("Fetching A股资金面...")
    # Use the oldest last_date across all capital flow series
    cf_last_dates = []
    for sid in CAPITAL_FLOW_MAP.values():
        ld = get_last_date(conn, sid)
        if ld:
            cf_last_dates.append(ld)
    cf_start = min(cf_last_dates) if cf_last_dates else "2020-01-01"

    md = call_htsc("get_a_stock_capital_flow",
                   {"start_date": cf_start, "end_date": today.isoformat()},
                   api_key)
    if md:
        sections = parse_capital_flow_tables(md)
        for section_name, points in sections.items():
            sid = CAPITAL_FLOW_MAP.get(section_name)
            if not sid:
                if verbose:
                    log(f"Unknown capital flow section: {section_name}", "WARN")
                continue
            last = get_last_date(conn, sid)
            new_pts = [(d, v) for d, v in points if not last or d > last]
            if verbose:
                log(f"  {section_name}: {len(points)} pts, {len(new_pts)} new")
            for d, v in new_pts:
                if not dry_run:
                    conn.execute(
                        """INSERT OR REPLACE INTO observations (series_id, date, value, as_of_date, imported_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        (sid, d, v, d, imported_at)
                    )
                total_inserted += 1
    else:
        log("A股资金面 fetch failed", "ERROR")

    if not dry_run and total_inserted > 0:
        conn.commit()
        log(f"Committed {total_inserted} new observations", "OK")
    elif dry_run:
        log(f"[DRY RUN] Would insert {total_inserted} observations", "WARN")

    conn.close()
    return total_inserted


# ── CLI ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fetch market emotion data from 华泰智研 MCP")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to SQLite database")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if not Path(args.db).exists():
        log(f"Database not found: {args.db}", "ERROR")
        sys.exit(1)

    log("Martin Morning Brief — fetch_emotion.py (华泰智研 MCP)")
    if args.dry_run:
        log("Mode: DRY RUN (no writes)", "WARN")

    try:
        count = fetch_and_store(args.db, dry_run=args.dry_run, verbose=args.verbose)
    except Exception as e:
        log(f"Fatal error: {e}", "ERROR")
        if args.verbose:
            import traceback
            traceback.print_exc()
        sys.exit(2)

    log(f"=== Emotion Fetch Summary ===")
    log(f"New obs:  {count} observations")


if __name__ == "__main__":
    main()
