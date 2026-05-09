from app.init_db import prepare


async def init_database():
    """如果数据库不存在则自动初始化"""
    db_initializer, db_sql_orm = prepare()

    need_init = []
    for db_name, sql_file, output_path in db_sql_orm:
        exists = await db_initializer.check_db_exists(db_name)
        if not exists:
            need_init.append((db_name, sql_file, output_path))
    if need_init:
        await db_initializer.init_db(need_init)
