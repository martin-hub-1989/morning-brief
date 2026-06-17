#!/usr/bin/env python3
"""
从 中间价与套保成本.xlsx 导入外汇相关序列到 SQLite。

数据包含两个工作表：
  - Fixing: 美元兑人民币中间价、即期汇率（日频，1981 年起）
  - 0.Fwd Spread: CNH 远期、在岸掉期点、套保成本、中美短端利率（日频）

数据分为两类：
  A) 原始数据（从 Wind 直接导出，未来可从 Wind MCP 更新）：
     - USDCNY 中间价 / 即期汇率
     - USDCNH 即期汇率 / DF 远期
     - USDCNY 掉期点 (买报价)
  B) 衍生数据（Excel 公式计算，未来由 Python 应用层复算，不从外部 MCP 获取）：
     - 套保成本 (Cols 14-21)
     - 年化套保成本 (Cols 22-29)

=== 即期汇率变动拆解（自创指标，Excel 公式计算）===

夜盘中间价调整 (Col 5):
  = IF(昨收=0, 0, (-今日中间价 + 昨日即期收盘) × 10^4)    [单位: pips]
  含义：PBOC 夜盘将中间价相对昨日收盘价调整了多少。正值 = RMB 升值方向。

日盘交易变动 (Col 6):
  = IF(今收=0, 0, (今日中间价 - 今日即期收盘) × 10^4)      [单位: pips]
  含义：日盘市场从中间价出发交易了多少。正值 = RMB 升值方向。

恒等式: 夜盘调整 + 日盘变动 = -(即期变动) = -(今日收盘 - 昨日收盘)

累积值 (Cols 7-8): 从第 5 行向下的运行总和
5 日 MA (Cols 11-12): (累积_t - 累积_{t-5}) / 5
20 日 MA (Cols 14-15): (累积_t - 累积_{t-20}) / 20

=== 套保成本计算逻辑（提取自 Excel 公式）===

CNH 套保成本 (Cols 14-17):
  = IF(CNH_DF * CNH_Spot = 0, 前值, CNH_DF / CNH_Spot - 1)
  即：CNH 远期升贴水幅度 = (DF远期价 / CNH即期价) - 1
  负值表示 USD 远期贴水（做多 USD 需付出的套保成本）

CNY 套保成本 (Cols 18-21):
  = IF(掉期点 * CNY_Spot = 0, 前值, 掉期点 / 10000 / CNY_Spot)
  即：将掉期点(pips)转换为汇率单位后除以即期价
  掉期点 ÷ 10000 → 远期升贴水幅度

年化套保成本 (Cols 22-29):
  = (套保成本 + 1)^n - 1
  其中 n = 12(1M), 4(3M), 2(6M), 1(1Y)
  即复利年化：(1 + r)^n - 1

用法:
  python3 scripts/import_fx_data.py                     # 导入全部（幂等）
  python3 scripts/import_fx_data.py --dry-run           # 干跑
  python3 scripts/import_fx_data.py --verbose           # 详细输出
"""

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

try:
    import openpyxl
except ImportError:
    print("ERROR: openpyxl is required. pip install openpyxl")
    sys.exit(1)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "morning_brief.sqlite"
DEFAULT_XLSX = ROOT / "seed" / "中间价与套保成本.xlsx"

