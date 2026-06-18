# Daily Morning Brief 开发文档

> 接手 agent 必读。本文档覆盖项目架构、数据流、关键决策和所有代码位置。

## 项目定位

本地金融市场早报和交互看板工具。以 Excel 历史底稿为种子数据，导入 SQLite 后通过 EDB/Wind MCP 每日拉取最新原始数据，Python 复算衍生序列，最后生成单文件离线 HTML 交互看板。

## 架构与数据流

```text
seed/                                config/
  └── seed.xlsx                            ├── edb_mapping.json
        │                                  └── wind_mapping.json
        ▼                                        │
  import_seed.py              fetch_data.py (THS EDB) + fetch_wind.py (Wind MCP)
  import_fx_data.py                 │
  import_super_cycle.py             ▼
        │                      morning_brief.sqlite ──────────────┐
        ▼                              │                          │
  morning_brief.sqlite                 ▼                          │
        │                      recompute_fx_derived.py             │
        ▼                       (复算外汇衍生序列)                   │
  update_data.py                      │                          │
        │                              ▼                          │
        ▼                      fetch_emotion.py                   │
  update_plan.json              (华泰智研 MCP)                    │
        │                              │                          │
        ├──────────────────────────────┴──────────────────────────┘
        ▼
  generate_interactive_dashboard.py ← templates/dashboard.html
        │
        ▼
  interactive_dashboard.html（单文件，零依赖）
```

> 公共模块 `scripts/lib.py` 提供日志/DB连接/验证/路径常量，所有脚本通过 `from lib import ...` 引用。

**一键运行**：`python3 scripts/run_daily.py`，依次执行：
1. `update_data.py` → 生成更新计划
2. `fetch_data.py` → 同花顺 EDB 拉取
3. `fetch_wind.py` → Wind MCP 拉取
4. `recompute_fx_derived.py` → 复算外汇衍生序列
5. `fetch_emotion.py` → 华泰智研 MCP 拉取
6. `generate_interactive_dashboard.py` → 生成看板（从 DB 读取超级周期数据）

**首次运行额外步骤**：`import_seed.py` → `import_fx_data.py` → `import_super_cycle.py`（导入历史种子数据）。

**数据源优先级**：THS EDB（免费，主力）→ Wind MCP（积分，补充外汇远期/掉期点+全收益指数+估值）→ Python 复算（衍生序列）→ 华泰智研 MCP（情绪/资金面）。

## 目录结构

| 路径 | 用途 |
| ---- | ---- |
| `SKILL.md` | **权威副本** Claude Code skill，修改后同步到 `~/.claude/skills/morning-brief/` |
| `seed/seed.xlsx` | 合并种子文件（走势图/PE TTM/PB LF/股息率/Fixing/Fwd Spread/美元指数，7 个工作表） |
| `templates/dashboard.html` | HTML 看板模板（~2400 行独立文件，IDE 语法高亮/格式化） |
| `data/morning_brief.sqlite` | 本地时序数据库 |
| `data/update_plan.json` | 增量更新计划，每个序列列出 `fetch_start_date`、`next_start_date`、`validation_dates` |
| `data/fetch_summary.json` | 每次 fetch 运行的摘要（运行时产物） |
| `config/edb_mapping.json` | series_id → EDB 查询词映射 + 批处理分组 + 验证容差 + skip 标记 |
| `config/wind_mapping.json` | series_id → Wind MCP 调用参数映射（kline + economic + fundamentals + category_overrides） |
| `output/interactive_dashboard.html` | **唯一输出产物**：封面 + 9 个数据模块 + 专题图表 |
| `scripts/lib.py` | **公共工具模块**（log / load_json / open_db / get_validation_dates / values_match / 路径常量 / Windows UTF-8 修复），所有脚本 import 引用 |
| `scripts/import_seed.py` | 从 Excel 导入/重建 SQLite，支持 `--replace` |
| `scripts/import_fx_data.py` | 从 Excel 导入外汇原始序列（一次性历史导入） |
| `scripts/import_super_cycle.py` | 导入美元超级周期数据（3 原始月频 + 6 归一化周期序列，幂等） |
| `scripts/update_data.py` | 扫描数据库生成更新计划 |
| `scripts/fetch_data.py` | 从同花顺 EDB HTTP API 拉取数据，验证后 UPSERT 入库 |
| `scripts/fetch_wind.py` | 从 Wind MCP CLI 拉取补充数据（外汇原始/全收益/估值） |
| `scripts/recompute_fx_derived.py` | 从原始数据复算外汇衍生序列（幂等），支持 --dry-run / --category |
| `scripts/fetch_emotion.py` | 从华泰智研 MCP HTTP API 拉取市场情绪和资金面数据（2 情绪 + 6 资金面）|
| `scripts/generate_interactive_dashboard.py` | Python 查库 + 从 DB 读取超级周期数据 + 嵌入 JSON → 输出 HTML |
| `scripts/run_daily.py` | 串联 import → update → fetch → recompute → dashboard（动态 step 计数） |
| `tests/` | 单元测试（test_validation.py + test_recompute.py，14 项） |
| `docs/plans/` | 历史设计文档存档 |

