# Daily Morning Brief — Multi Asset Dashboard

本地金融市场早报工具。历史 Excel 数据已导入 SQLite，基于同花顺 EDB API + Wind MCP 每日自动拉取最新数据，生成离线 HTML 交互看板。

> 🌐 **在线看板**：[martin-hub-1989.github.io/morning-brief](https://martin-hub-1989.github.io/morning-brief/)
>
> 开发接手请阅读 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)，包含完整架构、数据流、关键决策和代码位置。
>
> Claude Code 中使用 `morning-brief` skill 一键执行每日更新。

## 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/martin-hub-1989/morning-brief.git
cd morning-brief

# 2. 安装依赖（仅 pandas + openpyxl）
pip install -r requirements.txt

# 3. 从 seed.xlsx 导入全部历史数据（一条命令，自动处理所有 sheet）
python3 scripts/import_seed.py --replace

# 4. 复算外汇衍生序列（汇率拆解 + 套保成本 + 年化，24 个序列）
python3 scripts/recompute_fx_derived.py

# 5. 安装 Claude Code skill（可选）
mkdir -p ~/.claude/skills/morning-brief
cp SKILL.md ~/.claude/skills/morning-brief/SKILL.md

# 6. 生成看板（使用种子数据，不拉取新数据）
python3 scripts/run_daily.py --skip-fetch

# 7. 打开看板
open output/interactive_dashboard.html                # macOS
# start output\interactive_dashboard.html             # Windows
# xdg-open output/interactive_dashboard.html          # Linux
```

> **Windows 用户**：如遇 `UnicodeEncodeError` 报错，请使用 Windows Terminal（默认 UTF-8），或运行前执行 `set PYTHONIOENCODING=utf-8`。PowerShell 中执行 `$env:PYTHONIOENCODING="utf-8"`。
>
> 如需每日自动拉取最新数据，需配置同花顺 EDB + Wind MCP 凭证（见下方环境要求）。

## 环境要求

- Python 3（标准库 + `pandas` + `openpyxl`）
- 浏览器打开 HTML 即可使用（零前端依赖）
- 同花顺 EDB API 访问（`api-mcp.51ifind.com:8643`）
- Wind MCP CLI（用于外汇远期/掉期点和全收益指数数据）

## 目录

```text
├── SKILL.md                       ← Claude Code skill（权威副本）
├── README.md                      ← 本文件
├── .gitignore
├── requirements.txt               ← Python 依赖
├── seed/                          ← 历史数据种子文件
│   └── seed.xlsx                  ← 合并种子（走势/估值/外汇/债券/美元指数/MSCI，8 个工作表）
├── templates/                     ← HTML 模板
│   └── dashboard.html             ← 看板 HTML/CSS/JS 模板（~2400 行独立文件）
├── config/                        ← 数据源查询映射
│   ├── edb_mapping.json           ← series_id → EDB 查询词
│   └── wind_mapping.json          ← series_id → Wind MCP 参数
├── data/                          ← 运行时数据（SQLite + JSON 计划）
│   ├── morning_brief.sqlite       ← ~176 序列时序数据库
│   ├── update_plan.json           ← 增量更新计划
│   └── fetch_summary.json         ← 最近拉取摘要
├── scripts/                       ← 所有 Python 脚本
│   ├── lib.py                     ← 公共工具模块（日志/DB/验证/路径常量）
│   ├── run_daily.py               ← 一键运行入口
│   ├── import_seed.py             ← 从 seed.xlsx 一键导入全部数据（含外汇/超级周期）
│   ├── update_data.py             ← 生成增量更新计划
│   ├── fetch_data.py              ← 同花顺 EDB 数据拉取
│   ├── fetch_wind.py              ← Wind MCP 数据拉取
│   ├── fetch_emotion.py           ← 华泰智研 MCP 情绪数据
│   ├── recompute_fx_derived.py    ← 外汇衍生序列复算（幂等）
│   └── generate_interactive_dashboard.py ← 生成 HTML 看板
├── tests/                         ← 单元测试
│   ├── test_validation.py         ← values_match 验证逻辑测试
│   └── test_recompute.py          ← 外汇套保成本公式测试
├── docs/                          ← 开发文档
│   ├── DEVELOPMENT.md
│   └── plans/
└── output/                        ← 生成产物
    └── interactive_dashboard.html ← 封面 + 9 模块 + 专题图表
```

## 常用操作

```bash
# 一键运行（拉取最新数据 + 复算衍生序列 + 生成看板）
python3 scripts/run_daily.py

# 仅生成看板（不拉取数据）
python3 scripts/run_daily.py --skip-fetch

# 仅拉取数据
python3 scripts/fetch_data.py

# 仅复算外汇衍生序列
python3 scripts/recompute_fx_derived.py --verbose

# 干跑验证（不写库）
python3 scripts/fetch_data.py --dry-run --verbose

# 测试单个序列
python3 scripts/fetch_data.py --series trend:USDCNY --verbose