# ── Series definitions ─────────────────────────────────────────────────
#
# update_method 约定:
#   "wind_mcp"   — 原始数据，未来可从 Wind MCP 定期拉取更新
#   "cfets"      — 原始数据，来源中国外汇交易中心，可从 Wind 获取
#   "derived"    — 非原始数据，由 Excel/Python 公式基于原始数据计算，不从 MCP 获取
#
# (series_id, display_name, sheet_name, unit, source_name, update_method, excel_sheet, excel_col)
FX_SERIES = [
    # ── 原始数据 (Wind / CFETS) ──────────────────────────────
    # Sheet "Fixing"
    ("fx:usdcny-fixing",     "USDCNY中间价",       "外汇", "fx",      "CFETS", "cfets",    "Fixing", 2),
    ("fx:usdcny-spot",       "USDCNY即期汇率",     "外汇", "fx",      "CFETS", "cfets",    "Fixing", 3),
    # Sheet "0.Fwd Spread" — CNH spot + forwards (Wind 终端导出)
    ("fx:usdcnh-spot",       "USDCNH即期汇率",     "外汇", "fx",      "Wind",  "wind_mcp", "0.Fwd Spread", 2),
    ("fx:cnh-df-1m",         "USDCNH DF 1M",       "外汇", "fx",      "Wind",  "wind_mcp", "0.Fwd Spread", 3),
    ("fx:cnh-df-3m",         "USDCNH DF 3M",       "外汇", "fx",      "Wind",  "wind_mcp", "0.Fwd Spread", 4),
    ("fx:cnh-df-6m",         "USDCNH DF 6M",       "外汇", "fx",      "Wind",  "wind_mcp", "0.Fwd Spread", 5),
    ("fx:cnh-df-1y",         "USDCNH DF 1Y",       "外汇", "fx",      "Wind",  "wind_mcp", "0.Fwd Spread", 6),
    # Sheet "0.Fwd Spread" — CNY onshore swap points (买报价, 单位: pips)
    ("fx:cny-swap-1m",       "USDCNY掉期点 1M",    "外汇", "price",   "Wind",  "wind_mcp", "0.Fwd Spread", 7),
    ("fx:cny-swap-3m",       "USDCNY掉期点 3M",    "外汇", "price",   "Wind",  "wind_mcp", "0.Fwd Spread", 8),
    ("fx:cny-swap-6m",       "USDCNY掉期点 6M",    "外汇", "price",   "Wind",  "wind_mcp", "0.Fwd Spread", 9),
    ("fx:cny-swap-1y",       "USDCNY掉期点 1Y",    "外汇", "price",   "Wind",  "wind_mcp", "0.Fwd Spread", 10),

    # ── 原始数据：中美短端利率（用于计算中美利差）─────────
    # Sheet "0.Fwd Spread" — cols 32-39, China and US short-end rates
    ("fx:cny-bond-1y",       "中债国债 1Y",        "外汇", "percent_point","Wind","wind_mcp","0.Fwd Spread", 34),
    ("fx:usd-bond-1y",       "美国国债 1Y",        "外汇", "percent_point","Wind","wind_mcp","0.Fwd Spread", 38),

    # ── 衍生数据：即期汇率变动拆解（自创指标，公式计算）─────
    # 将即期汇率每日变动拆解为两个正交分量的贡献：
    #
    #   夜盘中间价调整 = (-今日中间价 + 昨日即期收盘) × 10000   [单位: pips]
    #     含义：PBOC 在夜盘将中间价相对昨日收盘价调整了多少
    #     正值 = 中间价调强（RMB升值方向）
    #
    #   日盘交易变动   = (今日中间价 - 今日即期收盘) × 10000     [单位: pips]
    #     含义：日盘市场从中间价出发交易了多少
    #     正值 = 即期收盘强于中间价（RMB升值方向）
    #
    #   恒等式: 夜盘调整 + 日盘变动 = -(即期变动) = -(今日收盘 - 昨日收盘)
    #
    ("fx:decomp-night-adj",  "夜盘中间价调整",     "外汇", "price", "自创指标","derived", "Fixing", 5),
    ("fx:decomp-day-move",   "日盘交易变动",       "外汇", "price", "自创指标","derived", "Fixing", 6),
    # 累积值 (从 Excel 第 5 行向下的运行总和)
    ("fx:decomp-night-cum",  "夜盘中间价调整累积",  "外汇", "price", "自创指标","derived", "Fixing", 7),
    ("fx:decomp-day-cum",    "日盘交易变动累积",    "外汇", "price", "自创指标","derived", "Fixing", 8),
    # 5 日移动平均
    ("fx:decomp-night-5d",   "夜盘中间价调整 5MA",  "外汇", "price", "自创指标","derived", "Fixing", 11),
    ("fx:decomp-day-5d",     "日盘交易变动 5MA",    "外汇", "price", "自创指标","derived", "Fixing", 12),
    # 20 日移动平均
    ("fx:decomp-night-20d",  "夜盘中间价调整 20MA", "外汇", "price", "自创指标","derived", "Fixing", 14),
    ("fx:decomp-day-20d",    "日盘交易变动 20MA",   "外汇", "price", "自创指标","derived", "Fixing", 15),

    # ── 衍生数据：套保成本（公式计算，不从 MCP 获取）────────
    # CNH 套保成本 = CNH_DF / CNH_Spot - 1
    ("fx:cnh-hedge-1m",      "CNH套保成本 1M",     "外汇", "percent", "公式",  "derived",  "0.Fwd Spread", 14),
    ("fx:cnh-hedge-3m",      "CNH套保成本 3M",     "外汇", "percent", "公式",  "derived",  "0.Fwd Spread", 15),
    ("fx:cnh-hedge-6m",      "CNH套保成本 6M",     "外汇", "percent", "公式",  "derived",  "0.Fwd Spread", 16),
    ("fx:cnh-hedge-1y",      "CNH套保成本 1Y",     "外汇", "percent", "公式",  "derived",  "0.Fwd Spread", 17),
    # CNY 套保成本 = 掉期点(pips) / 10000 / CNY_Spot
    ("fx:cny-hedge-1m",      "CNY套保成本 1M",     "外汇", "percent", "公式",  "derived",  "0.Fwd Spread", 18),
    ("fx:cny-hedge-3m",      "CNY套保成本 3M",     "外汇", "percent", "公式",  "derived",  "0.Fwd Spread", 19),
    ("fx:cny-hedge-6m",      "CNY套保成本 6M",     "外汇", "percent", "公式",  "derived",  "0.Fwd Spread", 20),
    ("fx:cny-hedge-1y",      "CNY套保成本 1Y",     "外汇", "percent", "公式",  "derived",  "0.Fwd Spread", 21),
    # 年化套保成本 = (1 + 套保成本)^n - 1  (n = 12/4/2/1 for 1M/3M/6M/1Y)
    ("fx:cnh-hedge-ann-1m",  "CNH套保成本(年化) 1M","外汇","percent", "公式",  "derived",  "0.Fwd Spread", 22),
    ("fx:cnh-hedge-ann-3m",  "CNH套保成本(年化) 3M","外汇","percent", "公式",  "derived",  "0.Fwd Spread", 23),
    ("fx:cnh-hedge-ann-6m",  "CNH套保成本(年化) 6M","外汇","percent", "公式",  "derived",  "0.Fwd Spread", 24),
    ("fx:cnh-hedge-ann-1y",  "CNH套保成本(年化) 1Y","外汇","percent", "公式",  "derived",  "0.Fwd Spread", 25),
    ("fx:cny-hedge-ann-1m",  "CNY套保成本(年化) 1M","外汇","percent", "公式",  "derived",  "0.Fwd Spread", 26),
    ("fx:cny-hedge-ann-3m",  "CNY套保成本(年化) 3M","外汇","percent", "公式",  "derived",  "0.Fwd Spread", 27),
    ("fx:cny-hedge-ann-6m",  "CNY套保成本(年化) 6M","外汇","percent", "公式",  "derived",  "0.Fwd Spread", 28),
    ("fx:cny-hedge-ann-1y",  "CNY套保成本(年化) 1Y","外汇","percent", "公式",  "derived",  "0.Fwd Spread", 29),
]


