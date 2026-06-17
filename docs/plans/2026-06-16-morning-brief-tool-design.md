# Morning Brief Tool Design

## Problem

The workspace has a seed workbook with historical market data. The tool should turn that workbook into a reusable local system that can:

- keep historical time series locally,
- update only the missing dates later,
- generate a daily visual market brief,
- allow charts and metrics to expand through configuration.

## Selected Approach

Build a small local data and reporting tool first. A Codex skill can be added later as an operating manual, but it should not be the core data store or chart engine.

## Technical Decisions

- Store cleaned observations in `data/morning_brief.sqlite`.
- Keep chart definitions in `config/charts.json`.
- Keep the current seed input as `20260616-Morning Brief Skill.xlsx`.
- Generate a self-contained HTML report under `reports/`.
- Use Python standard library plus pandas/openpyxl, which are already available in the workspace runtime.
- Keep MCP data updates behind an explicit updater script until source mappings are confirmed.

## Data Shape

`series`

- `series_id`
- `display_name`
- `sheet_name`
- `frequency`
- `unit`
- `source_name`
- `source_code`

`observations`

- `series_id`
- `date`
- `value`
- `as_of_date`
- `imported_at`

The primary key is `(series_id, date)`, so imports and updates are idempotent.

## First Version Scope

- Import the existing workbook.
- Generate charts from local history.
- Provide a dry-run update plan that identifies the next required start date for each series.
- Leave live MCP fetching as the next step after source and cadence confirmation.

## Open Decisions

- Which series should be fetched from Wind, public web, or another MCP.
- Whether zero values in some non-trading-day rows should be treated as real data or missing values for each market.
- Which valuation percentiles should use all history vs rolling 10-year windows.
- Whether the final daily artifact should stay as HTML, become an email, or become a hosted dashboard.
