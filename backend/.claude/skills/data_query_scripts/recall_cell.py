import asyncio
from typing import Callable

import httpx
from config import CFG
from state_manage import read_state, write_state


async def recall_cell(
    r_state: Callable | None = None,
    w_state: Callable | None = None,
):
    """检索字段信息"""
    state = await r_state() if r_state else {}
    db_code: str = state["db_code"]
    keywords: list[str] = state.get("extracted_cells") or state["keywords"]
    retrieve_cell_url = CFG.meta_db.retrieve_cell_url

    async with httpx.AsyncClient() as client:
        response = await client.post(
            retrieve_cell_url, json={"db_code": db_code, "keywords": keywords}
        )

    if w_state:
        await w_state({"retrieved_cell_map": response.json()})


if __name__ == "__main__":
    asyncio.run(recall_cell(read_state, write_state))
