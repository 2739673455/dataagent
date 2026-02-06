import asyncio
from typing import Callable

from config import CFG
from state_manage import read_state, write_state
from util import ask_llm, get_prompt, parse_json


async def extend_column(
    r_state: Callable | None = None,
    w_state: Callable | None = None,
):
    """LLM 结合查询内容、关键词、表信息来扩展字段，生成查询可能用到的字段"""
    state = await r_state() if r_state else {}
    query: str = state["query"]
    keywords: list[str] = state["keywords"]
    tb_caption: str = state["tb_caption"]
    extend_model = CFG.llm.extend_model

    prompt = get_prompt(
        "table_rag",
        "extend_column_prompt",
        query=query,
        keywords=keywords,
        table_caption=tb_caption,
    )
    resp = await ask_llm(
        extend_model,
        [
            {"role": "system", "content": prompt["system"]},
            {"role": "user", "content": prompt["user"]},
        ],
        1,
        5,
    )
    extend_columns: list[str] = parse_json(resp)
    extracted_columns = list(set([i for i in extend_columns + keywords]))

    if w_state:
        await w_state({"extracted_columns": extracted_columns})


if __name__ == "__main__":
    asyncio.run(extend_column(read_state, write_state))
