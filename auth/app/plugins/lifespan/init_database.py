from app.init_db import prepare


def init_database():
    """如果数据库不存在则自动初始化"""
    db_initializer, dbs = prepare()

    # 检查每个数据库是否存在，不存在则初始化
    need_init = []
    for i in dbs:
        db_name = i["db_name"]
        exists = db_initializer.check_db_exists(db_name)
        if not exists:
            need_init.append(i)
    if need_init:
        db_initializer.init_db(need_init)
