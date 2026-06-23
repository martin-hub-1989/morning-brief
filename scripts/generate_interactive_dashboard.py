#!/usr/bin/env python3
import argparse
import glob
import json
import math
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from lib import ROOT, DEFAULT_DB, open_db

DEFAULT_OUTPUT = ROOT / "output" / "interactive_dashboard.html"
DOCS_OUTPUT = ROOT / "docs" / "index.html"
TEMPLATE_PATH = ROOT / "templates" / "dashboard.html"

TREND_SERIES = [
    "trend:美-10债", "trend:中-10债", "trend:日-10债", "trend:英-10债", "trend:德-10债",
    "trend:法-10债", "trend:意-10债", "trend:澳-10债", "trend:中-30债", "trend:日-30债",
    "trend:美-30债", "trend:中-2债", "trend:美-2债",
    "trend:USDCNY", "trend:USDJPY", "trend:USDHKD", "trend:GBPCNY", "trend:AUDCNY",
    "trend:DXY", "trend:CNYX",
    "trend:万得全A", "trend:上证指数", "trend:中证A500", "trend:沪深300", "trend:中证500",
    "trend:中证1000", "trend:中证2000", "trend:中证红利", "trend:创业板指", "trend:科创50",
    "trend:恒生高股息率", "trend:恒生指数", "trend:恒生科技", "trend:纳斯达克指数", "trend:标普500",
    "trend:AUXCNY", "trend:伦敦金现", "trend:ICE布油", "trend:南华商品指数",
]

RETURN_SERIES = [
    {"series_id": "trend:300收益", "region": "A股"},
    {"series_id": "trend:500收益", "region": "A股"},
    {"series_id": "trend:中证1000全收益", "region": "A股"},
    {"series_id": "trend:创业板R", "region": "A股"},
    {"series_id": "trend:科创50(全)", "region": "A股"},
    {"series_id": "trend:中证红利全收益", "region": "A股"},
    {"series_id": "trend:恒生指数R", "region": "港股"},
    {"series_id": "trend:恒生科技R", "region": "港股"},
    {"series_id": "trend:恒生高股息率R", "region": "港股"},
    {"series_id": "trend:标普500全收益指数", "region": "美股"},
    {"series_id": "trend:纳斯达克总回报指数", "region": "美股"},
    {"series_id": "trend:AUXCNY", "region": "商品"},
    {"series_id": "trend:伦敦金现", "region": "商品"},
    {"series_id": "trend:ICE布油", "region": "商品"},
    {"series_id": "trend:南华商品指数", "region": "商品"},
    {"series_id": "trend:金融(风格.中信)", "region": "A股风格"},
    {"series_id": "trend:周期(风格.中信)", "region": "A股风格"},
    {"series_id": "trend:消费(风格.中信)", "region": "A股风格"},
    {"series_id": "trend:成长(风格.中信)", "region": "A股风格"},
    {"series_id": "trend:稳定(风格.中信)", "region": "A股风格"},
    {"series_id": "trend:万得全A", "region": "A股风格"},
]

EMOTION_SERIES = [
    "htsc:A股情绪指数",
    "htsc:港股情绪指数",
]

CAPITAL_SERIES = [
    "htsc:ETF资金",
    "htsc:融资资金",
    "htsc:公募基金",
    "htsc:散户资金净流入",
    "htsc:产业资本减持",
    "htsc:一级市场",
]

RATE_SERIES = [
    "trend:美-10债", "trend:中-10债", "trend:日-10债", "trend:英-10债", "trend:德-10债",
    "trend:法-10债", "trend:意-10债", "trend:澳-10债", "trend:中-30债", "trend:日-30债",
    "trend:美-30债", "trend:中-2债", "trend:美-2债",
]

FX_SERIES = [
    {"series_id": "trend:USDCNY", "group": "兑人民币"},
    {"series_id": "trend:GBPCNY", "group": "兑人民币"},
    {"series_id": "trend:AUDCNY", "group": "兑人民币"},
    {"series_id": "trend:USDJPY", "group": "美元交叉"},
    {"series_id": "trend:USDHKD", "group": "美元交叉"},
    {"series_id": "trend:DXY", "group": "指数"},
    {"series_id": "trend:CNYX", "group": "指数"},
]

SPREAD_SERIES = [
    "trend:中-10债", "trend:美-10债", "trend:中-2债", "trend:美-2债", "trend:USDCNY"
]

FX_FIXING_SERIES = [
    "fx:usdcny-fixing",
    "fx:usdcny-spot",
    "fx:decomp-night-20d",
    "fx:decomp-day-20d",
]

