"""统一的日志配置。

提供 get_logger() 获取带控制台 + 滚动文件输出的 logger。
日志文件默认放在 uploader/logs/app.log。
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 日志目录：PROJECT_DIR/logs（与 .gitignore 的 logs/ 对齐，替代旧的 src/logs）
_LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"
_LOG_FILE = _LOGS_DIR / "app.log"

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def _ensure_configured() -> None:
    """全局只配置一次：根 logger 添加控制台与滚动文件 handler。"""
    global _configured
    if _configured:
        return

    _LOGS_DIR.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        _LOG_FILE,
        maxBytes=5 * 1024 * 1024,  # 5MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    _configured = True


def get_logger(name: str = "uploader") -> logging.Logger:
    """获取（已统一配置的）logger。"""
    _ensure_configured()
    return logging.getLogger(name)


def get_log_path() -> Path:
    """返回日志文件路径。"""
    return _LOG_FILE