## 数据模型

### series 表

| 列 | 说明 |
| ---- | ---- |
| `series_id` | 主键，格式 `{prefix}:{display_name}` |
| `display_name` | 展示名 |
| `sheet_name` | 来源工作表（走势图/PE TTM/PB LF/股息率/外汇） |
| `frequency` | `D`（日度）或 `M`（月度） |
| `unit` | `percent_point` / `fx` / `index` / `price` / `multiple` / `percent` |
| `source_code` | 数据源代码（如 `H00300.CSI`、`IXIC.GI`） |
| `active` | 1=活跃，0=停用 |
| `update_method` | `edb_mcp` / `wind_mcp` / `cfets` / `derived` |

### observations 表

| 列 | 说明 |
| ---- | ---- |
| `series_id` | 外键 |
| `date` | 观测日期 |
| `value` | 原始值（含 0，不在入库时改写） |
| `as_of_date` | 数据截止日期 |
| `imported_at` | 导入时间戳 |

主键 `(series_id, date)`，导入和更新幂等。

### series_id 前缀约定

| 前缀 | 来源 | 频率 | 更新方式 |
| ---- | ---- | ---- | ---- |
| `trend:*` | 走势图 | D | EDB / Wind MCP |
| `pe_ttm:*` | PE TTM | M | Wind MCP |
| `pb_lf:*` | PB LF | M | Wind MCP |
| `dividend_yield:*` | 股息率 | M | Wind MCP |
| `fx:*` | 外汇 | D | EDB / Wind MCP / Python 复算 |
| `super_cycle:*` | 美元超级周期 | M | Python 复算（从 seed 导入后复算） |
| `htsc:*` | 华泰智研 | D/W | HTSC MCP |

### update_plan.json 结构

```json
{
  "generated_at": "ISO时间戳",
  "target_date": "今天日期",
  "items": [
    {
      "series_id": "trend:沪深300",
      "last_date": "数据库最新日期",
      "next_start_date": "下一次该拉取的日期",
      "fetch_start_date": "实际拉取起点（比 next_start_date 早，覆盖末端两点复核窗口）",
      "validation_dates": ["倒数第二日期", "倒数第一日期"],
      "status": "needs_update | up_to_date | needs_full_history"
    }
  ]
}
```

## 外汇衍生序列复算

`scripts/recompute_fx_derived.py` 是外汇模块数据更新的关键环节。它从原始数据重新计算所有衍生序列，确保公式一致性和口径正确。

### 复算类别

| 类别 | 序列数 | 原始数据依赖 | 核心公式 |
|------|:------:|-------------|---------|
| 即期汇率变动拆解 | 8 | `fx:usdcny-fixing` + `fx:usdcny-spot` | night_adj = (-fixing[t] + spot[t-1]) × 10000; day_move = (fixing[t] - spot[t]) × 10000; MA = (cum[t] - cum[t-N]) / N |
| CNY 套保成本 | 4 | `fx:cny-swap-*` + `fx:usdcny-spot` | swap / 10000 / spot |
| CNH 套保成本 | 4 | `fx:cnh-df-*` + `fx:usdcnh-spot` | DF / spot - 1 |
| 年化套保成本 | 8 | 套保成本序列 | (1 + hedge)^n - 1（n = 12/4/2/1） |

### 设计要点

- **幂等**：重复运行产生 0 条新观测
- **日期对齐**：仅在有所有原始数据的日期计算
- **零值处理**：跳过原始数据中的零值（视为无数据）
- **增量更新**：每次运行只插入数据库中不存在的日期

## 零值处理双轨规则