FX_COST_SERIES = [
    "fx:cny-hedge-1m", "fx:cny-hedge-3m", "fx:cny-hedge-6m", "fx:cny-hedge-1y",
    "fx:cnh-hedge-1m", "fx:cnh-hedge-3m", "fx:cnh-hedge-6m", "fx:cnh-hedge-1y",
    "fx:cny-hedge-ann-1m", "fx:cny-hedge-ann-3m", "fx:cny-hedge-ann-6m", "fx:cny-hedge-ann-1y",
    "fx:cnh-hedge-ann-1m", "fx:cnh-hedge-ann-3m", "fx:cnh-hedge-ann-6m", "fx:cnh-hedge-ann-1y",
    "fx:cny-bond-1y", "fx:usd-bond-1y",
]

VALUATION_PREFIXES = {
    "PE TTM": "pe_ttm",
    "PB LF": "pb_lf",
    "股息率": "dividend_yield",
}


def fill_zero_with_previous(points):
    filled = []
    previous = None
    for d, value in points:
        if value is None or math.isnan(value):
            continue
        if value == 0:
            if previous is None:
                continue
            value = previous
        else:
            previous = value
        filled.append([d, value])
    return filled


def load_metadata(conn, series_ids):
    if not series_ids:
        return {}
    q = ",".join("?" for _ in series_ids)
    rows = conn.execute(
        f"""
        SELECT series_id, display_name, sheet_name, frequency, unit, source_code
        FROM series
        WHERE series_id IN ({q})
        """,
        series_ids,
    ).fetchall()
    return {
        sid: {
            "display_name": name,
            "sheet_name": sheet,
            "frequency": freq,
            "unit": unit,
            "source_code": code,
        }
        for sid, name, sheet, freq, unit, code in rows
    }


def load_observations(conn, series_ids):
    if not series_ids:
        return {}
    q = ",".join("?" for _ in series_ids)
    rows = conn.execute(
        f"""
        SELECT series_id, date, value
        FROM observations
        WHERE series_id IN ({q})
        ORDER BY series_id, date
        """,
        series_ids,
    ).fetchall()
    grouped = {}
    for sid, d, value in rows:
        grouped.setdefault(sid, []).append((d, float(value)))
    # 零值不再预填，保留原始数据。
    # 单一指标图：跳过零值（视为当日无数据，避免影响后续均线）。
    # 双指标图：在 JS 层按需前向填补，保证两条序列时间轴对齐。
    return grouped


def load_valuation_options(conn):
    rows = conn.execute(
        """
        SELECT series_id, display_name, sheet_name
        FROM series
        WHERE sheet_name IN ('PE TTM', 'PB LF', '股息率')
          AND active = 1
          AND display_name != '万得全A指数:收盘价'
        ORDER BY display_name, sheet_name
        """
    ).fetchall()
    options = {}
    series_ids = []
    for sid, name, sheet in rows:
        options.setdefault(name, {})[sheet] = sid
        series_ids.append(sid)
    complete = [
        {"display_name": name, "metrics": metrics}
        for name, metrics in options.items()
        if set(metrics.keys()) == set(VALUATION_PREFIXES.keys())
    ]
    complete.sort(key=lambda item: item["display_name"])
    return complete, series_ids


# 美元超级周期：三个历史峰值基准日期
_SUPER_CYCLE_BASE = {
    "1985": "1985-03-31",
    "2002": "2002-02-28",
    "2025": "2025-01-31",
}


def extract_super_cycle_data(conn):
    """从 DB 读取归一化超级周期数据，转换为图表格式。

    Returns dict with {t_labels, dxy: {1985,2002,2025}, dae: {1985,2002,2025}}
    or None if data not available.
    """
    dxy = {"1985": [], "2002": [], "2025": []}
    dae = {"1985": [], "2002": [], "2025": []}
    max_len = 0

    for cycle in ("1985", "2002", "2025"):
        for prefix, target in [("dxy", dxy), ("dae", dae)]:
            sid = f"super_cycle:{prefix}_{cycle}"
            rows = conn.execute(
                "SELECT date, value FROM observations WHERE series_id = ? ORDER BY date",
                (sid,)
            ).fetchall()
            values = [round(float(r[1]), 2) for r in rows]
            target[cycle] = values
            max_len = max(max_len, len(values))

    if max_len == 0:
        return None

    t_labels = [f"T+{i}" for i in range(max_len)]
    return {"t_labels": t_labels, "dxy": dxy, "dae": dae}


