#!/usr/bin/env python3
"""Martin Morning Brief 每日一键运行入口。

依次执行:
  1. import_seed.py           — 数据库不存在时从 Excel 导入（all-in-one）
  2. update_data.py           — 生成增量更新计划
  3. fetch_data.py            — 从同花顺 EDB 拉取最新数据
  4. fetch_wind.py            — 从 Wind MCP 拉取补充数据
  5. fetch_emotion.py         — 从华泰智研 MCP 拉取市场情绪数据
  6. recompute_fx_derived.py  — 从原始数据复算外汇衍生序列
  7. generate_interactive_dashboard.py — 生成 HTML 看板 + docs/index.html (GitHub Pages)

用法:
  python3 scripts/run_daily.py                    # 完整流水线
  python3 scripts/run_daily.py --skip-fetch       # 跳过所有数据拉取，仅生成看板
  python3 scripts/run_daily.py --skip-fetch-ths   # 仅跳过同花顺 EDB
  python3 scripts/run_daily.py --skip-fetch-wind  # 仅跳过 Wind MCP
  python3 scripts/run_daily.py --skip-fetch-emotion  # 仅跳过华泰智研 MCP
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from lib import ROOT, DEFAULT_DB, open_db, load_json

PYTHON = sys.executable

# Windows 默认 GBK 编码无法输出 ✓✗⚠ 等 Unicode 字符，强制 UTF-8
_UTF8_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def run(args):
    subprocess.run([PYTHON, *args], cwd=ROOT, check=True, env=_UTF8_ENV)


def _read_json(path):
    """Read JSON file, return {} if missing."""
    try:
        return load_json(path)
    except (FileNotFoundError, json.JSONDecodeError, Exception):
        return {}


def main():
    parser = argparse.ArgumentParser(description="Martin Morning Brief daily pipeline")
    parser.add_argument("--skip-fetch", action="store_true",
                        help="Skip ALL data fetching (both THS EDB and Wind MCP)")
    parser.add_argument("--skip-fetch-ths", action="store_true",
                        help="Skip only THS EDB fetch")
    parser.add_argument("--skip-fetch-wind", action="store_true",
                        help="Skip only Wind MCP fetch")
    parser.add_argument("--skip-fetch-emotion", action="store_true",
                        help="Skip only HTSC emotion fetch")
    args = parser.parse_args()

    start_time = time.time()
    start_dt = datetime.now()

    db = DEFAULT_DB
    if not db.exists():
        print("[run_daily] Database not found, importing from Excel seed...")
        run(["scripts/import_seed.py", "--replace"])
    elif db.exists():
        # Check if FX or super_cycle data is missing (e.g. from older DB version)
        with open_db(db) as _conn:
            _has_fx = _conn.execute(
                "SELECT COUNT(*) FROM series WHERE series_id LIKE 'fx:%'"
            ).fetchone()[0] > 0
            _has_sc = _conn.execute(
                "SELECT COUNT(*) FROM series WHERE series_id LIKE 'super_cycle:%'"
            ).fetchone()[0] > 0
        if not _has_fx or not _has_sc:
            print("[run_daily] Missing data detected, running import_seed.py (idempotent)...")
            try:
                run(["scripts/import_seed.py"])
            except subprocess.CalledProcessError as e:
                print(f"[run_daily] WARNING: import_seed.py failed with exit code {e.returncode}")
                print("[run_daily] Continuing...")

    # Build dynamic step list
    steps = [("Generating update plan", ["scripts/update_data.py"])]

    if not args.skip_fetch and not args.skip_fetch_ths:
        steps.append(("Fetching from THS EDB", ["scripts/fetch_data.py"]))
    else:
        steps.append(("THS EDB fetch (skipped)", None))

    if not args.skip_fetch and not args.skip_fetch_wind:
        steps.append(("Fetching from Wind MCP", ["scripts/fetch_wind.py"]))
    else:
        steps.append(("Wind MCP fetch (skipped)", None))

    if not args.skip_fetch and not args.skip_fetch_emotion:
        steps.append(("Fetching market emotion from HTSC MCP", ["scripts/fetch_emotion.py"]))
    else:
        steps.append(("HTSC MCP fetch (skipped)", None))

    steps.append(("Recomputing FX derived series", ["scripts/recompute_fx_derived.py"]))
    steps.append(("Generating interactive dashboard", ["scripts/generate_interactive_dashboard.py"]))

    total_steps = len(steps)
    step_failures = 0

    for i, (label, cmd) in enumerate(steps):
        print(f"[run_daily] Step {i+1}/{total_steps}: {label}")
        if cmd is not None:
            try:
                run(cmd)
            except subprocess.CalledProcessError as e:
                print(f"[run_daily] WARNING: {cmd[0]} failed with exit code {e.returncode}")
                print("[run_daily] Continuing...")
                step_failures += 1

    # ── Collect metrics ──
    elapsed = time.time() - start_time
    elapsed_str = str(timedelta(seconds=int(elapsed)))

    # Read Wind API call counts from summary JSONs
    edb_summary = _read_json(ROOT / "data" / "fetch_summary.json")
    wind_summary = _read_json(ROOT / "data" / "wind_fetch_summary.json")

    edb_wind_calls = edb_summary.get("wind_api_calls", 0)
    edb_fallback = edb_summary.get("wind_fallback_used", 0)
    wind_calls = wind_summary.get("wind_api_calls", 0)
    total_wind_calls = edb_wind_calls + wind_calls

    # Total observations inserted
    edb_obs = edb_summary.get("obs_inserted", 0)
    wind_obs = wind_summary.get("obs_inserted", 0)

    dashboard = ROOT / "output" / "interactive_dashboard.html"
    docs_dashboard = ROOT / "docs" / "index.html"
    print(f"[run_daily] Done! Dashboard: {dashboard}")
    print(f"[run_daily] GitHub Pages: {docs_dashboard}")

    # ── Execution Report ──
    print()
    print("=" * 60)
    print("  Martin Morning Brief — 执行报告")
    print("=" * 60)
    print(f"  执行时间:     {start_dt.strftime('%Y-%m-%d %H:%M:%S')} CST")
    print(f"  总耗时:       {elapsed_str}")
    print(f"  数据新增:     {edb_obs + wind_obs} 条观测")
    print(f"  Wind API 调用: {total_wind_calls} 次")
    if edb_fallback > 0:
        print(f"    ├─ EDB→Wind 自动切换: {edb_fallback} 个序列")
        print(f"    └─ 常规 Wind 拉取:    {wind_calls} 次")
    if edb_wind_calls > 0:
        print(f"  EDB 降级拉取:  {edb_wind_calls} 次 Wind 调用 (auto-fallback)")
    print(f"  步骤失败:     {step_failures} 个 (已自动跳过)")
    print("=" * 60)


if __name__ == "__main__":
    main()
