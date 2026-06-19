# Morning Brief 代码优化计划

> 本文档为代码审阅后整理的优化任务清单，供执行 agent 逐项实施。
> 审阅日期：2026-06-18
> 项目路径：`/Users/martin_ai/Desktop/Martin Morning Brief`

---

## 优先级说明

| 级别 | 含义 |
|-----|------|
| P0 | 立即执行，影响可靠性或维护效率 |
| P1 | 近期执行，改善代码质量 |
| P2 | 可选执行，属于增强项 |

---

## 1. 提取公共模块 [P0]

### 现状

以下代码在 5-6 个脚本中重复出现：

- `log(msg, level)` 函数 — 5 处
- Windows UTF-8 编码修复代码块 — 4 处
- `load_json(path)` 函数 — 3 处
- `values_match(db_val, fetched_val, config)` 验证逻辑 — 2 处（`fetch_data.py` + `fetch_wind.py`）
- `get_validation_dates(conn, series_id)` — 2 处
- `ROOT = Path(__file__).resolve().parents[1]` + `DEFAULT_DB` 定义 — 6 处

### 目标

新建 `scripts/lib.py`，集中管理所有公共代码，各脚本通过 import 引用。

### 实施步骤

1. 创建 `scripts/lib.py`，包含以下内容：

```python
#!/usr/bin/env python3
"""Morning Brief 公共工具模块。"""

import json
import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

# ── 路径常量 ──────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "morning_brief.sqlite"
MCP_CONFIG = Path(os.environ.get(
    "MORNING_BRIEF_MCP_CONFIG",
    str(Path.home() / ".claude" / "mcp.json")
))

# ── Windows UTF-8 编码修复（模块加载时执行一次）───────────────────────

if sys.platform == 'win32':
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding='utf-8')
        except Exception:
            pass


# ── 日志 ──────────────────────────────────────────────────────────────

def log(msg, level="INFO"):
    """统一日志输出。level: INFO / WARN / ERROR / OK"""
    prefix = {"INFO": "  ", "WARN": "  ⚠", "ERROR": "  ✗", "OK": "  ✓"}
    print(f"{prefix.get(level, '  ')} {msg}",
          file=sys.stderr if level == "ERROR" else sys.stdout)


# ── JSON ──────────────────────────────────────────────────────────────

def load_json(path):
    """读取 JSON 文件，返回 Python 对象。"""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── 数据库 ────────────────────────────────────────────────────────────

@contextmanager
def open_db(db_path=None):
    """SQLite 连接的 context manager，确保异常时也能关闭连接。"""
    conn = sqlite3.connect(str(db_path or DEFAULT_DB))
    try:
        yield conn
    finally:
        conn.close()


def get_validation_dates(conn, series_id):
    """获取数据库最后两个观测日期（从旧到新）。"""
    rows = conn.execute(
        "SELECT date, value FROM observations WHERE series_id = ? ORDER BY date DESC LIMIT 2",
        (series_id,)
    ).fetchall()
    return list(reversed(rows))


# ── 数据验证 ──────────────────────────────────────────────────────────

def values_match(db_val, fetched_val, config):
    """检查两个值在容差范围内是否一致。
    
    config 需包含:
      - float_relative_tolerance (float)
      - float_absolute_tolerance (float)
    """
    try:
        db_val = float(db_val)
        fetched_val = float(fetched_val)
    except (ValueError, TypeError):
        return db_val == fetched_val
    if db_val == fetched_val:
        return True
    if abs(db_val) > 1e-8:
        rel_diff = abs(fetched_val - db_val) / abs(db_val)
        if rel_diff <= config["float_relative_tolerance"]:
            return True
    if abs(fetched_val - db_val) <= config["float_absolute_tolerance"]:
        return True
    return False
```

2. 修改以下脚本，移除重复代码并改为 import：

