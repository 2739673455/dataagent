import argparse
import asyncio
from datetime import datetime
from typing import Callable

import httpx
import jieba.analyse
from config import CFG
from state_manage import read_state, write_state


async def add_context(
    query: str,
    r_state: Callable | None = None,
    w_state: Callable | None = None,
):
    """添加上下文信息:写入查询文本、关键词、当前日期信息、表信息等"""

    def is_numeric(s) -> bool:
        """判断字符串是否为数值"""
        try:
            float(s)
            return True
        except (ValueError, TypeError):
            return False

    db_code = CFG.use_db_code
    get_table_url = CFG.meta_db.get_table_url

    # 提取关键词
    allow_pos = (
        "n",  # 名词: 数据、服务器、表格
        "nr",  # 人名: 张三、李四
        "ns",  # 地名: 北京、上海
        "nt",  # 机构团体名: 政府、学校、某公司
        "nz",  # 其他专有名词: Unicode、哈希算法、诺贝尔奖
        "v",  # 动词: 运行、开发
        "vn",  # 名动词: 工作、研究
        "a",  # 形容词: 美丽、快速
        "an",  # 名形词: 难度、合法性、复杂度
        "eng",  # 英文
        "i",  # 成语
        "l",  # 常用固定短语
    )
    keywords = jieba.analyse.extract_tags(
        query, withWeight=False, allowPOS=allow_pos
    ) + [query]
    keywords = list(set([w for w in keywords if not is_numeric(w)]))

    # 获取当前日期信息
    today = datetime.today()
    today_date = today.strftime("%Y-%m-%d")
    today_weekday = today.strftime("%A")
    cur_date_info = "当前日期信息:%s,%s" % (today_date, today_weekday)

    # 获取表信息
    async with httpx.AsyncClient() as client:
        response = await client.post(get_table_url, json={"db_code": db_code})
    db_info, tb_map = response.json()

    # 构建表的说明信息
    tb_caption = f"数据库: {db_info['db_name']}\n" + "".join(
        [f"表名: {i['tb_name']}，表含义: {i['tb_meaning']}\n" for i in tb_map.values()]
    )

    if w_state:
        await w_state(
            {
                "db_code": db_code,
                "query": query,  # 查询
                "keywords": keywords,  # 关键词
                "cur_date_info": cur_date_info,  # 当前日期信息
                "tb_map": tb_map,  # 表信息
                "tb_caption": tb_caption,  # 表说明
            }
        )


async def main():
    usage = "python add_context.py 查询文本"
    parser = argparse.ArgumentParser(usage=usage)
    parser.add_argument("query", type=str, help="查询文本")

    args = parser.parse_args()
    await add_context(args.query, read_state, write_state)


if __name__ == "__main__":
    asyncio.run(main())
