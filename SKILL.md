---
name: morning-brief
description: 运行 Daily Morning Brief 每日流水线 — 从同花顺 EDB + Wind MCP 拉取最新市场数据，复算外汇衍生序列，更新本地 SQLite 数据库，生成交互式 HTML 看板。触发词：早报、morning brief、更新看板、run daily。
---

# Daily Morning Brief — Multi Asset Dashboard

本地金融市场早报和交互看板工具。一键执行每日数据更新流程。

> 🌐 **在线看板**：[martin-hub-1989.github.io/morning-brief](https://martin-hub-1989.github.io/morning-brief/) — GitHub Pages 自动部署

## 触发条件

用户提到以下任一关键词时激活：早报、morning brief、更新看板、run daily、fetch data、generate dashboard、晨报、run_daily。

## 项目位置

项目根目录即本仓库的顶层文件夹。首次克隆后，先用 `cd` 进入该目录再执行后续命令。

> **同步规则**：本文件是 skill 的权威副本（canonical copy）。对 skill 的任何修改都应更新此文件，然后同步到 skills 目录。
> 
> **macOS / Linux:**
> ```bash
> cp SKILL.md ~/.claude/skills/morning-brief/SKILL.md
> ```
> **Windows:**
> ```cmd
> copy SKILL.md %USERPROFILE%\.claude\skills\morning-brief\SKILL.md
> ```

## 标准流程

### 1. 确认环境

在项目根目录下执行：

```bash
python3 -c "import sqlite3, json, urllib.request; print('OK')"
```

### 2. 执行每日流水线

```bash
python3 scripts/run_daily.py
```

这将依次运行脚本：

1. **update_data.py** — 扫描数据库，生成增量更新计划 (`data/update_plan.json`)
2. **fetch_data.py** — 从同花顺 EDB API 拉取日频数据，验证后写入 SQLite
3. **fetch_wind.py** — 从 Wind MCP 拉取外汇原始数据（CNH远期/掉期点）+ 全收益指数 + 估值
4. **recompute_fx_derived.py** — 从原始数据复算所有外汇衍生序列（汇率拆解+套保成本+年化），幂等
5. **fetch_emotion.py** — 从华泰智研 MCP 拉取市场情绪和资金面数据
6. **generate_interactive_dashboard.py** — 从 DB & `templates/dashboard.html` 生成 `output/interactive_dashboard.html` + `docs/index.html`（GitHub Pages）

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
Fetched:  36 series    ← 成功从同花顺 EDB 拉取
Passed:   36 ok        ← 验证通过
New obs:  N observations

=== Wind Fetch Summary ===
Targeted: 38 series    ← Wind 负责的序列
Fetched:  38 series    ← 成功从 Wind 拉取
New obs:  N observations

=== Recompute FX Derived ===
New derived obs: N     ← 新增的复算观测（幂等，重复运行=0）
```

**THS EDB 跳过分类**：
- `skip_reason: 改用 Wind MCP` → 中信风格/德日债/GBPCNY/USDJPY，Wind 数据与 DB 一致
- `skip_reason: EDB ... 全收益` → 全收益指数 EDB 不支持，已由 Wind MCP 接管
- `skip_reason: EDB 无...` → EDB 无此指标（如万得全A），已由 Wind 接管

### 4. 报告结果

向用户报告：

```
## Daily Morning Brief 每日更新完成

- 更新时间：<timestamp>
- 同花顺 EDB：36 / 36 个序列通过
- Wind MCP：38 / 38 个序列拉取成功
- FX 衍生复算：N 条新观测
- 验证通过：74 个
- 看板文件：output/interactive_dashboard.html
```

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
| 2 | 看世界 | 看世界 | iframe 加载成功，无占位提示；或显示占位提示说明原因 |
| 3 | 走势看板 | 走势看板 | 图表+数据表正常，可选择不同指标和周期 |
| 4 | 股票涨跌 | 涨跌复盘 | 柱状图+表格正常，中信风格子图正常 |
| 5 | 利率涨跌 | 涨跌复盘 | 柱状图+表格正常 |
| 6 | 汇率涨跌 | 外汇看板 | 柱状图+表格正常，分组颜色正确 |
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
- 看世界 iframe 占位 → 运行 `@global-news-report` skill 生成报告后重新生成看板
- 中间价/套保成本无数据 → 检查 `recompute_fx_derived.py` 是否成功运行
- **⚠️ 级联故障（关键）**：如果多个连续模块同时空白（尤其是外汇看板的后三个模块：中间价/套保成本/专题图表），很可能是某个**前面的模块**抛出了未捕获的 JS 错误导致 `init()` 中断。最常引发此问题的模块是**市场情绪**（`renderEmotion`），当 HTSC 情绪数据缺失时 `latestDate()` 返回 `null`，传入 `startForPeriod()` → `toDate(null)` 生成无效 Date → `toISO()` 调用 `toISOString()` 抛出 `RangeError: Invalid time value`。排查方法：检查浏览器控制台是否有 JS 错误，检查 `htsc:*` 系列数据是否存在。

## 补充命令

### 仅重新生成看板（不拉取数据）

```bash
python3 scripts/run_daily.py --skip-fetch
```

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

## 当前状态（2026-06-18）

| 指标 | 数值 |
|------|------|
| 趋势序列 | 55 个 |
| 估值序列 (PE/PB/DY) | 51 个 |
| 外汇原始序列 | 13 个 |
| 外汇衍生序列 | 24 个（Python 复算） |
| 美元超级周期 | 9 个（3 原始月频 + 6 归一化周期） |
| **总覆盖** | **152 序列，100%** |
| **数据流** | EDB(36) + Wind(38) + Python复算(30) → emotion(8) → dashboard |
| 看板模块 | 封面 + 看世界 + 10 数据模块 + 专题图表 |
| 图表功能 | 导出PNG图片 + 下载Excel数据（每个图表左上角悬停按钮） |

### 数据源分工

| 数据源 | 负责序列 | 调用量/日 |
|------|---------|:--:|
| **THS EDB**（免费）| 利率、汇率、商品、A/港/美股价格指数、FX 中间价/即期/债券收益率 | ~36 次 |
| **Wind MCP**（积分）| CNH远期/掉期点、全收益指数(12)、中信风格(5)、万得全A、CNYX、德/日债(3)、GBPCNY/USDJPY、估值 PE/PB/DY (51) | ~52 次 |
| **Python 复算** | 汇率变动拆解(8) + 套保成本(8) + 年化套保(8) | 1 次 |
| **华泰智研 MCP** | 情绪指数(2) + 资金面(6) | 1 次 |

## 故障排查

| 症状 | 可能原因 | 解决方案 |
|------|---------|---------|
| `fetch_data.py` 报认证错误 | JWE Token 过期 | 从同花顺重新获取 Token，更新 `~/.claude/mcp.json` |
| `fetch_data.py` 网络超时 | 同花顺 API 不可达 | 检查网络，确认 `api-mcp.51ifind.com:8643` 可达 |
| `fetch_wind.py` FX 序列无数据 | Wind 搜索词不精确 | 调整 `config/wind_mapping.json` 中的 `indicator_filter` |
| 大量验证失败（新序列） | 数据库数据源与 EDB 口径不一致 | 检查 `config/edb_mapping.json` 中的 EDB 查询是否准确 |
| 数据库不存在 | 首次运行 | `run_daily.py` 会自动调用 `import_seed.py --replace`（all-in-one） |
| 衍生序列缺失 | `recompute_fx_derived.py` 未运行 | 手动运行 `python3 scripts/recompute_fx_derived.py` |
| Windows 编码报错（UnicodeEncodeError） | Windows 默认 GBK 编码无法处理 ✓✗⚠ 等字符 | 运行时加前缀 `PYTHONIOENCODING=utf-8`；或使用 Windows Terminal（默认 UTF-8） |
| Windows 路径报错 | SKILL.md / README 中路径示例为 Unix 格式 | 参考 README 快速开始中的 Windows 对应命令 |
| Yahoo MCP 不工作 | 中国大陆被屏蔽 | 已切换为同花顺 EDB，无需 Yahoo |
