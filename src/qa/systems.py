"""The two baseline systems: llm_only and rag_baseline (PLAN section 3).

Both call complete("answer", SYSTEM_PROMPT, ...) with the same prompt; rag_baseline adds the top_k
units retrieved by the dense index from configs/retrieval.toml (bge-m3, decision #4).
"""

from src.llm import LLMResult, complete
from src.qa.answer import SYSTEM_PROMPT, context_message, question_message

RETRIEVE_FOR_METRICS = 10  # Recall@10 / MRR are computed on the top-10; the prompt gets top_k of them


def _params(effort: str | None) -> dict | None:
    return {"effort": effort} if effort else None


def llm_only(question: str, run: int = 0, effort: str | None = None, complete_fn=complete) -> tuple[LLMResult, None]:
    result = complete_fn("answer", SYSTEM_PROMPT, [{"role": "user", "content": question_message(question)}],
                         run=run, params_override=_params(effort))
    return result, None


def rag_baseline(question: str, index, run: int = 0, effort: str | None = None,
                 complete_fn=complete) -> tuple[LLMResult, list[str]]:
    """index: a DenseIndex (from DenseIndex.from_config()) exposing search(), units and top_k."""
    hits = index.search(question, RETRIEVE_FOR_METRICS)
    retrieved = [uid for uid, _ in hits]
    by_id = {u["id"]: u for u in index.units}
    context = [by_id[uid] for uid in retrieved[: index.top_k]]
    result = complete_fn("answer", SYSTEM_PROMPT, [{"role": "user", "content": context_message(question, context)}],
                         run=run, params_override=_params(effort))
    return result, retrieved
