"""入库时写下调度用的排序键。查询只做普通比较，并带 LIMIT。"""

from __future__ import annotations

import os
import re
from pathlib import Path

# 集数、时间戳和雪花 id 都落在 18 位以内。更长的数字不再补零，字典序可能再次偏离自然序。
_DIGIT_WIDTH = 18
_DIGITS = re.compile(r"\d+")

# 读不到落盘时间时用它。比真实 Unix 时间大，升序时排在后面。
UNKNOWN_SOURCE_BORN = 9.0e15

SCHEDULE_ORDERS = ("oldest", "natural", "created", "smallest")


def canonical_schedule_order(value: object) -> str:
    """缺省和无法识别的值都是入库顺序。小文件优先仍认 smallest。"""
    text = str(value or "").strip()
    if text in {"natural", "created", "smallest"}:
        return text
    return "oldest"


def make_natural_key(text: str) -> str:
    """路径里的连续数字补到固定宽度，SQLite 的字典序就和自然序一致。"""
    lowered = text.replace("\\", "/").casefold()
    return _DIGITS.sub(lambda match: match.group(0).zfill(_DIGIT_WIDTH), lowered)


def child_natural_key(parent_key: str, part_index: int) -> str:
    """分段排在源文件键后面，并按段号挨在一起。"""
    return f"{parent_key}/{int(part_index):0{_DIGIT_WIDTH}d}"


def extract_source_born_at(path: Path) -> float:
    """Windows 用创建时间。读不到创建时间时用修改时间。文件不在时返回哨兵。"""
    try:
        stat_result = path.stat()
    except OSError:
        return UNKNOWN_SOURCE_BORN
    birth = getattr(stat_result, "st_birthtime", None)
    if isinstance(birth, (int, float)) and birth > 0 and os.name != "nt":
        return float(birth)
    if os.name == "nt":
        created = birth if isinstance(birth, (int, float)) and birth > 0 else stat_result.st_ctime
        return float(created)
    return float(stat_result.st_mtime)