> 这是 2026-06-17 修改的核心规则，接手 agent 务必理解。

**原则**：数据库保留原始值，口径规则在应用层执行。

| 场景 | 规则 | 位置 |
| ---- | ---- | ---- |
| 单一指标图/计算 | 值为 0 → 视为当日无数据，直接跳过 | JS: `skipZeros()` |
| 一张图两个指标 | 值为 0 → 前向填补（取上一期有效读数），保证两条序列时间轴对齐 | JS: `fillZeroWithPrevious()` |
| 开头连续为 0 | 无论哪种规则都跳过（无上一期有效读数可填补） | 两个函数均处理 |

**实现位置**（均在 `scripts/generate_interactive_dashboard.py` 内）：

- **Python 侧** `load_observations()`：返回原始数据，不做任何零值处理。
- **JS 侧**（嵌入在 HTML_TEMPLATE 字符串中）：
  - `skipZeros(points)` — 过滤 `value === 0` 的点
  - `fillZeroWithPrevious(points)` — 与 Python 旧版 `fill_zero_with_previous` 等价
  - `pointsInRange(sid, start, end, {skipZero})` — 默认 `skipZero: true`，按需切换
  - `nearestOnOrBefore(sid, target)` — 始终跳过零值点

## 交互看板技术细节

### 封面 + 九个数据模块 + 专题图表

| 模块 | DOM ID | 渲染函数 | 图表类型 |
| ---- | ---- | ---- | ---- |
| 封面 | `view-cover` | (静态 HTML + CSS) | 导航卡片，浅色克制设计 |
| 走势看板 | `view-trend` | `renderTrend()` | `renderSvgLine` 折线图 |
| 股票涨跌 | `view-returns` | `renderReturns()` | `renderBarChart` 水平柱状图 |
| 利率涨跌 | `view-rates` | `renderRates()` | `renderBarChart` 水平柱状图 |
| 汇率涨跌 | `view-fx` | `renderFx()` | `renderBarChart` 水平柱状图 |
| 中美利差 | `view-spread` | `renderSpread()` | `renderSvgLine` 折线图（两张） |
| 中间价 | `view-fixing` | `renderFxFixing()` | `renderSvgLine` 折线图（两张：中间价 vs 即期 + 汇率拆解） |
| 套保成本 | `view-cost` | `renderFxCost()` | `renderTermStructure` 期限结构 + `renderSvgLine` 时序图（三张） |
| 估值看板 | `view-valuation` | `renderValuation()` | `renderSvgLine` 折线图 + 参考线 |
| 市场情绪 | `view-emotion` | `renderEmotion()` | `renderSvgLine` 折线图（三张） |
| 专题图表 | `view-topics` | `renderTopics()` | `renderCategoricalLine` 分类 X 轴折线图 |

### 套保成本模块特殊设计

- **Chart 1（期限结构）**：`renderTermStructure()` — 自定义 SVG，支持多取值方式叠加、CNY/CNH 双合约、年化值数据标签
- **Chart 2（3M 时序）**：独立合约选择 + 当日值/5日均值切换
- **Chart 3（1Y vs 利差）**：双 Y 轴（左=套保成本%，右=利差 pp），独立合约选择 + 平滑

所有套保成本展示均为年化值。CNY = 实线，CNH = 虚线。

### 序列清单

在 `generate_interactive_dashboard.py` 中定义为 Python 常量，也以 JSON 形式嵌入 HTML：

- `TREND_SERIES`：约 28 个日频走势指标（利率、汇率、股票指数、商品）
- `RETURN_SERIES`：15 个含 region 标签的全收益/商品序列（A 股/港股/美股/商品）
- `RATE_SERIES`：13 个利率指标
- `FX_SERIES`：7 个汇率指标，分 group（兑人民币/美元交叉/指数）
- `SPREAD_SERIES`：4 个利率 + USDCNY，用于计算中美利差
- `FX_FIXING_SERIES`：4 个中间价相关序列（fixing/spot/decomp 20MA）
- `FX_COST_SERIES`：18 个套保成本相关序列（原始+年化+债券收益率）
- `EMOTION_SERIES`：2 个情绪指数（A股情绪、港股情绪），来源华泰智研 MCP
- `CAPITAL_SERIES`：6 个 A 股资金面序列（ETF/融资/公募/散户/减持/一级市场），来源华泰智研 MCP
- 估值：动态从数据库查询 PE TTM / PB LF / 股息率三个维度都有的指数
- 专题：从 DB 读取超级周期归一化数据（`super_cycle:dxy_*` / `super_cycle:dae_*`）

