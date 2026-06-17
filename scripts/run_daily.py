#!/usr/bin/env python3
"""Martin Morning Brief 每日一键运行入口。

依次执行:
  1. import_seed.py          — 数据库不存在时从 Excel 导入
  2. update_data.py          — 生成增量更新计划
  3. fetch_data.py           — 从同花顺 EDB 拉取最新数据
  4. fetch_wind.py           — 从 Wind MCP 拉取补充数据
  5. recompute_fx_derived.py — 从原始数据复算外汇衍生序列
  6. fetch_emotion.py        — 从华泰智研 MCP 拉取市场情绪数据
  7. generate_interactive_dashboard.py — 生成交互式 HTML 看板

用法:
  python3 scripts/run_daily.py                    # 完整流水线
  python3 scripts/run_daily.py --skip-fetch       # 跳过所有数据拉取，仅生成看板
  python3 scripts/run_daily.py --skip-fetch-ths   # 仅跳过同花顺 EDB
  python3 scripts/run_daily.py --skip-fetch-wind  # 仅跳过 Wind MCP
"""

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def run(args):
    subprocess.run([PYTHON, *args], cwd=ROOT, check=True)


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

    db = ROOT / "data" / "morning_brief.sqlite"
    if not db.exists():
        print("[run_daily] Database not found, importing from Excel seed...")
        run(["scripts/import_seed.py"])

    total_steps = 6
    step = 0

    print(f"[run_daily] Step {step+1}/{total_steps}: Generating update plan...")
    step += 1
    run(["scripts/update_data.py"])

    if not args.skip_fetch and not args.skip_fetch_ths:
        print(f"[run_daily] Step {step+1}/{total_steps}: Fetching from THS EDB...")
        step += 1
        try:
            run(["scripts/fetch_data.py"])
        except subprocess.CalledProcessError as e:
            print(f"[run_daily] WARNING: fetch_data.py failed with exit code {e.returncode}")
            print("[run_daily] Continuing...")
    else:
        print(f"[run_daily] Step {step+1}/{total_steps}: THS EDB fetch skipped")
        step += 1

    if not args.skip_fetch and not args.skip_fetch_wind:
        print(f"[run_daily] Step {step+1}/{total_steps}: Fetching from Wind MCP...")
        step += 1
        try:
            run(["scripts/fetch_wind.py"])
        except subprocess.CalledProcessError as e:
            print(f"[run_daily] WARNING: fetch_wind.py failed with exit code {e.returncode}")
            print("[run_daily] Continuing...")
    else:
        print(f"[run_daily] Step {step+1}/{total_steps}: Wind MCP fetch skipped")
        step += 1

    if not args.skip_fetch and not args.skip_fetch_emotion:
        print(f"[run_daily] Step {step+1}/{total_steps}: Fetching market emotion from HTSC MCP...")
        step += 1
        try:
            run(["scripts/fetch_emotion.py"])
        except subprocess.CalledProcessError as e:
            print(f"[run_daily] WARNING: fetch_emotion.py failed with exit code {e.returncode}")
            print("[run_daily] Continuing...")
    else:
        print(f"[run_daily] Step {step+1}/{total_steps}: HTSC MCP fetch skipped")
        step += 1

    print(f"[run_daily] Step {step+1}/{total_steps}: Recomputing FX derived series...")
    step += 1
    try:
        run(["scripts/recompute_fx_derived.py"])
    except subprocess.CalledProcessError as e:
        print(f"[run_daily] WARNING: recompute_fx_derived.py failed with exit code {e.returncode}")
        print("[run_daily] Continuing...")

    print(f"[run_daily] Step {step+1}/{total_steps}: Generating interactive dashboard...")
    step += 1
    run(["scripts/generate_interactive_dashboard.py"])

    dashboard = ROOT / "output" / "interactive_dashboard.html"
    print(f"[run_daily] Done! Dashboard: {dashboard}")


if __name__ == "__main__":
    main()
