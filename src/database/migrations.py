from .connection import get_db

async def get_table_columns(db, table_name: str) -> set[str]:
    async with db.execute(
        f"PRAGMA table_info({table_name})"
    ) as cursor:
        rows = await cursor.fetchall()
        
    return {row[1] for row in rows}


async def migrate_upload_tasks(db, schema):
    """
    检查 upload_tasks 表是否缺少字段。
    如果缺少，则自动添加。
    """

    # 获取数据库当前已有的字段
    existing_columns = await get_table_columns(
        db,
        "upload_tasks",
    )
    
    # 遍历代码中定义的字段
    for column_name, column_definition in schema.items():
        
        count = 0

        # 如果已经存在，就跳过
        if column_name in existing_columns:
            continue

        # 如果不存在，就添加
        await db.execute(
            f"""
            ALTER TABLE upload_tasks
            ADD COLUMN {column_name} {column_definition}
            """
        )
        count+=1
        
    return count