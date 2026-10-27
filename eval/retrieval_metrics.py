"""Retrieval metrics on the dev question set (PLAN section 6; docs/schema_v1.md section 3).

source_ids are already at :DonViNoiDung level, and so are the retrieved unit ids, so no mapping is
needed. Questions without source_ids (out_of_scope) are excluded from Recall/Hit/MRR.
"""

import json
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEV_PATH = REPO_ROOT / "data" / "eval" / "questions_v1_dev.jsonl"
GROUP_KEYS = ("origin", "type", "delegated")


def load_dev_questions(path: Path = DEV_PATH) -> list[dict]:
    """Read the dev split only; every record must say split == "dev"."""
    if not path.name.endswith("_dev.jsonl"):
        raise ValueError(f"refusing to read {path.name}: only the dev split may be used here")
    questions = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for q in questions:
        if q.get("split") != "dev":
            raise ValueError(f"{q.get('id')}: split is {q.get('split')!r}, expected 'dev'")
    return questions


def is_scored(question: dict) -> bool:
    return bool(question["source_ids"])


def first_hit_rank(retrieved: list[str], sources: list[str]) -> int | None:
    """1-based rank of the first correct unit in retrieved, or None if absent."""
    wanted = set(sources)
    return next((i for i, uid in enumerate(retrieved, start=1) if uid in wanted), None)


def recall_at(retrieved: list[str], sources: list[str], k: int) -> float:
    return len(set(sources) & set(retrieved[:k])) / len(set(sources))


def hit_at(retrieved: list[str], sources: list[str], k: int) -> float:
    return 1.0 if set(sources) & set(retrieved[:k]) else 0.0


def reciprocal_rank(retrieved: list[str], sources: list[str], k: int = 10) -> float:
    rank = first_hit_rank(retrieved[:k], sources)
    return 1.0 / rank if rank else 0.0


def score_question(question: dict, retrieved: list[str]) -> dict:
    """Per-question metrics; out_of_scope questions get None everywhere."""
    if not is_scored(question):
        return {"scored": False, "first_hit_rank": None, "recall@5": None, "recall@10": None,
                "hit@5": None, "rr": None}
    src = question["source_ids"]
    return {
        "scored": True,
        "first_hit_rank": first_hit_rank(retrieved[:10], src),
        "recall@5": recall_at(retrieved, src, 5),
        "recall@10": recall_at(retrieved, src, 10),
        "hit@5": hit_at(retrieved, src, 5),
        "rr": reciprocal_rank(retrieved, src, 10),
    }


def summarise(rows: list[dict]) -> dict:
    """Mean Recall@5, Recall@10, Hit@5, MRR over scored rows; n = number of scored questions."""
    scored = [r for r in rows if r["scored"]]
    n = len(scored)
    mean = lambda key: round(sum(r[key] for r in scored) / n, 4) if n else None  # noqa: E731
    return {"n": n, "recall@5": mean("recall@5"), "recall@10": mean("recall@10"), "hit@5": mean("hit@5"),
            "mrr": mean("rr")}


def breakdown(rows: list[dict]) -> dict:
    """Summaries for all rows and per value of origin, type and delegated."""
    out = {"total": summarise(rows), "excluded_out_of_scope": sum(not r["scored"] for r in rows)}
    for key in GROUP_KEYS:
        groups = defaultdict(list)
        for r in rows:
            groups[str(r[key]).lower()].append(r)
        out[key] = {value: summarise(group) for value, group in sorted(groups.items())}
    return out


def compare_ranks(rows_a: list[dict], rows_b: list[dict]) -> dict:
    """Per question (scored only): is A's first correct unit ranked better, worse or the same as B's?"""
    by_id_b = {r["question_id"]: r for r in rows_b}
    better = worse = same = 0
    for a in rows_a:
        if not a["scored"]:
            continue
        b = by_id_b[a["question_id"]]
        rank_a = a["first_hit_rank"] or 11  # not in top-10 ranks after every hit
        rank_b = b["first_hit_rank"] or 11
        better += rank_a < rank_b
        worse += rank_a > rank_b
        same += rank_a == rank_b
    return {"a_better": better, "a_worse": worse, "same": same}