# 重建数据库
python3 scripts/import_seed.py --replace
```

## 数据源

| 优先级 | 数据源 | 状态 | 覆盖率 |
| ---- | ---- | ---- | ---- |
| 1 | 同花顺 EDB | ✅ 已接入 | 32 日频趋势 + 4 FX 原始序列 + 11 MSCI 综合市场 |
| 2 | Wind MCP | ✅ 已接入 | 24 补充序列 + 14 FX 原始序列 + 51 估值序列 + 24 MSCI（全系列） |
| 3 | Python 复算 | ✅ 已接入 | 24 FX 衍生序列（汇率拆解 + 套保成本 + 年化） |
| 4 | 华泰智研 MCP | ✅ 已接入 | 2 情绪指数 + 6 资金面序列 |
| 5 | 美元超级周期 | ✅ 已接入 | 3 原始月频 + 6 归一化周期序列（DB 存储，不再依赖 Excel） |
| 6 | global-news-report | 🔧 可选 | 看世界模块（需单独安装 skill） |

### 看世界模块

看世界模块依赖 [global-news-report](https://github.com/xk2133/global-news-report) skill：

```bash
# 1. 安装 skill
mkdir -p ~/.claude/skills/global-news-report
cd /tmp && git clone https://github.com/xk2133/global-news-report.git
cp /tmp/global-news-report/SKILL.md ~/.claude/skills/global-news-report/SKILL.md

# 2. 在 Claude Code 中运行
@global-news-report

# 3. 将生成的 Global News Report-YYYYMMDD.html 放到 output/，然后重新生成看板
python3 scripts/run_daily.py --skip-fetch
```

如果未安装或未生成报告，看世界模块会显示占位提示。

## 交互式看板

打开 `output/interactive_dashboard.html`：

- **封面**：Multi Asset Morning Brief，导航卡片直达各模块
- **看世界**：全球宏观要闻、市场数据一览（内联嵌入，看板文件完全自包含，无需额外文件即可共享）
- **11 个数据模块**：走势看板（含 MSCI 综合市场指数）、股票走势、股票涨跌（含 Since 924 区间，>1Y 显示年化）、国际股票（MSCI 市场/行业涨跌 + 成长vs价值走势）、利率涨跌（>1Y 显示年化 bp）、汇率涨跌（>1Y 显示年化）、中美利差、中间价、套保成本、估值看板、市场情绪
- **专题图表**：美元超级周期（DXY + D/AE 对比）
- **图表导出**：每个图表左上角悬停显示导出图片（PNG）和下载数据（Excel）按钮
- **返回主页**：每个模块页面右上角提供 `← 主页` 按钮，一键回到欢迎封面

## 每日流水线

```text
首次运行: import_seed.py → recompute_fx_derived.py → update_data.py → ...
日常运行: update_data.py → fetch_data.py (EDB) → fetch_wind.py (Wind)
         → recompute_fx_derived.py → fetch_emotion.py (HTSC)
         → generate_interactive_dashboard.py
```

> `import_seed.py` 一键处理 seed.xlsx 的全部 7 个 sheet（走势/估值/外汇/超级周期），无需多个脚本。

1. **import_seed.py** — 首次运行：从 Excel 种子文件导入全部历史数据（走势/估值/外汇/超级周期）
2. **update_data.py** — 扫描数据库，生成增量更新计划
3. **fetch_data.py** — 同花顺 EDB 拉取日频数据（含 MSCI 综合市场 11 个），验证后 UPSERT
4. **fetch_wind.py** — Wind MCP 拉取 MSCI 全部指数 + 外汇原始数据 + 全收益指数 + 估值
5. **recompute_fx_derived.py** — 从原始数据复算所有外汇衍生序列（幂等）
6. **fetch_emotion.py** — 华泰智研 MCP 拉取市场情绪数据
7. **generate_interactive_dashboard.py** — 生成单文件 HTML 看板

## 数据口径

- 数据库保留原始导入值，口径规则在应用层执行。
- 单一指标图：值为 `0` → 视为当日无数据，跳过。
- 一张图双指标：值为 `0` → 前向填补（保证时间轴对齐）。
- 开头连续为 `0`：无论哪种规则都跳过。
- 外汇衍生序列由 `recompute_fx_derived.py` 从原始数据重算，确保公式一致性。

## 外汇衍生序列复算

`scripts/recompute_fx_derived.py` 从原始数据计算三类衍生序列：

| 类别 | 序列数 | 公式 |
| ---- | :----: | ---- |
| 即期汇率变动拆解 | 8 | 夜盘调整/日盘变动/累积值/5MA/20MA ← fixing + spot |
| 套保成本 | 8 | CNY = swap/10000/spot；CNH = DF/spot - 1 |
| 年化套保成本 | 8 | (1 + hedge)^n - 1（n = 12/4/2/1） |

支持 `--dry-run`、`--verbose`、`--category decomp|hedge` 参数。重复运行幂等（0 new）。

## 数据验证

每次从 EDB/Wind 拉取后，比较 `validation_dates`（数据库最后两点）与新拉取值：

- 两点匹配 → 追加新日期
- 一点不匹配 → 记录警告，仍追加新日期
- 两点都不匹配 → 跳过该序列，保留原库数据（人工复核）

## 当前状态（2026-06-18）

| 指标 | 数值 |
| ---- | ---- |
| 趋势序列 | 55 个（含 MSCI 24 个） |
| 估值序列 (PE/PB/DY) | 51 个 |
| 外汇原始序列 | 13 个（中间价/即期/CNH远期/掉期点/债券收益率） |
| 外汇衍生序列 | 24 个（汇率拆解 + 套保成本 + 年化） |
| 美元超级周期 | 9 个（3 原始月频 + 6 归一化周期） |
| **THS EDB 覆盖** | 47 个（32 趋势 + 4 FX + 11 MSCI 综合市场） |
| **Wind MCP 覆盖** | 62 个（25 趋势 + 13 FX + 24 MSCI） + 51 估值 |
| **Python 复算** | 24 FX 衍生 + 6 超级周期归一化 |
| **总覆盖** | **176 序列，100% 覆盖** |
| 数据起点 | 趋势 1989-06-05，估值 1990-12-19，外汇 1981-01-02，美元指数 1971-01-31，MSCI 1989-06-05 |
