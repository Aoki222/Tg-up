"""SQLite 查询按表分开放。

连接和建表在 database/。这里不调用 Telegram，也不处理 HTTP。
upload_tasks 与 upload_slices 仍由 TaskRepository 承担，避免一次改完所有调用方。
"""
