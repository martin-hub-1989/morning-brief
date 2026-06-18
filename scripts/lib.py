#!/usr/bin/env python3
"""Morning Brief 公共工具模块。

供所有 scripts/ 下的脚本 import 使用，避免重复代码。
"""

import json
import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

# ── 路径常量 ──────────────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "morning_brief.sqlite"
MCP_CONFIG = Path(
    os.environ.get("MORNING_BRIEF_MCP_CONFIG", str(Path.home() / ".claude" / "mcp.json"))
)

# ── Windows UTF-8 编码修复（模块加载时执行一次）───────────────────────

if sys.platform == "win32":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass


# ── 日志 ──────────────────────────────────────────────────────────────

def log(msg, level="INFO"):
    """统一日志输出。level: INFO / WARN / ERROR / OK"""
    prefix = {"INFO": "  ", "WARN": "  ⚠", "ERROR": "  ✗", "OK": "  ✓"}
    print(
        f"{prefix.get(level, '  ')} {msg}",
        file=sys.stderr if level == "ERROR" else sys.stdout,
    )


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
    """获取数据库最后两个观测日期和值（从旧到新）。"""
    rows = conn.execute(
        "SELECT date, value FROM observations WHERE series_id = ? ORDER BY date DESC LIMIT 2",
        (series_id,),
    ).fetchall()
    return list(reversed(rows))  # [(date, value), ...] oldest first


# ── 数据验证 ──────────────────────────────────────────────────────────

def values_match(db_val, fetched_val, config, category=None):
    """检查两个值在容差范围内是否一致。支持 category 级别容差覆盖。

    config 需包含:
      - float_relative_tolerance (float)
      - float_absolute_tolerance (float)
      - category_overrides (dict, optional): 按 category 覆盖容差

    容差覆盖示例（来自 wind_mapping.json）:
      "category_overrides": {
        "fx_swap": {"float_absolute_tolerance": 50},
        "bond_yield": {"float_absolute_tolerance": 0.5}
      }
    """
    try:
        db_val = float(db_val)
        fetched_val = float(fetched_val)
    except (ValueError, TypeError):
        return db_val == fetched_val
    if db_val == fetched_val:
        return True

    # Apply category-level tolerance overrides
    overrides = config.get("category_overrides", {})
    rel_tol = config["float_relative_tolerance"]
    abs_tol = config["float_absolute_tolerance"]
    if category and category in overrides:
        override = overrides[category]
        if "float_relative_tolerance" in override:
            rel_tol = override["float_relative_tolerance"]
        if "float_absolute_tolerance" in override:
            abs_tol = override["float_absolute_tolerance"]

    if abs(db_val) > 1e-8:
        rel_diff = abs(fetched_val - db_val) / abs(db_val)
        if rel_diff <= rel_tol:
            return True
    if abs(fetched_val - db_val) <= abs_tol:
        return True
    return False
