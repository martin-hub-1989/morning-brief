# Daily Morning Brief — Multi Asset Dashboard

本地金融市场早报工具。历史 Excel 数据已导入 SQLite，基于同花顺 EDB API + Wind MCP 每日自动拉取最新数据，生成离线 HTML 交互看板。

> 开发接手请阅读 [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)，包含完整架构、数据流、关键决策和代码位置。
>
> Claude Code 中使用 `morning-brief` skill 一键执行每日更新。

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
│   ├── 20260617-Morning Brief Skill（日频全量）.xlsx
│   └── 中间价与套保成本.xlsx
├── config/                        ← 数据源查询映射
│   ├── edb_mapping.json           ← series_id → EDB 查询词
│   └── wind_mapping.json          ← series_id → Wind MCP 参数
├── data/                          ← 运行时数据（SQLite + JSON 计划）
│   ├── morning_brief.sqlite       ← ~130 序列时序数据库
│   ├── update_plan.json           ← 增量更新计划
│   └── fetch_summary.json         ← 最近拉取摘要
├── scripts/                       ← 所有 Python 脚本
│   ├── run_daily.py               ← 一键运行入口
│   ├── import_seed.py             ← 从 Excel 导入/重建数据库
│   ├── import_fx_data.py          ← 从 Excel 导入外汇序列
│   ├── update_data.py             ← 生成增量更新计划
│   ├── fetch_data.py              ← 同花顺 EDB 数据拉取
│   ├── fetch_wind.py              ← Wind MCP 数据拉取
│   ├── fetch_emotion.py           ← 华泰智研 MCP 情绪数据
│   ├── recompute_fx_derived.py    ← 外汇衍生序列复算（幂等）
│   └── generate_interactive_dashboard.py ← 生成 HTML 看板
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
| 1 | 同花顺 EDB | ✅ 已接入 | 32 日频趋势 + 4 FX 原始序列 |
| 2 | Wind MCP | ✅ 已接入 | 24 补充序列 + 14 FX 原始序列 + 51 估值序列 |
| 3 | Python 复算 | ✅ 已接入 | 24 FX 衍生序列（汇率拆解 + 套保成本 + 年化） |
| 4 | 华泰智研 MCP | ✅ 已接入 | 2 情绪指数 + 6 资金面序列 |

## 交互式看板

打开 `output/interactive_dashboard.html`：

- **封面**：Multi Asset Morning Brief，导航卡片直达各模块
- **9 个数据模块**：走势看板、股票涨跌、利率涨跌、汇率涨跌、中美利差、中间价、套保成本、估值看板、市场情绪
- **专题图表**：美元超级周期（DXY + D/AE 对比）

## 每日流水线

```text
update_data.py → fetch_data.py (EDB) → fetch_wind.py (Wind)
       → recompute_fx_derived.py → fetch_emotion.py (HTSC)
       → generate_interactive_dashboard.py
```

1. **update_data.py** — 扫描数据库，生成增量更新计划
2. **fetch_data.py** — 同花顺 EDB 拉取日频数据，验证后 UPSERT
3. **fetch_wind.py** — Wind MCP 拉取外汇原始数据 + 全收益指数 + 估值
4. **recompute_fx_derived.py** — 从原始数据复算所有外汇衍生序列（幂等）
5. **fetch_emotion.py** — 华泰智研 MCP 拉取市场情绪数据
6. **generate_interactive_dashboard.py** — 生成单文件 HTML 看板

## 数据口径

- 数据库保留原始导入值，口径规则在应用层执行。
- 单一指标图：值为 `0` → 视为当日无数据，跳过。
- 一张图双指标：值为 `0` → 前向填补（保证时间轴对齐）。
- 开头连续为 `0`：无论哪种规则都跳过。
- 外汇衍生序列由 `recompute_fx_derived.py` 从原始数据重算，确保公式一致性。

## 外汇衍生序列复算

`scripts/recompute_fx_derived.py` 从原始数据计算三类衍生序列：

| 类别 | 序列数 | 公式 |
|------|:------:|------|
| 即期汇率变动拆解 | 8 | 夜盘调整/日盘变动/累积值/5MA/20MA ← fixing + spot |
| 套保成本 | 8 | CNY = swap/10000/spot；CNH = DF/spot - 1 |
| 年化套保成本 | 8 | (1 + hedge)^n - 1（n = 12/4/2/1） |

支持 `--dry-run`、`--verbose`、`--category decomp|hedge` 参数。重复运行幂等（0 new）。

## 数据验证

每次从 EDB/Wind 拉取后，比较 `validation_dates`（数据库最后两点）与新拉取值：

- 两点匹配 → 追加新日期
- 一点不匹配 → 记录警告，仍追加新日期
- 两点都不匹配 → 跳过该序列，保留原库数据（人工复核）

## 当前状态（2026-06-17）

| 指标 | 数值 |
|------|------|
| 趋势序列 | 55 个 |
| 估值序列 (PE/PB/DY) | 51 个 |
| 外汇原始序列 | 13 个（中间价/即期/CNH远期/掉期点/债券收益率） |
| 外汇衍生序列 | 24 个（汇率拆解 + 套保成本 + 年化） |
| **THS EDB 覆盖** | 36 个（32 趋势 + 4 FX） |
| **Wind MCP 覆盖** | 37 个（23 趋势 + 14 FX） + 51 估值 |
| **Python 复算** | 24 FX 衍生序列 |
| **总覆盖** | **~130 序列，100% 覆盖** |
| 数据起点 | 趋势 1989-06-05，估值 1990-12-19，外汇 1981-01-02 |
