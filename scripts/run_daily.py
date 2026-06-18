#!/usr/bin/env python3
"""Martin Morning Brief 每日一键运行入口。

依次执行:
  1. import_seed.py          — 数据库不存在时从 Excel 导入
  2. import_fx_data.py       — FX 数据不存在时从 Excel 导入
  3. update_data.py          — 生成增量更新计划
  4. fetch_data.py           — 从同花顺 EDB 拉取最新数据
  5. fetch_wind.py           — 从 Wind MCP 拉取补充数据
  6. recompute_fx_derived.py — 从原始数据复算外汇衍生序列
  7. fetch_emotion.py        — 从华泰智研 MCP 拉取市场情绪数据
  8. generate_interactive_dashboard.py — 生成交互式 HTML 看板

用法:
  python3 scripts/run_daily.py                    # 完整流水线
  python3 scripts/run_daily.py --skip-fetch       # 跳过所有数据拉取，仅生成看板
  python3 scripts/run_daily.py --skip-fetch-ths   # 仅跳过同花顺 EDB
  python3 scripts/run_daily.py --skip-fetch-wind  # 仅跳过 Wind MCP
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

from lib import ROOT, DEFAULT_DB, open_db

PYTHON = sys.executable

# Windows 默认 GBK 编码无法输出 ✓✗⚠ 等 Unicode 字符，强制 UTF-8
_UTF8_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def run(args):
    subprocess.run([PYTHON, *args], cwd=ROOT, check=True, env=_UTF8_ENV)


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

    for i, (label, cmd) in enumerate(steps):
        print(f"[run_daily] Step {i+1}/{total_steps}: {label}")
        if cmd is not None:
            try:
                run(cmd)
            except subprocess.CalledProcessError as e:
                print(f"[run_daily] WARNING: {cmd[0]} failed with exit code {e.returncode}")
                print("[run_daily] Continuing...")

    dashboard = ROOT / "output" / "interactive_dashboard.html"
    print(f"[run_daily] Done! Dashboard: {dashboard}")


if __name__ == "__main__":
    main()