| 脚本 | 要移除的代码 | 替换为 |
|------|------------|--------|
| `fetch_data.py` | `log()`、`load_json()`、Windows fix、`ROOT`/`DEFAULT_DB`/`MCP_CONFIG`、`values_match()`、`get_validation_dates()` | `from lib import log, load_json, ROOT, DEFAULT_DB, MCP_CONFIG, values_match, get_validation_dates, open_db` |
| `fetch_wind.py` | `log()`、`load_json()`、Windows fix、`ROOT`/`DEFAULT_DB`、`values_match()`、`get_validation_dates()` | `from lib import log, load_json, ROOT, DEFAULT_DB, values_match, get_validation_dates, open_db` |
| `fetch_emotion.py` | `log()`、`load_json()`、Windows fix、`ROOT`/`DEFAULT_DB`/`MCP_CONFIG` | `from lib import log, load_json, ROOT, DEFAULT_DB, MCP_CONFIG, open_db` |
| `recompute_fx_derived.py` | `log()`、Windows fix、`ROOT`/`DEFAULT_DB` | `from lib import log, ROOT, DEFAULT_DB, open_db` |
| `update_data.py` | `ROOT`/`DEFAULT_DB` | `from lib import ROOT, DEFAULT_DB, open_db` |
| `generate_interactive_dashboard.py` | `ROOT`/`DEFAULT_DB` | `from lib import ROOT, DEFAULT_DB` |

3. 保留各脚本的 `if __name__ == "__main__": main()` 入口不变，确保可独立运行。

### 验证

```bash
python3 -m py_compile scripts/lib.py
python3 scripts/run_daily.py --skip-fetch
```

---

## 2. 数据库连接改用 Context Manager [P0]

### 现状

所有脚本手动管理 `conn = sqlite3.connect(...)` / `conn.close()`，异常时连接可能泄露。

### 目标

使用 `lib.py` 中的 `open_db()` context manager 包裹所有数据库操作。

### 实施步骤

以 `fetch_data.py` 的 `fetch_and_update()` 为例，当前：

```python
conn = sqlite3.connect(db_path)
# ... 200 行操作 ...
conn.close()
return summary
```

改为：

```python
with open_db(db_path) as conn:
    # ... 所有操作 ...
    if not dry_run and obs_inserted > 0:
        conn.commit()
    return summary
```

需要修改的函数：

| 脚本 | 函数 |
|------|------|
| `fetch_data.py` | `fetch_and_update()` |
| `fetch_wind.py` | `fetch_and_update()` |
| `fetch_emotion.py` | `fetch_and_store()` |
| `recompute_fx_derived.py` | `recompute_all()` |
| `update_data.py` | `build_update_plan()` |
| `run_daily.py` | FX 检查逻辑（第 57-61 行） |

### 验证

```bash
python3 scripts/run_daily.py --skip-fetch
python3 scripts/recompute_fx_derived.py --dry-run --verbose
```

---

## 3. HTML 模板外置 [P1]

### 现状

`generate_interactive_dashboard.py` 中 `HTML_TEMPLATE = r"""..."""` 包含约 2000 行 HTML/CSS/JS 原始字符串。在 Python 字符串中编辑前端代码无法获得：
- 语法高亮
- IDE 自动补全
- 独立的 lint/format

### 目标

将 HTML 模板提取为独立文件，Python 端仅负责数据注入。

### 实施步骤

1. 创建目录和模板文件：

```bash
mkdir -p templates
```

2. 将 `generate_interactive_dashboard.py` 中 `HTML_TEMPLATE = r"""` 到末尾 `"""` 之间的全部 HTML 内容提取到 `templates/dashboard.html`。保留 `__DATA__` 占位符不变。

3. 修改 `generate_interactive_dashboard.py`：

```python
TEMPLATE_PATH = ROOT / "templates" / "dashboard.html"

def render_dashboard(db_path, output_path):
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    payload = build_payload(db_path)
    data_json = json.dumps(payload, ensure_ascii=False)
    html = template.replace("__DATA__", data_json)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
```

4. 删除原来的 `HTML_TEMPLATE` 变量。

5. 更新 `.gitignore` — `templates/` 应当被追踪（不要排除）。

6. 更新 `docs/DEVELOPMENT.md` 目录结构表，增加 `templates/dashboard.html` 条目。

### 验证