def log(msg, level="INFO"):
    prefix = {"INFO": "  ", "WARN": "  ⚠", "ERROR": "  ✗", "OK": "  ✓"}
    print(f"{prefix.get(level, '  ')} {msg}", file=sys.stderr if level == "ERROR" else sys.stdout)


def parse_date(v):
    """Parse Excel datetime to date string YYYY-MM-DD."""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, str):
        return v[:10]
    return None


def import_fx_data(db_path, xlsx_path, dry_run=False, verbose=False):
    if not Path(xlsx_path).exists():
        log(f"Excel not found: {xlsx_path}", "ERROR")
        return 0

    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    conn = sqlite3.connect(db_path)
    imported_at = datetime.now().isoformat(timespec="seconds")

    # Ensure series rows exist
    for sid, display_name, sheet_name, unit, source_name, update_method, _, _ in FX_SERIES:
        conn.execute(
            """INSERT OR IGNORE INTO series (
                   series_id, display_name, sheet_name, frequency, unit,
                   source_name, source_code, active, update_method, created_at, updated_at
               ) VALUES (?, ?, ?, 'D', ?, ?, ?, 1, ?, ?, ?)""",
            (sid, display_name, sheet_name, unit, source_name, sid, update_method, imported_at, imported_at),
        )

    total_inserted = 0

    for sid, display_name, sheet_name, unit, source_name, update_method, excel_sheet, excel_col in FX_SERIES:
        ws = wb[excel_sheet]
        if ws is None:
            log(f"Sheet '{excel_sheet}' not found — skipping {sid}", "WARN")
            continue

        # Find data rows: row 5 onwards, col 1 = date, excel_col = value
        points = []
        for r in range(5, ws.max_row + 1):
            date_val = ws.cell(row=r, column=1).value
            val = ws.cell(row=r, column=excel_col).value
            d = parse_date(date_val)
            if d is None:
                continue
            if val is None:
                continue
            # Convert zero to None (0 = missing data in Wind exports)
            try:
                fv = float(val)
            except (ValueError, TypeError):
                continue
            if fv == 0:
                continue
            points.append((d, fv))

        if not points:
            log(f"No data for {sid}", "WARN")
            continue

        # Get existing dates for this series to avoid re-inserting
        existing = set()
        for row in conn.execute(
            "SELECT date FROM observations WHERE series_id = ?", (sid,)
        ).fetchall():
            existing.add(row[0])

        new_pts = [(d, v) for d, v in points if d not in existing]

        if verbose:
            log(f"{display_name}: {len(points)} pts total, {len(new_pts)} new")

        if not dry_run:
            for d, v in new_pts:
                conn.execute(
                    """INSERT OR REPLACE INTO observations (series_id, date, value, as_of_date, imported_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (sid, d, v, d, imported_at),
                )
        total_inserted += len(new_pts)

    wb.close()

    if not dry_run and total_inserted > 0:
        conn.commit()
        log(f"Committed {total_inserted} new observations", "OK")
    elif dry_run:
        log(f"[DRY RUN] Would insert {total_inserted} observations", "WARN")
    else:
        log("No new observations to insert")

    conn.close()
    return total_inserted


def main():
    parser = argparse.ArgumentParser(description="Import FX data from 中间价与套保成本.xlsx")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to SQLite database")
    parser.add_argument("--xlsx", default=str(DEFAULT_XLSX), help="Path to Excel source")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    if not Path(args.db).exists():
        log(f"Database not found: {args.db}", "ERROR")
        sys.exit(1)

    log("Martin Morning Brief — import_fx_data.py")
    if args.dry_run:
        log("Mode: DRY RUN (no writes)", "WARN")

    count = import_fx_data(args.db, args.xlsx, dry_run=args.dry_run, verbose=args.verbose)
    log(f"=== FX Import Summary ===")
    log(f"New obs:  {count}")


if __name__ == "__main__":
    main()
