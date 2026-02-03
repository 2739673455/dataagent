import asyncio
from typing import Callable

import httpx
from config import CFG
from state_manage import read_state, write_state


async def add_kn_col(
    r_state: Callable | None = None,
    w_state: Callable | None = None,
):
    """获取知识相关字段，并与之前检索出的字段合并"""
    state = await r_state() if r_state else {}
    db_code: str = state["db_code"]
    col_map: dict[str, dict[str, dict]] = state["col_map"]
    kn_map: dict[int, dict] = {int(k): v for k, v in state["kn_map"].items()}
    retrieve_column_url = CFG.meta_db.retrieve_column_url
    if not kn_map or all(not kn.get("rel_col") for kn in kn_map.values()):
        return

    # 获取知识的相关字段
    tb_col_tuple_set: set[tuple[str, str]] = set()
    for kn in kn_map.values():
        for tb_col in kn.get("rel_col", []):
            tb_name, col_name = tb_col.split(".")
            tb_col_tuple_set.add((tb_name, col_name))
    async with httpx.AsyncClient() as client:
        response = await client.post(
            retrieve_column_url,
            json={"db_code": db_code, "tb_col_tuple_list": list(tb_col_tuple_set)},
        )
        kn_rel_col_map = response.json()

    # 将知识的相关字段与召回的字段合并
    for tb_code, n_c_map in kn_rel_col_map.items():
        col_map.setdefault(tb_code, {}).update(n_c_map)

    if w_state:
        await w_state({"col_map": col_map})


if __name__ == "__main__":
    asyncio.run(add_kn_col(read_state, write_state))