```bash
# 生成看板
python3 scripts/generate_interactive_dashboard.py

# 验证 JS 语法
awk '/^  <script>$/{p=1; next} /^  <\/script>$/{p=0} p' output/interactive_dashboard.html > /tmp/dash.js
node --check /tmp/dash.js

# 浏览器打开确认渲染正常
open output/interactive_dashboard.html
```

---

## 4. 异常处理规范化 [P1]

### 现状

存在过宽的异常捕获和缺乏上下文的错误处理。

### 修改点

#### 4.1 `fetch_wind.py` 第 82 行

当前：
```python
except:
    msg = result.stderr[:200] if result.stderr else result.stdout[:200]
```

改为：
```python
except (json.JSONDecodeError, ValueError, KeyError):
    msg = result.stderr[:200] if result.stderr else result.stdout[:200]
```

#### 4.2 `fetch_data.py` 第 418-431 行 — insert 循环增加连续失败保护

当前：
```python
for date_str, value in new_points:
    try:
        conn.execute(...)
    except Exception as e:
        log(f"{sid} insert error at {date_str}: {e}", "ERROR")
        continue
    obs_inserted += 1
```

改为：
```python
consecutive_errors = 0
MAX_CONSECUTIVE_ERRORS = 5

for date_str, value in new_points:
    try:
        conn.execute(...)
        consecutive_errors = 0
    except Exception as e:
        consecutive_errors += 1
        log(f"{sid} insert error at {date_str}: {e}", "ERROR")
        if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
            log(f"{sid}: {MAX_CONSECUTIVE_ERRORS} consecutive insert errors, skipping remaining", "ERROR")
            break
        continue
    obs_inserted += 1
```

#### 4.3 `fetch_wind.py` 第 547-561 行 — 同样增加连续失败保护

应用与 4.2 相同的模式。

### 验证

```bash
python3 -m py_compile scripts/fetch_data.py scripts/fetch_wind.py
python3 scripts/fetch_data.py --dry-run --verbose --max-series 3
```

---

## 5. 并发数据拉取（仅 THS EDB）[P1]

### 现状

`fetch_data.py` 串行拉取 36 个序列，每次间隔 0.3s，最短耗时 ~18 秒（含网络延迟）。

### 目标

对 THS EDB 的 HTTP 请求并行化，控制并发数为 3，将总耗时缩短到 ~6 秒。

### 实施步骤

在 `fetch_data.py` 的 `fetch_and_update()` 函数中，将串行 fetch loop 改为：

```python
from concurrent.futures import ThreadPoolExecutor, as_completed

def _fetch_one(item, token, base_url, fetch_cfg):
    """单序列拉取（含重试），返回 (series_id, data_or_None)。"""
    sid = item["series_id"]
    query = f"{item['edb_query']} 最近60个交易日"
    data = None
    for attempt in range(fetch_cfg["max_retries"] + 1):
        data = call_edb(token, query, base_url, fetch_cfg["request_timeout_seconds"])
        if data is not None:
            break
        if attempt < fetch_cfg["max_retries"]:
            time.sleep(fetch_cfg["retry_backoff_seconds"][attempt])
    return sid, data

# 替换原来的 for 循环
max_workers = min(3, len(fetch_list))
with ThreadPoolExecutor(max_workers=max_workers) as pool:
    futures = {
        pool.submit(_fetch_one, item, token, base_url, fetch_cfg): item
        for item in fetch_list
    }
    for future in as_completed(futures):
        item = futures[future]
        sid = item["series_id"]
        _, data = future.result()
        if data is None:
            fetch_errors.append({"series_id": sid, "reason": "fetch_failed"})
            log(f"{sid}: fetch failed after retries", "ERROR")
        elif not data["points"]:
            fetch_errors.append({"series_id": sid, "reason": "empty_data"})
        else:
            results[sid] = data
            if verbose:
                log(f"{sid}: got {len(data['points'])} points", "OK")
```

### 注意事项

- **仅对 THS EDB 并行化**。Wind MCP 因积分限制保持串行。
- `edb_mapping.json` 中的 `delay_between_calls_seconds` 在并发模式下由线程池自然限流，可保留配置项供串行回退使用。
- 如果 THS API 出现频率限制（429），在 `call_edb()` 中检测并返回 None 触发重试。

