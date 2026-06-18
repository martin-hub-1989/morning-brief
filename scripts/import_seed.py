#!/usr/bin/env python3
import argparse
import math
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_XLSX = ROOT / "seed" / "seed.xlsx"
DEFAULT_DB = ROOT / "data" / "morning_brief.sqlite"

SHEET_PREFIX = {
    "走势图": "trend",
    "PE TTM": "pe_ttm",
    "PB LF": "pb_lf",
    "股息率": "dividend_yield",
}

SHEET_FREQUENCY = {
    "走势图": "D",
    "PE TTM": "D",
    "PB LF": "D",
    "股息率": "D",
}

DISPLAY_ALIASES = {
    # A股价格指数
    "881001.WI": "万得全A",
    "000001.SH": "上证指数",
    "000510.SH": "中证A500",
    "000300.SH": "沪深300",
    "000905.SH": "中证500",
    "000852.SH": "中证1000",
    "932000.CSI": "中证2000",
    "000922.CSI": "中证红利",
    "399006.SZ": "创业板指",
    "000688.SH": "科创50",
    # 港股价格指数
    "HSI.HI": "恒生指数",
    "HSTECH.HI": "恒生科技",
    "HSHDYI.HI": "恒生高股息率",
    # 美股价格指数
    "IXIC.GI": "纳斯达克指数",
    "SPX.GI": "标普500",
    # 商品
    "NH0100.NHF": "南华商品指数",
    # A股全收益指数
    "H00300.CSI": "300收益",
    "H00905.CSI": "500收益",
    "H00852.SH": "中证1000全收益",
    "399606.SZ": "创业板R",
    "000688CNY01.SH": "科创50(全)",
    "H00922.CSI": "中证红利全收益",
    # 中信风格指数
    "CI005917.WI": "金融(风格.中信)",
    "CI005918.WI": "周期(风格.中信)",
    "CI005919.WI": "消费(风格.中信)",
    "CI005920.WI": "成长(风格.中信)",
    "CI005921.WI": "稳定(风格.中信)",
    # 港股全收益
    "HSIRH.HI": "恒生指数R",
    "HSTECHT.HI": "恒生科技R",
    "HSI52.HI": "恒生高股息率R",
    # 美股全收益
    "SP500TR.SPI": "标普500全收益指数",
    "XCMP.GI": "纳斯达克总回报指数",
    # FX — row 1 有 CFETS 来源描述，row 2 是 Bloomberg 代码
    "USDCNY.IB": "USDCNY",
    "USDJPY.IB": "USDJPY",
    "USDHKD.IB": "USDHKD",
    "GBPCNY.IB": "GBPCNY",
    "AUDCNY.IB": "AUDCNY",
    "USDX.FX": "DXY",
    "CNYX.IB": "CNYX",
    # 商品 — row 1 有来源描述
    "Au9999.SGE": "AUXCNY",
    "SPTAUUSDOZ.IDC": "伦敦金现",
    "B.IPE": "ICE布油",
}


def clean_text(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value).strip().replace("\t", "")


def is_date(value):
    if isinstance(value, pd.Timestamp):
        return True
    parsed = pd.to_datetime(value, errors="coerce")
    return not pd.isna(parsed)


def first_data_row(raw):
    for idx, value in raw.iloc[:, 0].items():
        if is_date(value):
            return idx
    raise ValueError("No date column found")


def infer_unit(sheet, display, source):
    text = f"{display} {source}"
    if sheet == "PE TTM":
        return "multiple"
    if sheet == "PB LF":
        return "multiple"
    if sheet == "股息率":
        return "percent"
    if "收益率" in text or "债" in display:
        return "percent_point"
    if any(x in display for x in ["USDCNY", "USDJPY", "USDHKD", "GBPCNY", "AUDCNY"]):
        return "fx"
    if any(x in display for x in ["金", "油"]):
        return "price"
    return "index"


def display_name(sheet, col, raw, data_start):
    """Extract canonical display name from column headers.

    Excel structure: row 0 = display name (may be None), row 1 = source description,
    row 2 = indicator code. For columns where row 0 is empty and row 2 contains a
    Wind/Bloomberg code, use DISPLAY_ALIASES to get the canonical name.
    """
    top = clean_text(raw.iat[0, col]) if raw.shape[0] > 0 else ""
    second = clean_text(raw.iat[1, col]) if raw.shape[0] > 1 else ""
    source = clean_text(raw.iat[2, col]) if raw.shape[0] > 2 and data_start >= 3 else ""

    if top and top not in {"error!", "Wind"}:
        return top

    # If no display name in row 0, check DISPLAY_ALIASES by indicator code first
    if not top and source:
        alias = DISPLAY_ALIASES.get(source)
        if alias:
            return alias

    if second and second not in {"error!", "Wind"}:
        return second

    return DISPLAY_ALIASES.get(source, source or f"column_{col}")


