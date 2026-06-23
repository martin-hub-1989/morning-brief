---
name: morning-brief
description: 运行 Daily Morning Brief 每日流水线 — 先生成全球新闻报告(看世界模块)，再从同花顺 EDB + Wind MCP 拉取最新市场数据，复算外汇衍生序列，更新本地 SQLite 数据库，生成交互式 HTML 看板。触发词：早报、morning brief、更新看板、run daily。
---

# Daily Morning Brief — Multi Asset Dashboard

本地金融市场早报和交互看板工具。一键执行每日数据更新流程（含「看世界」全球新闻模块）。

> 🌐 **在线看板**：[martin-hub-1989.github.io/morning-brief](https://martin-hub-1989.github.io/morning-brief/) — GitHub Pages 自动部署

## 触发条件

用户提到以下任一关键词时激活：早报、morning brief、更新看板、run daily、fetch data、generate dashboard、晨报、run_daily。

## 项目位置

项目根目录即本仓库的顶层文件夹。首次克隆后，先用 `cd` 进入该目录再执行后续命令。

> **同步规则**：本文件是 skill 的权威副本（canonical copy）。对 skill 的任何修改都应更新此文件，然后同步到 skills 目录。
> ```bash
> cp SKILL.md ~/.claude/skills/morning-brief/SKILL.md
> ```

---

## 标准流程

### 0. 生成全球新闻报告（看世界模块）⚠️ 必须首先执行

**此步骤生成当天的 `Global News Report-YYYYMMDD.html`，后续看板生成步骤会自动将其嵌入「看世界」模块。**

执行方式：运行 `@global-news-report` skill，或手动按以下步骤操作。

> **如果你只想更新市场数据而不更新新闻**，可以跳过此步骤。看板会继续使用最近一次生成的新闻报告。

---

<!-- ═══════════════════════════════════════════════════════════════════ -->
<!-- NINA:START v2.3.1 — 来源: https://github.com/xk2133/global-news-report -->
<!--                                                                      -->
<!-- ⚠️ 更新原则（重要）：                                               -->
<!--   ✅ 跟随更新：Step 0A (Wind MCP 数据获取)、Step 0B (新闻搜索/筛选)、-->
<!--                Step 0D (质检清单)                                    -->
<!--   ❌ 保持不动：Step 0C (HTML 模板/CSS 样式) — 网页设计使用交互式     -->
<!--                看板的统一规范，不随 Nina 更新                        -->
<!--                                                                      -->
<!-- 更新方法：当 Nina 仓库更新后，将新版 SKILL.md 仅替换 Step 0A、0B、  -->
<!-- 0D 三部分，Step 0C 保持现有模板（仅在交互式看板统一升级设计规范时    -->
<!-- 才修改）。Wind MCP CLI 路径需适配本地环境（macOS / Windows）。       -->
<!--                                                                      -->
<!-- 原始仓库: git clone https://github.com/xk2133/global-news-report.git -->
<!-- 当前 Nina 版本: v2.3.1                                               -->
<!-- 本区块适配版本: v2.3.1-martin                                       -->
<!-- ═══════════════════════════════════════════════════════════════════ -->

### Global News Report (v2.3.1)

Generate a polished, self-contained HTML report with market data tables and bilingual news cards.

#### Output

- **File**: `output/Global News Report-YYYYMMDD.html`（生成到 output/，看板自动内联嵌入；仅滚动保留最近 7 天）
- **Title**: "Global Top News"
- **Display language**: English; cards bilingual (EN + 中文)
- **Color**: Up=Red `#d32f2f`, Down=Green `#2e7d32`

---

#### Step 0A: Market Data — Wind MCP Only

**CLI** — adapt path for your OS. On macOS:
```
node ~/.claude/skills/wind-mcp-skill/scripts/cli.mjs call {server} {tool} '{JSON params}'
```
On Windows (Nina's original):
```
node C:\Users\kongxy12\.claude\skills\wind-mcp-skill\scripts\cli.mjs call {server} {tool} '{JSON params}'
```
⚠️ Must use `spawnSync` in Node.js scripts (NOT `execSync`) to avoid shell quote escaping.

##### 0A-1: Indices + FX — `get_index_quote` (index_data)

Always available (24/7). Returns minute K-lines; extract last bar for daily close.

| # | Indicator | windcode | Extract from rows[] |
|---|-----------|----------|---------------------|
| 1 | Dow Jones | `DJI.GI` | `rows[-1][0]`=Close, `rows[-1][5]`=Date (`yyyyMMdd`) |
| 2 | S&P 500 | `SPX.GI` | same |
| 3 | Nasdaq | `IXIC.GI` | same |
| 4 | USD/CNY | `USDCNY.IB` | same |

> Note: quote returns MATCH (close), AVGPRICE, VOLUME, TURNOVER, TIME, _DATE columns.
> Day change % requires Open from `rows[0][0]`: `(Close - Open) / Open * 100`.
>
> **YTD data**: Fetch enough bars to cover from **{PREV_YEAR}-12-15** onward (or pass `beginDate`/`endDate` if supported). The last bar with a date ≤ `{PREV_YEAR}-12-31` is the YTD base.

##### 0A-2: Mag 7 Stocks — `get_global_stock_price_indicators` (global_stock_data)

Always available. Returns PrevClose → exact daily change %.

| # | Stock | windcode |
|---|-------|----------|
| 5 | Apple | `AAPL.O` |
| 6 | Microsoft | `MSFT.O` |
| 7 | Nvidia | `NVDA.O` |
| 8 | Amazon | `AMZN.O` |
| 9 | Meta | `META.O` |
|10 | Alphabet | `GOOGL.O` |
|11 | Tesla | `TSLA.O` |

Parse: `data.rows[0]` contains price, prevClose, change%, volume etc.

> **YTD base for stocks**: Fetch data for the **first trading day of the current year** separately. The `prevClose` in that day's response = the last trading day's close of the previous year. This `prevClose` IS the YTD denominator. Alternatively, fetch the year's full daily K-line and take the first bar's prevClose.

##### 0A-3: Macro/Commodity (Gold, WTI, DXY, US 10Y) — `get_economic_data` (economic_data)

**Single call covers all 4 indicators** — `economic_data` endpoint has broader coverage than `index_data` and is available 24/7 (EDB economic database).

```
node scripts/cli.mjs call economic_data get_economic_data \
  '{"metricIdsStr":"美元指数,伦敦现货黄金价格,NYMEX原油期货价格,美国国债收益率10年","freq":"日","beginDate":"{PREV_YEAR}1215","endDate":"{TODAY}"}'
```

**Response structure**: `content[0].text` → parse inner JSON → `data.date[]` + `data.indicatorInfo[]`.
Each indicator has `code`, `name`, `data[]` (same length as `date[]`, null = non-trading day).

**Target codes to filter** (ignore other indicators in the response):

| # | Indicator | Code | Name (中文) | Unit |
|---|-----------|------|-------------|------|
|12 | Gold | `S0031645` | 现货价(伦敦市场):黄金:美元 | USD/oz |
|13 | WTI Crude | `S0180938` | 期货结算价(活跃合约):NYMEX轻质原油 | USD/bbl |
|14 | DXY | `M0000271` | 美元指数 | index pts |
|15 | US 10Y | `G0000891` | 美国:国债收益率:10年 | % |

**Parsing**:
- `date[]` is **forward-chronological** (oldest first, newest last)
- Filter `indicatorInfo` for the 4 codes above, match by `code` field
- Strip nulls for trading-day-only series: `[(date[i], val) for i,val in enumerate(data) if val is not None]`
- Latest value = last non-null entry; YTD history = full array

**Don't use `count`** param — `economic_data` doesn't support it. Always pass `beginDate` + `endDate` (format: `yyyyMMdd`, no dashes).

##### 0A-4: YTD Calculation — CRITICAL

**⚠️ Unified methodology: YTD is anchored on the LAST trading day of the PREVIOUS year's close price.**
This is consistent with the main dashboard's `nearestOnOrBefore(series, "YYYY-01-01")` pattern (see `templates/dashboard.html`).
Industry standard (Bloomberg, Reuters) — covers the full calendar-year return including the year-end overnight gap.

###### YTD Formula (price/index indicators)

```
YTD% = (latest_value / prev_year_last_trading_day_value - 1) × 100
```

Where:
- `latest_value` = last non-null data point across the full series (current date)
- `prev_year_last_trading_day_value` = **last non-null data point whose date falls in the previous calendar year** (e.g. 2025-12-31 or earlier if that day is a holiday)

###### YTD Formula (US 10Y yield — basis points)

```
YTD_bp = (latest_yield - prev_year_last_trading_day_yield) × 100
```
Display as e.g. `+55bp` or `-12bp` (no % sign for yields).

###### Data fetching — extend backward into December

To capture the last trading day of the previous year, ALL data queries must start from **{PREV_YEAR}-12-15** (Dec 15 of the previous year), not Jan 1 of the current year:

| Source | Indicators | beginDate | YTD Base | Latest |
|--------|-----------|-----------|----------|--------|
| `get_index_kline` (index_data) | DJI, SPX, IXIC | `{PREV_YEAR}1215` | Last bar with date ≤ `{PREV_YEAR}-12-31` | `bars[-1]` close |
| `get_economic_data` (economic_data) | Gold, WTI, DXY, US 10Y | `{PREV_YEAR}1215` | Last non-null value with date ≤ `{PREV_YEAR}-12-31` | `data[-1][1]` |
| `get_global_stock_price_indicators` | Mag 7 | Latest + first-trading-day prevClose | `prevClose` from the first trading day of the current year = last trading day of previous year | Latest close |

**For stocks**: the `prevClose` field returned for the first trading day of the year IS the last trading day's close of the previous year. Use it as the YTD base.

###### YTD data extraction algorithm

1. Fetch data with `beginDate = {PREV_YEAR}1215`
2. Strip nulls (non-trading days)
3. Partition: `prev_year = [(d, v) for (d, v) in data if d[:4] == '{PREV_YEAR}']`
4. `prev_year_last_trading_day_value = prev_year[-1][1]` (last non-null of previous year)
5. `latest_value = data[-1][1]` (last non-null overall)
6. For stocks: `ytd_base = prevClose_from_first_trading_day_of_year_response`

###### Correct YTD sign conventions

- **Red (`#d32f2f`, class `up`)**: YTD > 0 (price went UP)
- **Green (`#2e7d32`, class `down`)**: YTD < 0 (price went DOWN)
- For US 10Y yield: up = yields rose (bearish bonds), down = yields fell (bullish bonds)

###### YTD Validation (DO NOT SKIP)

After computing all 15 YTD values, run these sanity checks before generating HTML:

1. **Gold YTD should be negative in 2026** (gold peaked ~$5,400 in Jan and has declined to ~$4,200-4,400 by mid-year)
2. **DXY YTD should be +1% to +3%** (started ~98.3, currently ~99-101)
3. **WTI YTD should be strongly positive** (started ~$57, currently ~$75-80, rally >30%)
4. **US 10Y YTD should be +20 to +35bp** (started ~4.18%, currently ~4.4-4.5%)
5. If any YTD value contradicts these ranges, **re-check your calculation before proceeding**

##### 0A-5: Fallback — WebFetch (P1)

Only when Wind MCP returns AUTH_ERROR or NETWORK_ERROR. Fetch individual MarketWatch pages:

| Indicator | URL suffix |
|-----------|-----------|
| DJIA | `marketwatch.com/investing/index/djia` |
| S&P 500 | `marketwatch.com/investing/index/spx` |
| Nasdaq | `marketwatch.com/investing/index/comp` |
| Gold | `marketwatch.com/investing/future/gold` |
| WTI | `marketwatch.com/investing/future/cl.1` |
| DXY | `marketwatch.com/investing/index/dxy` |
| US 10Y | `marketwatch.com/investing/bond/tmubmusd10y` |
| USD/CNY | `marketwatch.com/investing/currency/usdcny` |
| AAPL/MSFT/NVDA/etc | `marketwatch.com/investing/stock/{ticker}` |

**DO NOT use yfinance, Python scripts, Finnhub API, NewsAPI, or TradingEconomics.** Report data = last US trading day close. Wind EDB daily close is authoritative. Real-time sources (TradingEconomics etc.) reflect intraday prices for a different as-of date — never use them as primary source.

---

#### Step 0B: Collect News (WebSearch max 2 calls)

##### Call 1 — All 4 sections merged (`topic: "news"`)

Replace `{DATE}` with today's date:

| Group | Query |
|-------|-------|
| Financial | `"top financial markets stock economy news today {DATE}"` |
| Technology | `"top technology AI chips funding IPO news today {DATE}"` |
| Political | `"top political geopolitics trade diplomacy news today {DATE}"` |
| Other | `"world sports crypto defense highlights news today {DATE}"` |

Aim for 3-5 stories per section.

##### Call 2 — Gap Fill (only if section < 2 stories)

Targeted search for the deficient section.

##### WebFetch Follow-ups (max 2 calls)

Fetch full text for **1 lead story per section** only. Others use search snippets.

##### News Filtering Rules

**Source tiers**:

| Tier | Sources | Rule |
|------|---------|------|
| T1 | Reuters, AP, Bloomberg, BBC, **WSJ** | 政治板块只能用 T1 |
| T2 | CNBC, **CNN**, NYT, FT, MarketWatch, Barron's, The Information | 金融板块可用 |
| T3 | TechCrunch, Wired, The Verge, Ars Technica, VentureBeat | 仅科技板块可用 |
| BANNED | Aggregators/blogs (CoinStats, TechStartups, CoinTelegraph, ValueWalk, Business Insider tech), Seeking Alpha, Fortune, Forbes | Zero tolerance |

##### Section-level source rules

| Section | Allowed tiers | T1 minimum |
|---------|--------------|------------|
| Financial | **T1, T2 only** | ≥ 2 stories must be T1 |
| Technology | **T1, T2, T3** | — |
| Political | **T1 only** | All stories must be T1 |
| Other | T1, T2, T3 | — |

##### General rules

- All sources English-only (NO sina, qq, 36kr, eastmoney)
- Same event → merge into primary section, cite highest-tier source
- Recency hard limit: < 24 hours
- Each section designates 1 lead story (record-breaking, billion$, war, election, etc.)

---

<!-- ───────────────────────────────────────────────────── -->
<!-- ⚠️ 设计边界：以下 Step 0C 为 HTML/CSS 模板           -->
<!-- 使用交互式看板统一设计规范，不随 Nina 更新           -->
<!-- 仅在交互式看板整体升级设计时才修改此部分             -->
<!-- ───────────────────────────────────────────────────── -->

#### Step 0C: Generate HTML

##### Built-in Template (self-contained, no dependency on prior report)

Use the following full HTML template. Replace all `{PLACEHOLDERS}` with generated data.

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Global Top News — {DATE}</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
  * { margin:0; padding:0; box-sizing: border-box; }
  body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Microsoft YaHei', 'PingFang SC', sans-serif;
    background: #f8f9fa; color: #1a1a2e; line-height: 1.6; -webkit-font-smoothing: antialiased;
  }
  .container { max-width: 760px; margin: 0 auto; padding: 32px 20px; }
  .header { text-align: center; margin-bottom: 12px; padding-bottom: 20px; border-bottom: 2px solid #e9ecef; }
  .header .date { font-size: 13px; font-weight: 600; letter-spacing: 2px; text-transform: uppercase; color: #868e96; margin-bottom: 8px; }
  .header h1 { font-size: 28px; font-weight: 800; color: #1a1a2e; margin-bottom: 0; }
  .tab-bar { display: flex; gap: 4px; background: #fff; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,0.08); margin-bottom: 28px; padding: 4px; }
  .tab-bar button { flex: 1; text-align: center; padding: 13px 10px; font-size: 12px; font-weight: 700; letter-spacing: 1.2px; text-transform: uppercase; border-radius: 9px; border: none; cursor: pointer; font-family: inherit; white-space: nowrap; background: transparent; color: #868e96; transition: all 0.25s ease; outline: none; -webkit-tap-highlight-color: transparent; touch-action: manipulation; }
  .tab-bar button:hover { opacity: 0.85; }
  .tab-bar button .dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 6px; vertical-align: middle; margin-top: -2px; }
  .tab-bar button .label { vertical-align: middle; }
  .tab-bar button:not(.active)[data-tab="finance"]:hover { color: #e65100; background: #fff3e0; }
  .tab-bar button:not(.active)[data-tab="tech"]:hover { color: #1565c0; background: #e3f2fd; }
  .tab-bar button:not(.active)[data-tab="politics"]:hover { color: #c62828; background: #fce4ec; }
  .tab-bar button:not(.active)[data-tab="other"]:hover { color: #7b1fa2; background: #f3e5f5; }
  .tab-bar button.active { color: #fff; }
  .tab-bar button.active .dot { background: #fff !important; }
  .tab-bar button.active[data-tab="finance"] { background: #ff9800; }
  .tab-bar button.active[data-tab="tech"] { background: #2196f3; }
  .tab-bar button.active[data-tab="politics"] { background: #e91e63; }
  .tab-bar button.active[data-tab="other"] { background: #9c27b0; }
  button[data-tab="finance"] .dot { background: #ff9800; }
  button[data-tab="tech"] .dot { background: #2196f3; }
  button[data-tab="politics"] .dot { background: #e91e63; }
  button[data-tab="other"] .dot { background: #9c27b0; }
  .panel { display: none; }
  .panel.active { display: block; }
  .section-bar { display: flex; align-items: center; gap: 10px; margin-bottom: 20px; padding: 10px 16px; border-radius: 8px; font-size: 13px; font-weight: 700; letter-spacing: 1.5px; text-transform: uppercase; }
  .section-bar.finance { background: #fff3e0; color: #e65100; }
  .section-bar.tech { background: #e3f2fd; color: #1565c0; }
  .section-bar.politics { background: #fce4ec; color: #c62828; }
  .section-bar.other { background: #f3e5f5; color: #7b1fa2; }
  .market-table { width: 100%; border-collapse: collapse; margin-bottom: 16px; background: #fff; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.06); }
  .market-table th { background: #1a1a2e; color: #fff; font-size: 12px; font-weight: 600; padding: 10px 16px; text-align: left; white-space: nowrap; }
  .market-table td { padding: 9px 16px; font-size: 13px; border-bottom: 1px solid #f1f3f5; }
  .market-table tr:last-child td { border-bottom: none; }
  .market-table td.name { font-weight: 600; color: #1a1a2e; }
  .market-table td.num { font-family: 'Inter', monospace; font-weight: 500; text-align: right; }
  .market-table td.num.up { color: #d32f2f; }
  .market-table td.num.down { color: #2e7d32; }
  .sparkline-cell { width: 130px; text-align: center; }
  .card { background: #fff; border-radius: 12px; padding: 20px 24px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.06); border-left: 4px solid #dee2e6; transition: box-shadow 0.2s, transform 0.2s; }
  .card:hover { box-shadow: 0 4px 12px rgba(0,0,0,0.1); }
  .card.finance { border-left-color: #ff9800; }
  .card.tech { border-left-color: #2196f3; }
  .card.politics { border-left-color: #e91e63; }
  .card.other { border-left-color: #9c27b0; }
  .card .tag { font-size: 10px; font-weight: 700; letter-spacing: 1px; text-transform: uppercase; color: #adb5bd; margin-bottom: 6px; }
  .card h3 { font-size: 16px; font-weight: 700; color: #1a1a2e; margin-bottom: 8px; line-height: 1.4; }
  .card p { font-size: 14px; color: #495057; line-height: 1.6; }
  .cn-trans { margin-top: 12px; padding-top: 10px; border-top: 1px dashed #dee2e6; font-size: 13px; color: #6c757d; line-height: 1.7; }
  .cn-trans strong { display: block; font-weight: 600; color: #495057; margin-bottom: 4px; }
  .card .source { display: inline-block; margin-top: 10px; font-size: 11px; color: #adb5bd; font-style: italic; }
  .card .source a { color: #868e96; text-decoration: none; border-bottom: 1px dashed #ced4da; transition: color 0.2s, border-color 0.2s; }
  .card .source a:hover { color: #1a1a2e; border-bottom-color: #1a1a2e; }
  .footer { text-align: center; margin-top: 40px; padding-top: 20px; border-top: 1px solid #e9ecef; font-size: 12px; color: #adb5bd; line-height: 1.8; }
  .footer a { color: #868e96; text-decoration: none; border-bottom: 1px dashed #ced4da; }
  @media (max-width: 640px) {
    .tab-bar { flex-wrap: wrap; }
    .tab-bar button { min-width: calc(50% - 4px); font-size: 11px; padding: 10px 6px; }
    .market-table th, .market-table td { padding: 7px 10px; font-size: 12px; }
    .sparkline-cell { width: 80px !important; }
    .card { padding: 16px; }
    .card h3 { font-size: 15px; }
  }
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="date">{DAYOFWEEK}, {MONTH} {DD}, {YYYY}</div>
    <h1>Global Top News</h1>
  </div>
  <nav class="tab-bar" id="tabBar">
    <button class="active" data-tab="finance" onclick="switchTab('finance')">
      <span class="dot"></span><span class="label">Financial</span>
    </button>
    <button data-tab="tech" onclick="switchTab('tech')">
      <span class="dot"></span><span class="label">Technology</span>
    </button>
    <button data-tab="politics" onclick="switchTab('politics')">
      <span class="dot"></span><span class="label">Political</span>
    </button>
    <button data-tab="other" onclick="switchTab('other')">
      <span class="dot"></span><span class="label">Highlights</span>
    </button>
  </nav>

  <!-- ===== FINANCIAL PANEL ===== -->
  <div class="panel active" id="panel-finance">
    <div class="section-bar finance">Financial Markets</div>
    <table class="market-table">
      <thead><tr><th>Index</th><th>Close</th><th>Day Chg</th><th>YTD</th><th>YTD Trend</th></tr></thead>
      <tbody>{FINANCE_ROWS}</tbody>
    </table>
    {FINANCE_CARDS}
  </div>

  <!-- ===== TECHNOLOGY PANEL ===== -->
  <div class="panel" id="panel-tech">
    <div class="section-bar tech">Technology</div>
    <table class="market-table">
      <thead><tr><th>Stock</th><th>Close</th><th>Day Chg</th><th>YTD</th><th>YTD Trend</th></tr></thead>
      <tbody>{TECH_ROWS}</tbody>
    </table>
    {TECH_CARDS}
  </div>

  <!-- ===== POLITICAL PANEL ===== -->
  <div class="panel" id="panel-politics">
    <div class="section-bar politics">Politics &amp; Geopolitics</div>
    {POLITICS_CARDS}
  </div>

  <!-- ===== OTHER HIGHLIGHTS PANEL ===== -->
  <div class="panel" id="panel-other">
    <div class="section-bar other">Other Highlights</div>
    {OTHER_CARDS}
  </div>

  <div class="footer">
    Auto-generated by KK &middot; All sources in English &middot;
    <a href="https://apnews.com" target="_blank">AP News</a> &middot;
    <a href="https://www.cnbc.com" target="_blank">CNBC</a> &middot;
    <a href="https://www.marketwatch.com" target="_blank">MarketWatch</a> &middot;
    <a href="https://www.wsj.com" target="_blank">WSJ</a> &middot;
    <a href="https://www.bloomberg.com" target="_blank">Bloomberg</a> &middot;
    <a href="https://www.reuters.com" target="_blank">Reuters</a><br>
    As of {TIME} CST, {DATE_FULL} &middot; Market data as of {MARKET_DATE} close
  </div>
</div>
<script>
function switchTab(name) {
  document.querySelectorAll('.tab-bar button').forEach(function(b) { b.classList.remove('active'); });
  document.querySelectorAll('.panel').forEach(function(p) { p.classList.remove('active'); });
  document.querySelector('[data-tab="' + name + '"]').classList.add('active');
  var panel = document.getElementById('panel-' + name);
  if (panel) {
    panel.classList.add('active');
    panel.style.opacity = '0';
    panel.style.transition = 'opacity 0.2s ease';
    requestAnimationFrame(function() { panel.style.opacity = '1'; });
  }
}
</script>
</body>
</html>
```

**Tab panel mechanics**: `.panel { display:none } .panel.active { display:block }`. Default active = `#panel-finance`. Switch via `switchTab()` with 0.2s fade-in.

##### Market Table Row Template

```html
<tr>
  <td class="name">{NAME}</td>
  <td class="num">{PRICE}</td>
  <td class="num {up|down}">{DAY_CHG}</td>
  <td class="num {up|down}">{YTD_CHG}</td>
  <td class="sparkline-cell">{SPARKLINE_SVG}</td>
</tr>
```

##### Sparkline SVG Template

```svg
<svg viewBox="0 0 120 32" width="120" height="32">
  <defs><linearGradient id="g{ID}" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="{COLOR}" stop-opacity="0.25"/>
    <stop offset="100%" stop-color="{COLOR}" stop-opacity="0.02"/>
  </linearGradient></defs>
  <polygon fill="url(#g{ID})" points="0,30 {AREA_PTS} 120,30"/>
  <polyline fill="none" stroke="{COLOR}" stroke-width="1.5"
    stroke-linejoin="round" stroke-linecap="round" points="{LINE_PTS}"/>
</svg>
```
- Color: Red (`#d32f2f`) if YTD > 0, Green (`#2e7d32`) if YTD < 0
- Gradient ID unique per row (`gDow`, `gSp`, `gAapl`, etc.)
- Points: x=0..120, y mapped to min/max range within 0..30

##### News Card Template (bilingual)

```html
<div class="card {SECTION}">
  <div class="tag">{TAG}</div>
  <h3>{ENGLISH_HEADLINE}</h3>
  <p>{ENGLISH_SUMMARY}</p>
  <div class="cn-trans">
    <strong>{CN_HEADLINE}</strong><br>{CN_SUMMARY}
  </div>
  <div class="source">Source: <a href="{URL}" target="_blank">{SOURCE_NAME}</a></div>
</div>
```

- **Source line**: Display only source name(s) and link(s). Do NOT append tier labels (no "· T1/T2", "· T1", etc.). Example: `Source: Reuters · CNBC` — never `Source: Reuters · CNBC · T1/T2`.
- **TAG field**: Short keyword (CHIP REBOUND, WWDC 2026, etc.). Do NOT include tier info in tags.

---

#### Step 0D: Quality Checklist

- [ ] All 15 indicators via Wind MCP: 1A quote (4), 1B global_stock (7), 1C economic_data (4)
- [ ] economic_data response parsed: filter by codes (M0000271/S0031645/S0180938/G0000891), forward-chronological
- [ ] YTD sparkline data generated for all 15 indicators
- [ ] **YTD % validated**: Gold YTD ≈ negative in 2026, WTI YTD ≈ strongly positive (+25% to +35%), DXY YTD ≈ +1% to +3%
- [ ] Merged WebSearch for news (max 2 calls, gap fill if needed)
- [ ] Section source rules enforced: Financial (T1≥2 + T1/T2 only), Tech (T1/T2/T3), Politics (T1 only), Other (T1/T2/T3); no banned sources
- [ ] De-dup applied; all stories < 24h; each section has 1 lead story
- [ ] Built-in template used (CSS/layout self-contained, no dependency on prior report)
- [ ] Title exactly "Global Top News", file named `Global News Report-YYYYMMDD.html`
- [ ] All 8 financial indicators + 7 Mag 7 stocks present
- [ ] Sparkline colors match YTD direction; gradient IDs unique
- [ ] All source links clickable; every card has `.cn-trans` block
- [ ] No Chinese news sources used
- [ ] Source line has no tier labels; TAG has no tier info

<!-- ═══════════════════════════════════════════════════════════════════ -->
<!-- NINA:END v2.3.1 -->
<!-- ═══════════════════════════════════════════════════════════════════ -->

---

### 1. 确认环境

在项目根目录下执行：

```bash
cd "/Users/martin_ai/Desktop/Martin Morning Brief"
python3 -c "import sqlite3, json, urllib.request; print('OK')"
```

### 2. 执行每日流水线

```bash
python3 scripts/run_daily.py
```

这将依次运行脚本：

1. **update_data.py** — 扫描数据库，生成增量更新计划 (`data/update_plan.json`)
2. **fetch_data.py** — 从同花顺 EDB API 拉取日频数据，验证后写入 SQLite。**EDB 拉取失败时自动降级到 Wind MCP**（需该序列在 `wind_mapping.json` 中有映射）
3. **fetch_wind.py** — 从 Wind MCP 拉取外汇原始数据（CNH远期/掉期点）+ 全收益指数 + 估值
4. **recompute_fx_derived.py** — 从原始数据复算所有外汇衍生序列（汇率拆解+套保成本+年化），幂等
5. **fetch_emotion.py** — 从华泰智研 MCP 拉取市场情绪和资金面数据
6. **generate_interactive_dashboard.py** — 从 DB & `templates/dashboard.html` 生成 `output/interactive_dashboard.html` + `docs/index.html`（GitHub Pages）。同时自动查找最新的 Global News Report 并**内联嵌入**到看板 HTML（通过 Blob URL 注入 iframe），使看板文件完全自包含，单个文件即可共享给他人并正常显示「看世界」模块。

**EDB → Wind 自动切换**（新增）：当 `fetch_data.py` 从同花顺 EDB 拉取或验证失败时，自动检查 `config/wind_mapping.json` 是否有该序列的 Wind 映射。如有，则自动通过 Wind MCP 拉取，标记为 `wind_mcp_fallback`。无需人工干预。

如果 fetch 步骤部分序列验证失败，看板仍会用现有数据生成。

可选参数：
```bash
python3 scripts/run_daily.py --skip-fetch          # 跳过所有数据拉取
python3 scripts/run_daily.py --skip-fetch-ths      # 仅跳过同花顺 EDB
python3 scripts/run_daily.py --skip-fetch-wind     # 仅跳过 Wind MCP
python3 scripts/run_daily.py --skip-fetch-emotion  # 仅跳过华泰智研 MCP
```

### 3. 解读输出

关注各步骤的输出摘要：

```
=== THS EDB Fetch Summary ===
Targeted: 36 series    ← 需要更新的序列总数
Fetched:  36 series    ← 成功拉取（含 Wind 降级）
Passed:   36 ok        ← 验证通过
Wind fallback: 2       ← EDB 失败后自动切换 Wind 的序列数
Wind API calls: 4      ← 降级拉取消耗的 Wind 调用次数
New obs:  N observations

=== Wind Fetch Summary ===
Targeted: 38 series    ← Wind 负责的序列
Fetched:  38 series    ← 成功从 Wind 拉取
Wind API calls: 52     ← Wind 调用总次数
New obs:  N observations

=== Recompute FX Derived ===
New derived obs: N     ← 新增的复算观测（幂等，重复运行=0）

=== Dashboard ===
看世界 report: Global News Report-YYYYMMDD.html → inline (自包含，Blob URL 注入)
```

**THS EDB 跳过分类**：
- `skip_reason: 改用 Wind MCP` → 中信风格/德日债/GBPCNY/USDJPY，Wind 数据与 DB 一致
- `skip_reason: EDB ... 全收益` → 全收益指数 EDB 不支持，已由 Wind MCP 接管
- `skip_reason: EDB 无...` → EDB 无此指标（如万得全A），已由 Wind 接管

### 4. 报告结果

**每次执行完成后，必须向用户反馈以下信息：**

```
## Martin Morning Brief 每日更新完成

- 更新时间：<timestamp> CST
- 执行耗时：<HH:MM:SS>
- 全球新闻报告：已生成 / 复用最近报告（<filename>）

### 数据拉取
| 数据源 | 结果 |
|--------|------|
| 同花顺 EDB | X ok + Y partial, Z 失败 |
| EDB→Wind 自动切换 | N 个序列 (EDB 失败后自动降级) |
| Wind MCP (常规) | X ok, Y 失败 |
| 华泰智研情绪 | N 条新观测 |
| FX 衍生复算 | N 条新观测 |

### 资源消耗
| 指标 | 数值 |
|------|------|
| Wind API 调用 | N 次 (含 EDB 降级 + 常规拉取) |
| 本会话 Token | 约 N 万 |

- 看板文件：output/interactive_dashboard.html
```

**Wind 积分估算**（参考）：
- 每次 K-line 调用 ≈ 1 积分
- 每次 economic_data 调用 ≈ 1 积分
- 每次 get_index_fundamentals 调用 ≈ 1 积分
- 总消耗 = Wind API 调用次数 × 1 积分

如有新的验证失败（非预期），列出具体序列和差异值。

### 5. 打开看板

询问用户：

"是否需要我用浏览器打开看板？"

如用户同意，根据平台选择命令打开看板：

```bash
# macOS
open output/interactive_dashboard.html

# Windows
start output\interactive_dashboard.html

# Linux
xdg-open output/interactive_dashboard.html
```

### 6. 逐模块验证（必须执行）

**每次生成看板后，必须逐个模块检查内容是否正常显示。** 按以下顺序逐一验证：

| # | 模块 | 所在标签 | 验证要点 |
|---|------|---------|---------|
| 1 | 封面 | 默认 | 标题、日期、导航卡片完整 |
| 2 | 看世界 | 看世界 | 内联加载成功（Blob URL），新闻表格+卡片内容正常；若无当天报告则显示占位提示 |
| 3 | 走势看板 | 走势看板 | 图表+数据表正常，可选择不同指标和周期 |
| 4 | 股票涨跌 | 涨跌复盘 | 柱状图+表格正常，中信风格子图正常。区间选项含 Since 924（以 2024-09-23 收盘为基准，即 9/24 拐点前一日） |
| 5 | 利率涨跌 | 涨跌复盘 | 柱状图+表格正常。>1Y 时右侧显示年化 bp |
| 6 | 汇率涨跌 | 外汇看板 | 柱状图+表格正常，分组颜色正确。>1Y 时右侧显示年化涨跌幅 |
| 7 | 中美利差 | 外汇看板 | 10Y/2Y 两张图+表格正常 |
| 8 | 估值看板 | 权益看板 | 图表+均值/σ参考线+Z分数表正常 |
| 9 | 市场情绪 | 权益看板 | 情绪指数图+A股/港股对比图+表格正常。**若 HTSC 数据未拉取，模块显示友好提示而非空白** |
| 10 | 中间价 | 外汇看板 | 中间价vs即期图+汇率拆解图+表格正常 |
| 11 | 套保成本 | 外汇看板 | 期限结构图+3M时序图+1Y利差对比图+表格正常 |
| 12 | 专题图表 | 外汇看板 | DXY超级周期图+D/AE超级周期图+表格正常 |
| 13 | 数据口径 | 各标签底部 | 数据源说明文字显示正常 |

验证方法：
1. 打开看板后，**逐一**点击每个一级标签和二级标签
2. 确认每个模块的 **图表区域** 有 SVG 图表而非空白或「暂无数据」
3. 确认每个模块的 **数据表格** 有数据行
4. 如发现任何模块空白，立即排查原因并修复，修复后重新生成看板并**重新验证该模块**

常见故障排查：
- 图表空白 → 检查对应 `fetch_*.py` 步骤是否成功拉取该模块所需序列
- 超级周期图表空白 → 检查 `import_seed.py` 是否正确导入 `CYCLE_BASE_DATES`
- 看世界 iframe 占位或无当天报告 → 检查 Step 0 是否成功执行，报告是否生成在 output/
- 中间价/套保成本无数据 → 检查 `recompute_fx_derived.py` 是否成功运行
- **⚠️ 级联故障（关键）**：如果多个连续模块同时空白（尤其是外汇看板的后三个模块：中间价/套保成本/专题图表），很可能是某个**前面的模块**抛出了未捕获的 JS 错误导致 `init()` 中断。最常引发此问题的模块是**市场情绪**（`renderEmotion`），当 HTSC 情绪数据缺失时 `latestDate()` 返回 `null`，传入 `startForPeriod()` → `toDate(null)` 生成无效 Date → `toISO()` 调用 `toISOString()` 抛出 `RangeError: Invalid time value`。排查方法：检查浏览器控制台是否有 JS 错误，检查 `htsc:*` 系列数据是否存在。

## 补充命令

### 仅重新生成看板（不拉取数据）

```bash
python3 scripts/run_daily.py --skip-fetch
```

### 手动生成全球新闻报告（不跑完整流水线）

运行 `@global-news-report` skill，或手动执行 Step 0A–0D。

### 重建数据库（从 seed.xlsx）

```bash
python3 scripts/import_seed.py --replace
```

### 仅拉取数据（不生成看板）

```bash
python3 scripts/fetch_data.py
```

### 仅复算外汇衍生序列

```bash
python3 scripts/recompute_fx_derived.py --verbose
python3 scripts/recompute_fx_derived.py --dry-run
python3 scripts/recompute_fx_derived.py --category hedge   # 仅套保成本
python3 scripts/recompute_fx_derived.py --category decomp  # 仅汇率拆解
```

### 干跑验证（不写库）

```bash
python3 scripts/fetch_data.py --dry-run --verbose
```

### 测试单个序列

```bash
python3 scripts/fetch_data.py --series fx:usdcny-fixing --verbose
python3 scripts/fetch_wind.py --series fx:usdcnh-spot --verbose
```

---

## 当前状态（2026-06-19）

| 指标 | 数值 |
|------|------|
| 趋势序列 | 55 个 |
| 估值序列 (PE/PB/DY) | 51 个 |
| 外汇原始序列 | 13 个 |
| 外汇衍生序列 | 24 个（Python 复算） |
| 美元超级周期 | 9 个（3 原始月频 + 6 归一化周期） |
| **总覆盖** | **152 序列，100%** |
| **数据流** | Step 0 (新闻) → EDB(36) + Wind(38) + Python复算(30) → emotion(8) → dashboard |
| 看板模块 | 封面 + 看世界 + 10 数据模块 + 专题图表 |
| 图表功能 | 导出PNG图片 + 下载Excel数据（每个图表左上角悬停按钮） |
| **Skill 来源** | Step 0 (看世界) 来自 [xk2133/global-news-report](https://github.com/xk2133/global-news-report) v2.3.1（NINA 标注区） |

### 数据源分工

| 数据源 | 负责序列 | 调用量/日 |
|------|---------|:--:|
| **THS EDB**（免费）| 利率、汇率、商品、A/港/美股价格指数、FX 中间价/即期/债券收益率 | ~36 次 |
| **Wind MCP**（积分）| CNH远期/掉期点、全收益指数(12)、中信风格(5)、万得全A、CNYX、德/日债(3)、GBPCNY/USDJPY、估值 PE/PB/DY (51) | ~52 次 |
| **Python 复算** | 汇率变动拆解(8) + 套保成本(8) + 年化套保(8) | 1 次 |
| **华泰智研 MCP** | 情绪指数(2) + 资金面(6) | 1 次 |

---

## 文件结构

```
Martin Morning Brief/
├── SKILL.md                      ← 本文件（canonical skill）
├── scripts/
│   ├── run_daily.py              ← 每日流水线主入口
│   ├── generate_interactive_dashboard.py  ← 看板生成 + 内联嵌入报告 + 7天滚动清理
│   └── ...
├── templates/
│   └── dashboard.html            ← 看板 HTML 模板
├── output/
│   ├── interactive_dashboard.html        ← 主看板（不入库）
│   └── Global News Report-*.html         ← Step 0 产物，看板内联嵌入；仅留最近7天，不入库
├── docs/
│   └── index.html                ← GitHub Pages（自包含，已内联报告内容）
└── ...
```

---

## Nina 更新追踪

<!-- ═══════════════════════════════════════════════════════════════════ -->
<!-- NINA-UPDATE-GUIDE                                                    -->
<!--                                                                      -->
<!-- 当 Nina (xk2133) 更新 global-news-report 仓库时：                    -->
<!--                                                                      -->
<!-- 1. cd /tmp && git clone https://github.com/xk2133/global-news-report.git -->
<!--    或 cd global-news-report && git pull                              -->
<!--                                                                      -->
<!-- 2. 对比版本号：本文件 NINA:START 行标注的版本 vs 仓库 SKILL.md 版本  -->
<!--                                                                      -->
<!-- 3. ⚠️ 只更新数据获取流程，不更新网页设计：                           -->
<!--    ✅ Step 0A (Wind MCP 数据获取) — 跟随更新                         -->
<!--    ✅ Step 0B (新闻搜索/源筛选规则) — 跟随更新                       -->
<!--    ✅ Step 0D (质检清单) — 跟随更新                                  -->
<!--    ❌ Step 0C (HTML 模板/CSS 样式) — 保持不动，使用交互式看板        -->
<!--       的统一设计规范。只有当交互式看板整体升级设计时才修改此部分。   -->
<!--                                                                      -->
<!-- 4. 注意适配项（Nina 用 Windows，Martin 用 macOS）：                   -->
<!--    - Wind MCP CLI 路径：C:\Users\kongxy12\... → ~/.claude/skills/... -->
<!--    - YTD validation 年份范围可能需要更新                             -->
<!--                                                                      -->
<!-- 5. 替换后同步到 skills 目录：                                        -->
<!--    cp SKILL.md ~/.claude/skills/morning-brief/SKILL.md               -->
<!--                                                                      -->
<!-- 当前 Nina 版本：v2.3.1                                               -->
<!-- 本文件适配版本：v2.3.1-martin                                        -->
<!-- 最后同步日期：2026-06-19                                             -->
<!-- ═══════════════════════════════════════════════════════════════════ -->

## 故障排查

| 症状 | 可能原因 | 解决方案 |
|------|---------|---------|
| 看世界 iframe 显示占位 | Step 0 未执行或报告未生成 | 运行 Step 0 或 `@global-news-report` skill |
| 看世界内容不是当天新闻 | Step 0 执行了但报告是旧的 | 检查 output/ 是否有当天日期的 `Global News Report-*.html` |
| `fetch_data.py` 报认证错误 | JWE Token 过期 | 从同花顺重新获取 Token，更新 `~/.claude/mcp.json` |
| `fetch_data.py` 网络超时 | 同花顺 API 不可达 | 检查网络；系统会自动降级到 Wind MCP（如该序列在 `wind_mapping.json` 中有映射） |
| `fetch_wind.py` FX 序列无数据 | Wind 搜索词不精确 | 调整 `config/wind_mapping.json` 中的 `indicator_filter` |
| 大量验证失败（新序列） | 数据库数据源与 EDB 口径不一致 | 检查 `config/edb_mapping.json` 中的 EDB 查询是否准确；EDB 验证失败也会自动尝试 Wind 降级 |
| EDB 序列持续失败 | EDB + Wind 均无此序列 | 检查该序列是否同时在 `edb_mapping.json` 和 `wind_mapping.json` 中缺失；如是，需手动添加映射 |
| 数据库不存在 | 首次运行 | `run_daily.py` 会自动调用 `import_seed.py --replace`（all-in-one） |
| 衍生序列缺失 | `recompute_fx_derived.py` 未运行 | 手动运行 `python3 scripts/recompute_fx_derived.py` |
| Wind CLI 无输出 | symlink 路径问题 | 检查 `WIND_SKILL_DIR` 或使用真实路径 `~/.agents/skills/wind-mcp-skill/` |
| Windows 编码报错（UnicodeEncodeError） | Windows 默认 GBK 编码无法处理 ✓✗⚠ 等字符 | 运行时加前缀 `PYTHONIOENCODING=utf-8`；或使用 Windows Terminal（默认 UTF-8） |
| Windows 路径报错 | SKILL.md / README 中路径示例为 Unix 格式 | 参考 README 快速开始中的 Windows 对应命令 |
| Yahoo MCP 不工作 | 中国大陆被屏蔽 | 已切换为同花顺 EDB，无需 Yahoo |