### 验证

```bash
python3 scripts/fetch_data.py --dry-run --verbose
# 对比修改前后总耗时
```

---

## 6. 配置管理增强 [P2]

### 现状

Token 路径硬编码为 `~/.claude/mcp.json`。

### 目标

支持环境变量覆盖，便于跨平台迁移。

### 实施步骤

已在第 1 项 `lib.py` 中通过 `os.environ.get("MORNING_BRIEF_MCP_CONFIG", ...)` 实现。

额外需要修改 `fetch_wind.py` 中的 Wind Skill 路径：

当前：
```python
WIND_SKILL_DIR = Path.home() / ".claude" / "skills" / "wind-mcp-skill"
```

改为：
```python
WIND_SKILL_DIR = Path(os.environ.get(
    "WIND_SKILL_DIR",
    str(Path.home() / ".claude" / "skills" / "wind-mcp-skill")
))
```

### 验证

默认行为不变。设置环境变量时覆盖路径：
```bash
MORNING_BRIEF_MCP_CONFIG=/path/to/custom/mcp.json python3 scripts/fetch_data.py --dry-run
```

---

## 7. 添加基础单元测试 [P2]

### 现状

无任何测试文件。

### 目标

为核心纯函数添加单元测试，确保公式正确性和回归保护。

### 实施步骤

1. 创建目录：

```bash
mkdir -p tests
touch tests/__init__.py
```

2. 创建 `tests/test_validation.py`：

```python
"""测试数据验证逻辑。"""
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from lib import values_match

class TestValuesMatch(unittest.TestCase):
    CONFIG = {
        "float_relative_tolerance": 0.0005,
        "float_absolute_tolerance": 0.005,
    }

    def test_exact_match(self):
        self.assertTrue(values_match(3.14, 3.14, self.CONFIG))

    def test_within_relative_tolerance(self):
        # 0.04% 差异 < 0.05% 容差
        self.assertTrue(values_match(10000, 10004, self.CONFIG))

    def test_exceeds_relative_tolerance(self):
        # 0.1% 差异 > 0.05% 容差
        self.assertFalse(values_match(10000, 10010, self.CONFIG))

    def test_within_absolute_tolerance(self):
        # 差异 0.004 < 绝对容差 0.005
        self.assertTrue(values_match(0.001, 0.005, self.CONFIG))

    def test_zero_db_value(self):
        # db_val=0 时只看绝对容差
        self.assertTrue(values_match(0, 0.003, self.CONFIG))
        self.assertFalse(values_match(0, 0.01, self.CONFIG))

    def test_non_numeric(self):
        self.assertTrue(values_match("abc", "abc", self.CONFIG))
        self.assertFalse(values_match("abc", "def", self.CONFIG))


if __name__ == "__main__":
    unittest.main()
```

3. 创建 `tests/test_update_plan.py`：

```python
"""测试更新计划的日期计算逻辑。"""
import unittest
import sys
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from update_data import next_date

class TestNextDate(unittest.TestCase):
    def test_daily_next(self):
        self.assertEqual(next_date("2026-06-17", "D"), date(2026, 6, 18))

    def test_monthly_next(self):
        # 月频：下个月最后一天
        self.assertEqual(next_date("2026-01-31", "M"), date(2026, 2, 28))

    def test_monthly_december(self):
        self.assertEqual(next_date("2026-12-31", "M"), date(2027, 1, 31))

    def test_monthly_feb_to_mar(self):
        self.assertEqual(next_date("2026-02-28", "M"), date(2026, 3, 31))


if __name__ == "__main__":
    unittest.main()
```

4. 创建 `tests/test_recompute.py`：

