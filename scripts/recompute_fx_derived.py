#!/usr/bin/env python3
"""
从原始外汇数据重新计算所有衍生序列，确保口径一致。

衍生序列分为三类：
  A) 即期汇率变动拆解（8 个序列）
     - 夜盘调整 / 日盘变动 / 累积值 / 5MA / 20MA
     - 依赖: fx:usdcny-fixing + fx:usdcny-spot

  B) 套保成本（8 个序列）
     - CNY 套保成本 = 掉期点(pips) / 10000 / CNY即期汇率
     - CNH 套保成本 = CNH远期 / CNH即期汇率 - 1
     - 依赖: fx:cny-swap-* + fx:usdcny-spot + fx:cnh-df-* + fx:usdcnh-spot

  C) 年化套保成本（8 个序列）
     - (1 + 套保成本)^n - 1  (n = 12/4/2/1 for 1M/3M/6M/1Y)
     - 依赖: 套保成本序列

用法:
  python3 scripts/recompute_fx_derived.py              # 复算全部衍生序列
  python3 scripts/recompute_fx_derived.py --dry-run    # 干跑
  python3 scripts/recompute_fx_derived.py --verbose    # 详细输出
  python3 scripts/recompute_fx_derived.py --category decomp  # 仅复算汇率拆解
  python3 scripts/recompute_fx_derived.py --category hedge    # 仅复算套保成本
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

from lib import ROOT, DEFAULT_DB, log, open_db

# ── 衍生序列定义 ────────────────────────────────────────────────────────
#
# (series_id, display_name, unit, category)
DERIVED_SERIES = [
    # ── A) 即期汇率变动拆解 ──
    ("fx:decomp-night-adj",  "夜盘中间价调整",      "price",  "decomp"),
    ("fx:decomp-day-move",   "日盘交易变动",        "price",  "decomp"),
    ("fx:decomp-night-cum",  "夜盘中间价调整累积",   "price",  "decomp"),
    ("fx:decomp-day-cum",    "日盘交易变动累积",     "price",  "decomp"),
    ("fx:decomp-night-5d",   "夜盘中间价调整 5MA",   "price",  "decomp"),
    ("fx:decomp-day-5d",     "日盘交易变动 5MA",     "price",  "decomp"),
    ("fx:decomp-night-20d",  "夜盘中间价调整 20MA",  "price",  "decomp"),
    ("fx:decomp-day-20d",    "日盘交易变动 20MA",    "price",  "decomp"),

    # ── B) 套保成本 ──
    ("fx:cny-hedge-1m",      "CNY套保成本 1M",      "percent", "hedge"),
    ("fx:cny-hedge-3m",      "CNY套保成本 3M",      "percent", "hedge"),
    ("fx:cny-hedge-6m",      "CNY套保成本 6M",      "percent", "hedge"),
    ("fx:cny-hedge-1y",      "CNY套保成本 1Y",      "percent", "hedge"),
    ("fx:cnh-hedge-1m",      "CNH套保成本 1M",      "percent", "hedge"),
    ("fx:cnh-hedge-3m",      "CNH套保成本 3M",      "percent", "hedge"),
    ("fx:cnh-hedge-6m",      "CNH套保成本 6M",      "percent", "hedge"),
    ("fx:cnh-hedge-1y",      "CNH套保成本 1Y",      "percent", "hedge"),

    # ── C) 年化套保成本 ──
    ("fx:cnh-hedge-ann-1m",  "CNH套保成本(年化) 1M", "percent", "hedge"),
    ("fx:cnh-hedge-ann-3m",  "CNH套保成本(年化) 3M", "percent", "hedge"),
    ("fx:cnh-hedge-ann-6m",  "CNH套保成本(年化) 6M", "percent", "hedge"),
    ("fx:cnh-hedge-ann-1y",  "CNH套保成本(年化) 1Y", "percent", "hedge"),
    ("fx:cny-hedge-ann-1m",  "CNY套保成本(年化) 1M", "percent", "hedge"),
    ("fx:cny-hedge-ann-3m",  "CNY套保成本(年化) 3M", "percent", "hedge"),
    ("fx:cny-hedge-ann-6m",  "CNY套保成本(年化) 6M", "percent", "hedge"),
    ("fx:cny-hedge-ann-1y",  "CNY套保成本(年化) 1Y", "percent", "hedge"),
]

# 年化因子：1M→12, 3M→4, 6M→2, 1Y→1
ANNUAL_FACTORS = {"1m": 12, "3m": 4, "6m": 2, "1y": 1}


def load_series(conn, series_id):
    """加载一个序列的全部观测，返回 {date: value} 字典。"""
    rows = conn.execute(
        "SELECT date, value FROM observations WHERE series_id = ? ORDER BY date",
        (series_id,)
    ).fetchall()
    return {r[0]: r[1] for r in rows}


# ── A) 即期汇率变动拆解 ────────────────────────────────────────────────

def compute_decomp(conn, imported_at, dry_run=False, verbose=False):
    """
    计算即期汇率变动拆解序列。

    公式:
      night_adj[t] = (-fixing[t] + spot[t-1]) * 10000   [pips]
      day_move[t]   = (fixing[t] - spot[t]) * 10000     [pips]
      night_cum[t]  = Σ night_adj from row 5 (excel convention)
      day_cum[t]    = Σ day_move from row 5
      5MA[t]        = (cum[t] - cum[t-5]) / 5
      20MA[t]       = (cum[t] - cum[t-20]) / 20
    """
    fixing_data = load_series(conn, "fx:usdcny-fixing")
    spot_data = load_series(conn, "fx:usdcny-spot")

    if not fixing_data or not spot_data:
        log("Missing raw data for decomp: fx:usdcny-fixing or fx:usdcny-spot", "ERROR")
        return 0

    # Align dates: only dates where both fixing and spot exist
    all_dates = sorted(set(fixing_data.keys()) & set(spot_data.keys()))
    if len(all_dates) < 2:
        log("Insufficient data for decomp computation", "WARN")
        return 0

    # Step 1: Compute night_adj and day_move
    night_adj = {}  # date → value
    day_move = {}   # date → value

    # Index-based access for prev-day lookup
    spot_list = [(d, spot_data[d]) for d in all_dates]
    fixing_list = [(d, fixing_data[d]) for d in all_dates]

    for i, (d, fixing_val) in enumerate(fixing_list):
        spot_val = spot_data.get(d)
        if spot_val is None:
            continue

        # day_move: uses same-day spot
        if spot_val != 0:
            day_move[d] = (fixing_val - spot_val) * 10000

        # night_adj: uses previous-day spot
        if i > 0:
            prev_spot = spot_list[i - 1][1]
            if prev_spot != 0:
                night_adj[d] = (-fixing_val + prev_spot) * 10000

    # Step 2: Cumulative sums (from 5th data point onwards, matching Excel convention)
    night_cum = {}
    day_cum = {}
    night_sum = 0.0
    day_sum = 0.0

    for i, d in enumerate(all_dates):
        if d in night_adj:
            night_sum += night_adj[d]
        if d in day_move:
            day_sum += day_move[d]
        # Excel starts cum from row 5 (skip first 4 rows)
        if i >= 4:
            night_cum[d] = night_sum
            day_cum[d] = day_sum

    # Step 3: Moving averages
    cum_dates = sorted(night_cum.keys())
    night_5d = {}
    day_5d = {}
    night_20d = {}
    day_20d = {}

    for i, d in enumerate(cum_dates):
        # 5-day MA
        if i >= 5:
            night_5d[d] = (night_cum[d] - night_cum[cum_dates[i - 5]]) / 5
            day_5d[d] = (day_cum[d] - day_cum[cum_dates[i - 5]]) / 5
        # 20-day MA
        if i >= 20:
            night_20d[d] = (night_cum[d] - night_cum[cum_dates[i - 20]]) / 20
            day_20d[d] = (day_cum[d] - day_cum[cum_dates[i - 20]]) / 20

    results = {
        "fx:decomp-night-adj": night_adj,
        "fx:decomp-day-move": day_move,
        "fx:decomp-night-cum": night_cum,
        "fx:decomp-day-cum": day_cum,
        "fx:decomp-night-5d": night_5d,
        "fx:decomp-day-5d": day_5d,
        "fx:decomp-night-20d": night_20d,
        "fx:decomp-day-20d": day_20d,
    }

    # Upsert
    total = 0
    for sid, data in results.items():
        count = upsert_derived(conn, sid, data, imported_at, dry_run)
        total += count
        if verbose:
            log(f"  {sid}: {len(data)} dates, {count} new")

    return total


# ── B) 套保成本 + C) 年化套保成本 ─────────────────────────────────────

def compute_hedge_costs(conn, imported_at, dry_run=False, verbose=False):
    """
    计算套保成本和年化套保成本。

    CNY 套保成本 = swap_points / 10000 / CNY_spot
    CNH 套保成本 = CNH_DF / CNH_spot - 1
    年化 = (1 + 套保成本)^n - 1
    """
    cny_spot_data = load_series(conn, "fx:usdcny-spot")
    cnh_spot_data = load_series(conn, "fx:usdcnh-spot")

    # Load swap points and CNH DF forwards
    tenors = ["1m", "3m", "6m", "1y"]
    swap_data = {}
    cnh_df_data = {}

    for t in tenors:
        swap_data[t] = load_series(conn, f"fx:cny-swap-{t}")
        cnh_df_data[t] = load_series(conn, f"fx:cnh-df-{t}")

    if not cny_spot_data:
        log("Missing fx:usdcny-spot — skipping CNY hedge costs", "WARN")
    if not cnh_spot_data:
        log("Missing fx:usdcnh-spot — skipping CNH hedge costs", "WARN")

    total = 0

    for t in tenors:
        # ── CNY Hedge Cost ──
        if cny_spot_data and swap_data[t]:
            cny_hedge = {}
            for d, swap_val in swap_data[t].items():
                spot_val = cny_spot_data.get(d)
                if spot_val and spot_val != 0:
                    cny_hedge[d] = swap_val / 10000 / spot_val

            sid_cny = f"fx:cny-hedge-{t}"
            count = upsert_derived(conn, sid_cny, cny_hedge, imported_at, dry_run)
            total += count
            if verbose:
                log(f"  {sid_cny}: {len(cny_hedge)} dates, {count} new")

            # CNY Annualized
            n = ANNUAL_FACTORS[t]
            cny_ann = {}
            for d, v in cny_hedge.items():
                cny_ann[d] = (1 + v) ** n - 1

            sid_cny_ann = f"fx:cny-hedge-ann-{t}"
            count = upsert_derived(conn, sid_cny_ann, cny_ann, imported_at, dry_run)
            total += count
            if verbose:
                log(f"  {sid_cny_ann}: {len(cny_ann)} dates, {count} new")

        # ── CNH Hedge Cost ──
        if cnh_spot_data and cnh_df_data[t]:
            cnh_hedge = {}
            for d, df_val in cnh_df_data[t].items():
                spot_val = cnh_spot_data.get(d)
                if spot_val and spot_val != 0:
                    cnh_hedge[d] = df_val / spot_val - 1

            sid_cnh = f"fx:cnh-hedge-{t}"
            count = upsert_derived(conn, sid_cnh, cnh_hedge, imported_at, dry_run)
            total += count
            if verbose:
                log(f"  {sid_cnh}: {len(cnh_hedge)} dates, {count} new")

            # CNH Annualized
            n = ANNUAL_FACTORS[t]
            cnh_ann = {}
            for d, v in cnh_hedge.items():
                cnh_ann[d] = (1 + v) ** n - 1

            sid_cnh_ann = f"fx:cnh-hedge-ann-{t}"
            count = upsert_derived(conn, sid_cnh_ann, cnh_ann, imported_at, dry_run)
            total += count
            if verbose:
                log(f"  {sid_cnh_ann}: {len(cnh_ann)} dates, {count} new")

    return total


# ── UPSERT helper ──────────────────────────────────────────────────────

def ensure_series_row(conn, series_id, display_name, unit, imported_at):
    """确保 series 表有该序列的行（幂等）。"""
    conn.execute(
        """INSERT OR IGNORE INTO series (
               series_id, display_name, sheet_name, frequency, unit,
               source_name, source_code, active, update_method, created_at, updated_at
           ) VALUES (?, ?, '外汇', 'D', ?, 'Python复算', ?, 1, 'derived', ?, ?)""",
        (series_id, display_name, unit, series_id, imported_at, imported_at),
    )


def upsert_derived(conn, series_id, data, imported_at, dry_run=False):
    """将衍生数据写入 observations 表（幂等：先查已有日期）。"""
    if not data:
        return 0

    # Get existing dates
    existing = set()
    for row in conn.execute(
        "SELECT date FROM observations WHERE series_id = ?", (series_id,)
    ).fetchall():
        existing.add(row[0])

    new_pts = [(d, v) for d, v in data.items() if d not in existing]

    if new_pts and not dry_run:
        for d, v in new_pts:
            conn.execute(
                """INSERT OR REPLACE INTO observations (series_id, date, value, as_of_date, imported_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (series_id, d, v, d, imported_at),
            )

    return len(new_pts)