def source_name(sheet, col, raw, data_start):
    """Extract source/indicator code from row 2."""
    if data_start >= 3 and raw.shape[0] > 2:
        return clean_text(raw.iat[2, col])
    return clean_text(raw.iat[data_start - 1, col])


def setup_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS series (
            series_id TEXT PRIMARY KEY,
            display_name TEXT NOT NULL,
            sheet_name TEXT NOT NULL,
            frequency TEXT NOT NULL,
            unit TEXT NOT NULL,
            source_name TEXT,
            source_code TEXT,
            active INTEGER NOT NULL DEFAULT 1,
            update_method TEXT NOT NULL DEFAULT 'manual',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS observations (
            series_id TEXT NOT NULL,
            date TEXT NOT NULL,
            value REAL NOT NULL,
            as_of_date TEXT,
            imported_at TEXT NOT NULL,
            PRIMARY KEY (series_id, date),
            FOREIGN KEY (series_id) REFERENCES series(series_id)
        );

        CREATE VIEW IF NOT EXISTS latest_observations AS
        SELECT o.series_id, s.display_name, s.sheet_name, s.frequency, s.unit,
               o.date, o.value
        FROM observations o
        JOIN series s ON s.series_id = o.series_id
        JOIN (
            SELECT series_id, MAX(date) AS date
            FROM observations
            GROUP BY series_id
        ) latest
          ON latest.series_id = o.series_id AND latest.date = o.date;
        """
    )


def import_workbook(path, db_path, replace=False):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if replace and db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    setup_db(conn)
    imported_at = datetime.now().isoformat(timespec="seconds")
    total_series = 0
    total_obs = 0

    xls = pd.ExcelFile(path)
    for sheet in xls.sheet_names:
        if sheet not in SHEET_PREFIX:
            continue
        raw = pd.read_excel(path, sheet_name=sheet, header=None)
        start = first_data_row(raw)
        dates = pd.to_datetime(raw.iloc[start:, 0], errors="coerce")
        prefix = SHEET_PREFIX[sheet]
        frequency = SHEET_FREQUENCY[sheet]

        for col in range(1, raw.shape[1]):
            values = pd.to_numeric(raw.iloc[start:, col], errors="coerce")
            if values.dropna().empty:
                continue

            label = display_name(sheet, col, raw, start)
            source = source_name(sheet, col, raw, start)
            series_id = f"{prefix}:{label}"
            now = imported_at
            unit = infer_unit(sheet, label, source)

            conn.execute(
                """
                INSERT INTO series (
                    series_id, display_name, sheet_name, frequency, unit,
                    source_name, source_code, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(series_id) DO UPDATE SET
                    display_name=excluded.display_name,
                    sheet_name=excluded.sheet_name,
                    frequency=excluded.frequency,
                    unit=excluded.unit,
                    source_name=excluded.source_name,
                    source_code=excluded.source_code,
                    updated_at=excluded.updated_at
                """,
                (series_id, label, sheet, frequency, unit, source, source, now, now),
            )
            total_series += 1

            for date_value, value in zip(dates, values):
                if pd.isna(date_value) or pd.isna(value):
                    continue
                conn.execute(
                    """
                    INSERT INTO observations (series_id, date, value, as_of_date, imported_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(series_id, date) DO UPDATE SET
                        value=excluded.value,
                        as_of_date=excluded.as_of_date,
                        imported_at=excluded.imported_at
                    """,
                    (
                        series_id,
                        date_value.date().isoformat(),
                        float(value),
                        date_value.date().isoformat(),
                        imported_at,
                    ),
                )
                total_obs += 1

    conn.commit()
    conn.close()
    return total_series, total_obs


def main():
    parser = argparse.ArgumentParser(description="Import seed Excel data into the local morning brief database.")
    parser.add_argument("--xlsx", default=str(DEFAULT_XLSX))
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Rebuild the target database from the workbook instead of merging into existing series.",
    )
    args = parser.parse_args()

    series_count, obs_count = import_workbook(Path(args.xlsx), Path(args.db), replace=args.replace)
    print(f"Imported {series_count} series and {obs_count} observations into {args.db}")


if __name__ == "__main__":
    main()