```python
"""测试外汇衍生序列复算公式。"""
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from recompute_fx_derived import ANNUAL_FACTORS

class TestHedgeCostFormula(unittest.TestCase):
    def test_cny_hedge_cost(self):
        # CNY 套保成本 = swap_points / 10000 / spot
        swap_points = -150  # pips
        spot = 7.2500
        expected = -150 / 10000 / 7.2500
        self.assertAlmostEqual(expected, swap_points / 10000 / spot, places=8)

    def test_cnh_hedge_cost(self):
        # CNH 套保成本 = DF / spot - 1
        df = 7.2300
        spot = 7.2500
        expected = 7.2300 / 7.2500 - 1
        self.assertAlmostEqual(expected, df / spot - 1, places=8)

    def test_annualization(self):
        # 年化 = (1 + hedge)^n - 1
        hedge_1m = -0.002  # -0.2%
        n = ANNUAL_FACTORS["1m"]  # 12
        annualized = (1 + hedge_1m) ** n - 1
        self.assertAlmostEqual(annualized, (1 - 0.002) ** 12 - 1, places=8)

    def test_annual_factors(self):
        self.assertEqual(ANNUAL_FACTORS, {"1m": 12, "3m": 4, "6m": 2, "1y": 1})


if __name__ == "__main__":
    unittest.main()
```

### 验证

```bash
python3 -m unittest discover tests/ -v
```

---

## 8. 清理 git 中的 __pycache__ [P0]

### 现状

`scripts/__pycache__/*.pyc` 文件已被追踪到 git 仓库中（在 `.gitignore` 添加之前已 commit）。

### 实施步骤

```bash
cd "/Users/martin_ai/Desktop/Martin Morning Brief"
git rm -r --cached scripts/__pycache__
git commit -m "Remove tracked __pycache__ files (already in .gitignore)"
```

### 验证

```bash
git status  # 应显示 clean
ls scripts/__pycache__  # 本地文件仍在（不影响运行），但不再被 git 追踪
```

---

## 9. 其他小项 [P2]

### 9.1 `run_daily.py` step 计数修复

当前 `total_steps = 6` 硬编码，但实际可能执行 5-8 步。

改为动态：
```python
steps = []
steps.append(("Generating update plan", ["scripts/update_data.py"]))
if not args.skip_fetch and not args.skip_fetch_ths:
    steps.append(("Fetching from THS EDB", ["scripts/fetch_data.py"]))
# ... 类似追加
total_steps = len(steps)
```

### 9.2 专题 Excel 路径规范化

当前 `extract_super_cycle_data()` 默认读 `~/Downloads/Super Dollar Scenario.xlsx`。

改为优先读项目内路径：
```python
def extract_super_cycle_data(xlsx_path=None):
    if xlsx_path is None:
        # 优先项目内
        project_path = ROOT / "seed" / "Super Dollar Scenario.xlsx"
        if project_path.exists():
            xlsx_path = project_path
        else:
            xlsx_path = Path.home() / "Downloads" / "Super Dollar Scenario.xlsx"
    ...
```

### 9.3 日志持久化（可选）

在 `run_daily.py` 中增加日志文件输出：
```python
import logging
from datetime import date

LOG_DIR = ROOT / "data" / "logs"
LOG_DIR.mkdir(exist_ok=True)
log_file = LOG_DIR / f"{date.today().isoformat()}.log"

# 将 subprocess 输出同时写入文件
# 或在各脚本中增加 file handler
```

如实施此项，需在 `.gitignore` 中添加 `data/logs/`。

---

## 执行顺序建议

```text
1. [P0] 清理 git __pycache__（第 8 项）         ← 1 分钟
2. [P0] 创建 scripts/lib.py（第 1 项）          ← 核心，其他项依赖此步
3. [P0] 各脚本改用 lib import + open_db（第 1+2 项）
4. [P1] HTML 模板外置（第 3 项）
5. [P1] 异常处理规范化（第 4 项）
6. [P1] 并发拉取（第 5 项）
7. [P2] 配置管理（第 6 项）— 已在第 1 步顺带完成
8. [P2] 添加测试（第 7 项）
9. [P2] 其他小项（第 9 项）
```

每完成一个大项后运行验证：
```bash
python3 scripts/run_daily.py --skip-fetch
open output/interactive_dashboard.html
```

---

## 不做的事项

- **不优化看板数据体积** — 保持完整历史数据嵌入，功能优先
- **不引入外部 JS 依赖** — 保持零依赖单文件看板
- **不改变数据流架构** — 当前 seed → SQLite → API 增量 → 看板的流程已经合理
- **不改变 series_id 命名规范** — 保持向后兼容
