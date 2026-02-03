import asyncio
from typing import Callable

import httpx
from config import CFG
from state_manage import read_state, write_state


async def recall_knowledge(
    r_state: Callable | None = None,
    w_state: Callable | None = None,
):
    """混合检索知识"""
    state = await r_state() if r_state else {}
    db_code: str = state["db_code"]
    query: str = state["query"]
    keywords: list[str] = state["keywords"]
    retrieve_knowledge_url = CFG.meta_db.retrieve_knowledge_url

    async with httpx.AsyncClient() as client:
        response = await client.post(
            retrieve_knowledge_url,
            json={"db_code": db_code, "query": query, "keywords": keywords},
        )

    if w_state:
        await w_state({"retrieved_knowledge": response.json()})


if __name__ == "__main__":
    asyncio.run(recall_knowledge(read_state, write_state))