def build_payload(db_path):
    with open_db(db_path) as conn:
        valuation_options, valuation_series = load_valuation_options(conn)
        return_items = [item["series_id"] for item in RETURN_SERIES]
        fx_items = [item["series_id"] for item in FX_SERIES]
        all_series = sorted(set(TREND_SERIES + return_items + RATE_SERIES + fx_items + SPREAD_SERIES + valuation_series + EMOTION_SERIES + CAPITAL_SERIES + FX_FIXING_SERIES + FX_COST_SERIES))
        meta = load_metadata(conn, all_series)
        observations = load_observations(conn, all_series)
        super_cycle = extract_super_cycle_data(conn)

    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "source": {
            "database": str(db_path),
            "zero_rule": "0 values are treated as missing and forward-filled from the previous valid observation for charts and calculations.",
        },
        "meta": meta,
        "observations": observations,
        "trend_options": [
            {"series_id": sid, **meta[sid]} for sid in TREND_SERIES if sid in meta
        ],
        "return_options": [
            {"series_id": item["series_id"], "region": item["region"], **meta[item["series_id"]]}
            for item in RETURN_SERIES
            if item["series_id"] in meta
        ],
        "rate_options": [
            {"series_id": sid, **meta[sid]} for sid in RATE_SERIES if sid in meta
        ],
        "fx_options": [
            {"series_id": item["series_id"], "group": item["group"], **meta[item["series_id"]]}
            for item in FX_SERIES
            if item["series_id"] in meta
        ],
        "valuation_options": valuation_options,
        "valuation_metrics": list(VALUATION_PREFIXES.keys()),
        "fx_fixing_options": [
            {"series_id": sid, **meta[sid]} for sid in FX_FIXING_SERIES if sid in meta
        ],
        "fx_cost_options": [
            {"series_id": sid, **meta[sid]} for sid in FX_COST_SERIES if sid in meta
        ],
        "super_cycle": super_cycle,
    }


HTML_TEMPLATE = None  # Lazy-loaded from TEMPLATE_PATH on first render


def _find_world_html():
    """Find the latest Global News Report HTML and return its content (with widened layout).
    Returns empty string if no report found.
    """
    candidates = []
    for pattern in [
        str(ROOT / "Global News Report-*.html"),
        str(ROOT / "output" / "Global News Report-*.html"),
        str(Path.home() / ".claude" / "output" / "Global News Report-*.html"),
        str(Path.home() / "Desktop" / "Global News Report-*.html"),
    ]:
        candidates.extend(glob.glob(pattern))

    if not candidates:
        print("[dashboard] 看世界 report not found — run @global-news-report skill first")
        return ""

    latest = max(candidates, key=os.path.getmtime)
    content = Path(latest).read_text(encoding="utf-8")
    # Widen the container for better iframe display
    content = re.sub(r'max-width:\s*\d+px', 'max-width: 100%', content, count=1)
    # Escape </script> so it won't prematurely close the outer <script type="text/html"> wrapper.
    # The dashboard JS initWorldView() reverses this before creating the Blob URL.
    content = content.replace('</script>', '<\\/script>')
    print(f"[dashboard] 看世界 report: {os.path.basename(latest)} → inline (layout widened)")
    return content


def _prune_old_reports(keep=7):
    """Keep only the most recent `keep` Global News Reports in output/; delete older ones.
    Sorted by the YYYYMMDD in the filename (descending) so calendar order wins regardless of mtime.
    """
    reports = glob.glob(str(ROOT / "output" / "Global News Report-*.html"))
    if len(reports) <= keep:
        return

    def date_key(path):
        m = re.search(r'(\d{8})', os.path.basename(path))
        return m.group(1) if m else "00000000"

    reports.sort(key=date_key, reverse=True)
    for old in reports[keep:]:
        try:
            os.remove(old)
            print(f"[dashboard] 清理旧报告: {os.path.basename(old)}")
        except OSError as e:
            print(f"[dashboard] 无法删除 {os.path.basename(old)}: {e}")


def render_dashboard(db_path, output_path):
    global HTML_TEMPLATE
    if HTML_TEMPLATE is None:
        HTML_TEMPLATE = TEMPLATE_PATH.read_text(encoding="utf-8")

    # Find and embed the global news report (inline, so the dashboard is self-contained)
    world_html = _find_world_html()

    payload = build_payload(db_path)
    html = HTML_TEMPLATE.replace("__DATA__", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    html = html.replace("__WORLD_HTML__", world_html)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")

    # Also copy to docs/ for GitHub Pages deployment
    docs_path = DOCS_OUTPUT
    docs_path.parent.mkdir(parents=True, exist_ok=True)
    docs_path.write_text(html, encoding="utf-8")

    # Keep only the most recent 7 daily reports in output/
    _prune_old_reports(keep=7)

    return output_path


def main():
    parser = argparse.ArgumentParser(description="Generate the interactive Martin Morning Brief dashboard.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()
    output = render_dashboard(Path(args.db), Path(args.output))
    print(f"Generated {output}")


if __name__ == "__main__":
    main()