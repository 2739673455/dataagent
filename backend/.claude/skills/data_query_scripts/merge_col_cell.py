import asyncio
from typing import Callable

from config import CFG
from state_manage import read_state, write_state


async def merge_col_cell(
    r_state: Callable | None = None,
    w_state: Callable | None = None,
):
    """合并字段与单元格信息，并根据检索分数截取topk表和字段"""
    state = await r_state() if r_state else {}
    retrieved_col_map: dict[str, dict[str, dict]] = state["retrieved_col_map"]
    retrieved_cell_map: dict[str, dict[str, dict]] = state["retrieved_cell_map"]
    max_tb_num = CFG.max_tb_num
    max_col_per_tb = CFG.max_col_per_tb

    # 将单元格信息与字段信息合并
    for tb_code, cell_name_col_map in retrieved_cell_map.items():
        name_col_map = retrieved_col_map.setdefault(tb_code, {})
        for col_name, cell_col_dict in cell_name_col_map.items():
            if col_name not in name_col_map:
                # col_map 中原本没有此列则添加
                name_col_map[col_name] = cell_col_dict
            else:
                # col_map 中原本有此列则更新
                col_dict = name_col_map[col_name]
                col_dict["cells"] = list(
                    set(col_dict.get("cells", []) + cell_col_dict["cells"])
                )
                col_dict["score"] = max(
                    cell_col_dict["score"], col_dict["score"]
                )  # 取最高分

    # 根据检索分数截取topk表和字段
    tb_score_list: list[tuple] = []  # 存储表编号和对应的总分数
    for tb_code, n_c_map in retrieved_col_map.items():
        # 字段数量如果大于设定值，则按字段分数降序排序，截取topk个字段
        if len(n_c_map) > max_col_per_tb:
            n_c_map = dict(
                sorted(n_c_map.items(), key=lambda x: x[1]["score"], reverse=True)[
                    :max_col_per_tb
                ]
            )  # 按分数降序排序，截取topk个字段
            retrieved_col_map[tb_code] = n_c_map  # 更新字段字典
        # 此表所有字段分数之和作为表的总分数
        tb_score_list.append((tb_code, sum(i["score"] for i in n_c_map.values())))
    if len(tb_score_list) > max_tb_num:
        # 按表的总分数降序排序，并从字典中删除超出数量的表
        tb_score_list.sort(key=lambda x: x[1], reverse=True)
        for tb_code, _ in tb_score_list[max_tb_num:]:
            del retrieved_col_map[tb_code]

    if w_state:
        await w_state({"col_map": retrieved_col_map})


if __name__ == "__main__":
    asyncio.run(merge_col_cell(read_state, write_state))