### 核心渲染函数

- `renderSvgLine(containerId, cfg)`：SVG 折线图。支持左右双轴、图例、数据点标记（≤320 点时）、虚线、鼠标悬停 tooltip。
- `renderBarChart(containerId, values, cfg)`：水平柱状图。正值向右、负值向左，按值排序。
- `renderCategoricalLine(containerId, cfg)`：分类 X 轴折线图。用于专题图表（T+N 对齐比较），支持 tooltip 和图例。
- `renderTermStructure(containerId, modesData, contract)`：自定义 SVG 期限结构图。支持多取值方式叠加、CNY/CNH 双合约（实线/虚线区分）、数据标签自动堆叠。
- `rollingReferenceSeries(points, window)`：计算滚动均值 ±1σ/±2σ 参考线，窗口不含 T 日自身。

### 数据嵌入方式

`generate_interactive_dashboard.py` 从 `templates/dashboard.html` 加载 HTML 模板（~2400 行独立文件），内含 `__DATA__` 占位符。`render_dashboard()` 调用 `build_payload()` 查库构建 JSON（含超级周期归一化数据），然后 `template.replace("__DATA__", json.dumps(payload))`。

## 常用开发命令

```bash
# 重新导入历史数据（保留已有数据，追加新数据）
python3 scripts/import_seed.py

# 导入外汇数据
python3 scripts/import_fx_data.py

# 完全替换数据库（删除旧库，从 Excel 重建）
python3 scripts/import_seed.py --replace

# 生成增量更新计划
python3 scripts/update_data.py

# 复算外汇衍生序列
python3 scripts/recompute_fx_derived.py --verbose
python3 scripts/recompute_fx_derived.py --dry-run
python3 scripts/recompute_fx_derived.py --category hedge

# 重新生成交互看板
python3 scripts/generate_interactive_dashboard.py

# 一键运行全部
python3 scripts/run_daily.py

# Python 语法检查
python3 -m py_compile scripts/recompute_fx_derived.py scripts/import_seed.py scripts/update_data.py scripts/generate_interactive_dashboard.py scripts/run_daily.py

# 提取 JS 并检查语法
awk '/^  <script>$/{p=1; next} /^  <\/script>$/{p=0} p' output/interactive_dashboard.html > /tmp/dash.js
node --check /tmp/dash.js
```

## 依赖

- Python 3，标准库 + `pandas` + `openpyxl`（`import_seed.py` / `import_fx_data.py` / `import_super_cycle.py`）
- 看板生成不依赖 `openpyxl`（超级周期数据已从 DB 读取）
- Node.js（仅用于开发时 JS 语法检查，运行时不需）
- 浏览器打开 `output/interactive_dashboard.html`（零前端依赖）
- Google Fonts（IBM Plex Sans + Noto Sans SC，首次加载后浏览器缓存）
- Wind MCP CLI（`~/.claude/skills/wind-mcp-skill/`，用于外汇远期/掉期点/全收益指数/估值数据）

## 当前状态（2026-06-18）

### 已完成

- [x] Excel 底稿导入 SQLite（7 个工作表，~152 个序列）
- [x] 日频数据最新至 2026-06-18，估值日频数据最新至 2026-06-16
- [x] 增量更新计划生成（含末端两点复核窗口）
- [x] 交互看板 9 个数据模块 + 封面 + 专题图表全部可用
- [x] 零值处理双轨规则（单指标跳过 / 双指标填补）
- [x] 同花顺 EDB 实时数据拉取（`fetch_data.py`）
- [x] Wind MCP 补充数据拉取（`fetch_wind.py`）
- [x] 外汇衍生序列 Python 复算（`recompute_fx_derived.py`）
- [x] 华泰智研 MCP 情绪/资金面数据拉取（`fetch_emotion.py`）
- [x] Claude Code skill（`morning-brief`，一键执行 daily pipeline）
- [x] 封面 + 品牌（Multi Asset Morning Brief / Designed by MARTIN）
- [x] Impeccable 设计优化（字体 IBM Plex Sans + Noto Sans SC、色彩系统重构、微交互、a11y）
- [x] 专题图表模块（美元超级周期，`renderCategoricalLine` 渲染器，**从 DB 读取不再依赖 Excel**）
- [x] 中间价模块（中间价 vs 即期 + ±2% bands + 汇率变动拆解 20MA）
- [x] 套保成本模块（期限结构 + 3M 时序 + 1Y vs 利差；多取值方式/合约选择/平滑切换；全部年化）
- [x] **代码优化**（提取 `lib.py` 公共模块、HTML 模板外置、DB context manager、异常处理规范化、env var 配置、单元测试 14 项）
- [x] **Wind MCP FX 修复**（CNH DF + CNY swap 搜索词修复、category 级别容差覆盖）
- [x] **种子文件合并**（2 文件 → seed.xlsx 单文件，移除衍生列）
- [x] **美元超级周期 DB 化**（导入 seed + 存储在 DB + 看板从 DB 读取，不再依赖外部 Excel）

