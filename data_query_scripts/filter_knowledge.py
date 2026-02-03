import asyncio
from typing import Callable

from config import CFG
from state_manage import read_state, write_state
from util import ask_llm, get_prompt, kn_info_xml_str, parse_json


async def filter_knowledge(
    r_state: Callable | None = None,
    w_state: Callable | None = None,
):
    """LLM筛选指标知识"""
    state = await r_state() if r_state else {}
    query: str = state["query"]
    retrieved_knowledge: dict[int, dict] = {
        int(k): v for k, v in state["retrieved_knowledge"].items()
    }
    filter_model = CFG.llm.filter_model

    if not retrieved_knowledge:
        print("knowledge is empty")
        return

    prompt = get_prompt(
        "table_rag",
        "knowledge_filter_prompt",
        knowledge_info=kn_info_xml_str(retrieved_knowledge),
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
    filtered_kn_codes = parse_json(resp)
    needed_kn_codes = {i for i in filtered_kn_codes if i in retrieved_knowledge}
    # 补充可能在过滤中遗漏掉的关联知识
    while True:
        add_kn_codes: set[int] = {
            rel_code
            for kn_code in needed_kn_codes
            for rel_code in (retrieved_knowledge[kn_code].get("rel_kn") or [])
            if rel_code not in needed_kn_codes
        }
        if not add_kn_codes:
            break
        needed_kn_codes = needed_kn_codes | add_kn_codes
    kn_map = {k: retrieved_knowledge[k] for k in needed_kn_codes}

    if w_state:
        await w_state({"kn_map": kn_map})


if __name__ == "__main__":
    asyncio.run(filter_knowledge(read_state, write_state))
