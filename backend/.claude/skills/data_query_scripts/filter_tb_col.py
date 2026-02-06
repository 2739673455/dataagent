import asyncio
from typing import Callable

from config import CFG
from state_manage import read_state, write_state
from util import ask_llm, get_prompt, parse_json, tb_col_xml_str


async def filter_tb_col(
    r_state: Callable | None = None,
    w_state: Callable | None = None,
):
    """LLM筛选与查询相关的表和字段，进一步精简上下文信息"""

    async def filter_table(tb_code_list: list[str]) -> list[str]:
        """使用LLM过滤表，返回与查询相关的表编号列表"""
        async with table_filter_semaphore:
            try:
                _col_map = {tb_code: col_map[tb_code] for tb_code in tb_code_list}
                prompt = get_prompt(
                    "table_rag",
                    "table_filter_prompt",
                    time_info=cur_date_info,
                    table_info=tb_col_xml_str(tb_map, _col_map),
                    query=query,
                )
                resp = await ask_llm(
                    filter_model,
                    [
                        {"role": "system", "content": prompt["system"]},
                        {"role": "user", "content": prompt["user"]},
                    ],
                    1,
                    20,
                )
                filtered_tb_codes = parse_json(resp)
                return filtered_tb_codes
            except Exception:
                raise

    async def filter_column(tb_code: str) -> tuple | None:
        """使用LLM过滤字段，返回单表所有和查询相关的字段"""
        async with column_filter_semaphore:
            try:
                _tb_map = {tb_code: tb_map[tb_code]}
                _col_map = {tb_code: col_map[tb_code]}
                prompt = get_prompt(
                    "table_rag",
                    "column_filter_prompt",
                    time_info=cur_date_info,
                    table_info=tb_col_xml_str(_tb_map, _col_map),
                    query=query,
                )
                resp = await ask_llm(
                    filter_model,
                    [
                        {"role": "system", "content": prompt["system"]},
                        {"role": "user", "content": prompt["user"]},
                    ],
                    1,
                    20,
                )
                res = parse_json(resp)
            except Exception:
                raise

            # 预期的返回格式：
            # {
            #     "related_flag": true,  # 表是否相关
            #     "column_names": [col_name1, col_name2] # 相关字段列表
            # }
            if "related_flag" not in res or "column_names" not in res:
                # 如果返回格式不合法，原样返回表字段信息
                return tb_code, col_map[tb_code]

            # 如果表不相关或没有选择任何字段，返回None
            if not res["related_flag"] or not res["column_names"]:
                return None

            # 字段筛选，根据LLM返回的字段名列表，筛选出相关字段
            filtered_col_dict = {
                col_name: col_map[tb_code][col_name]
                for col_name in res["column_names"]
                if col_name in col_map[tb_code]
            }
            return tb_code, filtered_col_dict

    state = await r_state() if r_state else {}
    query: str = state["query"]
    tb_map: dict[str, dict] = state["tb_map"]
    col_map: dict[str, dict[str, dict]] = state["col_map"]
    cur_date_info: str = state["cur_date_info"]
    filter_model = CFG.llm.filter_model

    max_concurrent = 20
    table_filter_semaphore = asyncio.Semaphore(max_concurrent)
    column_filter_semaphore = asyncio.Semaphore(max_concurrent)
    table_filter_batch_size = 5  # 每批次过滤的表数量

    # 第一阶段：过滤表
    _tb_code_list = list(col_map.keys())
    table_filter_tasks = [
        filter_table(_tb_code_list[i : i + table_filter_batch_size])  # 分批处理
        for i in range(0, len(col_map), table_filter_batch_size)
    ]  # 创建表过滤任务

    # 第二阶段：过滤字段
    column_filter_tasks = []
    for coro in asyncio.as_completed(table_filter_tasks):  # as_completed 流水线处理
        filtered_table_codes = await coro  # 等待表过滤任务完成
        if not filtered_table_codes:  # 跳过空的过滤结果
            continue
        for tb_code in filtered_table_codes:  # 为每个过滤出的表创建字段过滤任务
            if tb_code in col_map:
                column_filter_tasks.append(asyncio.create_task(filter_column(tb_code)))

    # 等待所有字段过滤任务完成，构建最终结果
    res: list[tuple | None] = await asyncio.gather(*column_filter_tasks)
    filtered_col_map: dict[str, dict[str, dict]] = dict(filter(None, res))

    if w_state:
        await w_state({"col_map": filtered_col_map})


if __name__ == "__main__":
    asyncio.run(filter_tb_col(read_state, write_state))