# ── main ───────────────────────────────────────────────────────────────

def recompute_all(db_path, dry_run=False, verbose=False, category=None):
    imported_at = datetime.now().isoformat(timespec="seconds")

    with open_db(db_path) as conn:
        # Ensure all derived series have rows in series table
        for sid, display_name, unit, cat in DERIVED_SERIES:
            if category and cat != category:
                continue
            ensure_series_row(conn, sid, display_name, unit, imported_at)

        total_inserted = 0

        if not category or category == "decomp":
            if verbose:
                log("Computing FX decomposition...")
            count = compute_decomp(conn, imported_at, dry_run, verbose)
            total_inserted += count
            if verbose:
                log(f"  Decomp: {count} new observations", "OK" if count > 0 else "WARN")

        if not category or category == "hedge":
            if verbose:
                log("Computing hedge costs & annualized...")
            count = compute_hedge_costs(conn, imported_at, dry_run, verbose)
            total_inserted += count
            if verbose:
                log(f"  Hedge costs: {count} new observations", "OK" if count > 0 else "WARN")

        if not dry_run and total_inserted > 0:
            conn.commit()
            log(f"Committed {total_inserted} new derived observations", "OK")
        elif dry_run:
            log(f"[DRY RUN] Would insert {total_inserted} derived observations", "WARN")
        else:
            log("No new derived observations to insert")

    return total_inserted


def main():
    parser = argparse.ArgumentParser(description="Recompute FX derived series from raw data")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="Path to SQLite database")
    parser.add_argument("--dry-run", action="store_true", help="Dry run, no writes")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--category", choices=["decomp", "hedge"],
                        help="Only recompute one category")
    args = parser.parse_args()

    if not Path(args.db).exists():
        log(f"Database not found: {args.db}", "ERROR")
        sys.exit(1)

    log("Martin Morning Brief — recompute_fx_derived.py")
    if args.dry_run:
        log("Mode: DRY RUN (no writes)", "WARN")
    if args.category:
        log(f"Category: {args.category}")

    count = recompute_all(args.db, dry_run=args.dry_run, verbose=args.verbose,
                          category=args.category)
    log(f"=== Recompute Summary ===")
    log(f"New derived obs: {count}")


if __name__ == "__main__":
    main()