### 覆盖率

| 类别 | 序列数 | 数据源 | 备注 |
| ---- | ---- | ---- | ---- |
| 利率（中美欧日澳） | 13 | EDB | |
| 汇率 | 7 | EDB | GBPCNY/USDJPY 切回 Wind |
| 商品 | 4 | EDB | |
| A 股价格指数 | 9 | EDB | 万得全A 除外（Wind） |
| 港股价格指数 | 3 | EDB | |
| 美股价格指数 | 2 | EDB | |
| 中信风格指数 | 5 | Wind | 数据源差异 ~0.57% |
| 全收益/R 系列 | 12 | Wind | |
| **外汇原始** | **13** | **EDB + Wind** | 中间价/即期/CNH远期/掉期点/债券收益率 |
| **外汇衍生** | **24** | **Python 复算** | 汇率拆解(8) + 套保成本(8) + 年化(8) |
| **美元超级周期** | **9** | **Python 复算** | DXY/Real/Nominal 月频(3) + 归一化周期(6) |
| PE TTM | 16 | Wind | |
| PB LF | 16 | Wind | |
| 股息率 | 16 | Wind | |
| **总覆盖** | **~152** | | |

### 待完成

- [ ] **定时调度**：每天自动执行 `run_daily.py`（cron / Claude Code `/loop`）
- [ ] **均线功能**：用户计划在单一指标走势图上叠加均线
- [ ] **输出形式确认**：最终是 HTML 文件、邮件发送、还是托管仪表盘

## 开发约定

修改项目时请遵守：

1. **数据入库不改写**：`import_seed.py`、`fetch_data.py`、`fetch_wind.py` 原样写入，不在入库层清洗。
2. **口径规则在应用层**：零值处理、前向填补等均在 JS 或 Python 绘图层执行。
3. **衍生数据实时复算**：`recompute_fx_derived.py` 每次 fetch 后运行，确保衍生序列与原始数据一致。
4. **修改口径后更新文档**：改 README.md 和本文件，避免后续 agent 误用旧口径。
5. **新增序列**：确认底稿列名、源代码、频率、单位后，加入对应 Python 常量列表，并补全 EDB/Wind 映射。
6. **新增图表**：优先复用 `renderSvgLine`、`renderBarChart` 或 `renderTermStructure`，不引入外部 JS 依赖。
7. **替换底稿前先验证**：临时导入到独立 SQLite 文件核查序列和点数，再用 `--replace` 重建主库。
8. **JS 改动后做语法检查**：`node --check` 验证提取出的 JS。
9. **衍生数据写入 SQLite**：不在 generate 时临时计算，确保看板数据与 DB 一致且可溯源。

## GitHub 仓库

| 项目 | 值 |
| ---- | --- |
| **地址** | [github.com/martin-hub-1989/morning-brief](https://github.com/martin-hub-1989/morning-brief) |
| **可见性** | Public |
| **默认分支** | `main` |
| **远程名** | `origin` |

### .gitignore 排除项

```text
data/morning_brief.sqlite    160MB 数据库
output/interactive_dashboard.html  生成的看板
data/fetch_summary.json      运行时产物
data/update_plan.json        运行时产物
__pycache__/                  Python 编译缓存
*.pyc *.bak .DS_Store
```

### 同步更改

```bash
cd "/Users/martin_ai/Desktop/Martin Morning Brief"
git add -A
git commit -m "描述改动内容"
git push
```

或对 Claude Code 说「推到 GitHub」即可自动执行以上三步。
