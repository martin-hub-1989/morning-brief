#!/usr/bin/env python3
import argparse
import json
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "morning_brief.sqlite"
DEFAULT_PLAN = ROOT / "data" / "update_plan.json"


def next_date(last_date, frequency):
    d = datetime.fromisoformat(last_date).date()
    if frequency == "M":
        if d.month == 12:
            return date(d.year + 1, 1, 31)
        first_next = date(d.year, d.month + 1, 1)
        if first_next.month == 12:
            first_after = date(first_next.year + 1, 1, 1)
        else:
            first_after = date(first_next.year, first_next.month + 1, 1)
        return first_after - timedelta(days=1)
    return d + timedelta(days=1)


def recent_observation_dates(conn, series_id, limit=2):
    rows = conn.execute(
        """
        SELECT date
        FROM observations
        WHERE series_id = ?
        ORDER BY date DESC
        LIMIT ?
        """,
        (series_id, limit),
    ).fetchall()
    return [row[0] for row in reversed(rows)]


def build_update_plan(db_path):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        """
        SELECT s.series_id, s.display_name, s.sheet_name, s.frequency, s.unit,
               s.source_code, s.update_method, MAX(o.date) AS last_date
        FROM series s
        LEFT JOIN observations o ON o.series_id = s.series_id
        WHERE s.active = 1
        GROUP BY s.series_id
        ORDER BY s.sheet_name, s.series_id
        """
    ).fetchall()

    today = date.today()
    items = []
    for sid, name, sheet, freq, unit, source_code, update_method, last_date in rows:
        validation_dates = recent_observation_dates(conn, sid)
        if not last_date:
            start = None
            fetch_start = None
            status = "needs_full_history"
        else:
            start_date = next_date(last_date, freq)
            start = start_date.isoformat()
            fetch_start = validation_dates[0] if validation_dates else start
            status = "up_to_date" if start_date > today else "needs_update"
        items.append(
            {
                "series_id": sid,
                "display_name": name,
                "sheet_name": sheet,
                "frequency": freq,
                "unit": unit,
                "source_code": source_code,
                "update_method": update_method,
                "last_date": last_date,
                "next_start_date": start,
                "fetch_start_date": fetch_start,
                "validation_dates": validation_dates,
                "status": status,
            }
        )
    conn.close()
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target_date": today.isoformat(),
        "note": "Incremental update plan. Fetch scripts start from fetch_start_date and compare validation_dates before appending new observations.",
        "items": items,
    }


def main():
    parser = argparse.ArgumentParser(description="Create an incremental update plan for local series.")
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--output", default=str(DEFAULT_PLAN))
    args = parser.parse_args()
    plan = build_update_plan(Path(args.db))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

    counts = {}
    for item in plan["items"]:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    print(f"Wrote {output}")
    print(json.dumps(counts, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
