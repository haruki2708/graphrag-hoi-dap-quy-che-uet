"""Run a baseline system on the dev split and score retrieval and citations.

Run from the repo root:
    python eval/run_eval.py --system llm_only --ids dev-m001 dev-m002 --effort low
    python eval/run_eval.py --system rag_baseline --limit 5 --runs 1

Only the dev split is allowed (--split test is refused). Writes eval/results/<date>_<system>_dev.csv
(PLAN section 6; "_<effort>" is added only when --effort differs from the answer role's default) and a
.json with metadata (commit, configs, system prompt hash and text, library versions, tokens, cost).
Existing result files are never overwritten unless --overwrite is given.
cost_usd is computed from every row's tokens (cached rows keep the tokens of the call that produced them),
so a rerun served from cache reports the same cost; cost_usd_this_invocation counts only new calls.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env")  # HF_HOME before any HuggingFace import

import argparse  # noqa: E402
import csv  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
import tomllib  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
from importlib.metadata import version  # noqa: E402

from eval import retrieval_metrics as rm  # noqa: E402
from src.qa import citations as cit  # noqa: E402
from src.qa import systems  # noqa: E402
from src.qa.answer import SYSTEM_PROMPT, prompt_sha256  # noqa: E402

UNITS_PATH = REPO_ROOT / "data" / "processed" / "qc3626_content_units.jsonl"
LLM_CONFIG = REPO_ROOT / "configs" / "llm.toml"
RETRIEVAL_CONFIG = REPO_ROOT / "configs" / "retrieval.toml"
RESULTS_DIR = REPO_ROOT / "eval" / "results"
SYSTEMS = ("llm_only", "rag_baseline")
VN_TZ = timezone(timedelta(hours=7))

CSV_FIELDS = ["id", "origin", "type", "delegated", "source_ids", "retrieved_ids", "recall@5", "recall@10", "mrr",
              "answer", "cited_ids", "coarse_citations", "invalid_citations", "citation_recall", "citation_precision",
              "finish_reason",
              "truncated", "input_tokens", "output_tokens", "latency_s", "from_cache", "run", "effort",
              "model_served", "called_at"]


def load_unit_ids() -> tuple[set[str], set[str]]:
    """(ids of the 192 content units, ids of all 51 articles)."""
    with UNITS_PATH.open(encoding="utf-8") as f:
        units = [json.loads(line) for line in f if line.strip()]
    return {u["id"] for u in units}, {u["dieu_id"] for u in units}


def select(questions: list[dict], ids: list[str] | None, limit: int | None) -> list[dict]:
    if ids:
        by_id = {q["id"]: q for q in questions}
        missing = [i for i in ids if i not in by_id]
        if missing:
            raise ValueError(f"ids not in dev: {missing}")
        return [by_id[i] for i in ids]
    return questions[:limit] if limit else questions


def citation_scores(cited: list[str], sources: list[str]) -> tuple[float | None, float | None]:
    """(recall over source_ids, precision over cited ids); None when undefined (no sources / no citations)."""
    if not sources:
        return None, None
    hit = len(set(cited) & set(sources))
    recall = hit / len(set(sources))
    precision = hit / len(set(cited)) if cited else None
    return recall, precision


def evaluate(system: str, questions: list[dict], runs: int, effort: str | None, unit_ids: set[str],
             article_ids: set[str], complete_fn=None, index=None) -> list[dict]:
    """One row per question x run. complete_fn/index can be injected for tests."""
    kwargs = {"complete_fn": complete_fn} if complete_fn else {}
    rows = []
    for q in questions:
        if q.get("split") != "dev":
            raise ValueError(f"{q['id']}: only dev questions may be evaluated")
        for run in range(runs):
            if system == "llm_only":
                result, retrieved = systems.llm_only(q["question"], run=run, effort=effort, **kwargs)
            else:
                result, retrieved = systems.rag_baseline(q["question"], index, run=run, effort=effort, **kwargs)
            # Coarse (whole-article) citations count neither for nor against precision/recall
            cited, coarse, invalid = cit.citations(result.text, unit_ids, article_ids)
            c_recall, c_precision = citation_scores(cited, q["source_ids"])
            scores = rm.score_question(q, retrieved) if retrieved is not None else None
            rows.append({
                "id": q["id"], "origin": q["origin"], "type": q["type"], "delegated": q["delegated"],
                "source_ids": q["source_ids"], "retrieved_ids": retrieved,
                "recall@5": scores["recall@5"] if scores else None,
                "recall@10": scores["recall@10"] if scores else None,
                "mrr": scores["rr"] if scores else None,
                "answer": result.text, "cited_ids": cited, "coarse_citations": coarse, "invalid_citations": invalid,
                "citation_recall": c_recall, "citation_precision": c_precision,
                "finish_reason": result.finish_reason, "truncated": result.truncated,
                "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
                "latency_s": result.latency_s, "from_cache": result.from_cache, "run": run,
                "effort": (result.params or {}).get("effort", effort), "model_served": result.model_served,
                "called_at": result.called_at,
            })
    return rows


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, list):
        return " ".join(value)
    if isinstance(value, float):
        return round(value, 4)
    return value


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_FIELDS)
        for r in rows:
            writer.writerow([_cell(r[k]) for k in CSV_FIELDS])


def cost_usd(rows: list[dict], pricing: dict, fresh_only: bool) -> float:
    picked = [r for r in rows if not (fresh_only and r["from_cache"])]
    tokens_in = sum(r["input_tokens"] or 0 for r in picked)
    tokens_out = sum(r["output_tokens"] or 0 for r in picked)
    return round(tokens_in / 1e6 * pricing["input_per_mtok"] + tokens_out / 1e6 * pricing["output_per_mtok"], 6)


def git_commit() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Run a baseline on the dev split.")
    parser.add_argument("--system", choices=SYSTEMS, required=True)
    parser.add_argument("--split", default="dev")
    pick = parser.add_mutually_exclusive_group()
    pick.add_argument("--ids", nargs="+")
    pick.add_argument("--limit", type=int)
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--effort", choices=("low", "medium", "high", "xhigh", "max"))
    parser.add_argument("--overwrite", action="store_true", help="replace existing result files")
    args = parser.parse_args(argv)
    if args.split != "dev":
        print(f"LỖI: split {args.split!r} bị từ chối; chỉ chạy trên dev (test chỉ chạy ở T9).", file=sys.stderr)
        return 2

    llm_cfg = tomllib.loads(LLM_CONFIG.read_text(encoding="utf-8"))
    answer_cfg = llm_cfg["roles"]["answer"]
    default_effort = answer_cfg.get("params", {}).get("effort")
    effort = args.effort or default_effort
    now = datetime.now(VN_TZ)
    suffix = f"_{effort}" if effort != default_effort else ""
    stem = f"{now:%Y-%m-%d}_{args.system}{suffix}_dev"
    existing = [p.name for p in (RESULTS_DIR / f"{stem}.csv", RESULTS_DIR / f"{stem}.json") if p.exists()]
    if existing and not args.overwrite:
        print(f"LỖI: đã có {existing}; không ghi đè (dùng --overwrite nếu muốn thay).", file=sys.stderr)
        return 2
    questions = select(rm.load_dev_questions(), args.ids, args.limit)
    print(f"[{args.system}] effort={effort} | {len(questions)} câu × {args.runs} lượt = "
          f"tối đa {len(questions) * args.runs} lời gọi answer (lời gọi đã có trong cache không tính phí)")

    index = None
    if args.system == "rag_baseline":
        from src.retrieval.dense import DenseIndex

        index = DenseIndex.from_config()
    unit_ids, article_ids = load_unit_ids()
    rows = evaluate(args.system, questions, args.runs, args.effort, unit_ids, article_ids, index=index)

    pricing = llm_cfg["pricing"][answer_cfg["model"]]
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    write_csv(RESULTS_DIR / f"{stem}.csv", rows)
    meta = {
        "run_at": now.isoformat(timespec="seconds"), "git_commit": git_commit(), "system": args.system,
        "split": "dev", "question_ids": [q["id"] for q in questions], "runs": args.runs, "effort": effort,
        "answer_role": answer_cfg, "retrieval_config": (tomllib.loads(RETRIEVAL_CONFIG.read_text(encoding="utf-8"))
                                                       if args.system == "rag_baseline" else None),
        "retrieval_index": index.metadata() if index else None,
        "system_prompt_sha256": prompt_sha256(), "system_prompt": SYSTEM_PROMPT,
        "libraries": {p: version(p) for p in ("anthropic", "sentence-transformers", "torch", "numpy")},
        "tokens": {"input": sum(r["input_tokens"] or 0 for r in rows),
                   "output": sum(r["output_tokens"] or 0 for r in rows)},
        "pricing": pricing,
        "cost_usd": cost_usd(rows, pricing, fresh_only=False),
        "answers_first_called_at": min(r["called_at"] for r in rows),
        "cost_usd_this_invocation": cost_usd(rows, pricing, fresh_only=True),
        "fresh_calls_this_invocation": sum(not r["from_cache"] for r in rows),
        "cached_calls_this_invocation": sum(r["from_cache"] for r in rows),
        "truncated_rows": [r["id"] for r in rows if r["truncated"]],
        "n_coarse_citations": sum(len(r["coarse_citations"]) for r in rows),
        "rows_with_coarse_citations": {r["id"]: r["coarse_citations"] for r in rows if r["coarse_citations"]},
        "rows_with_invalid_citations": {r["id"]: r["invalid_citations"] for r in rows if r["invalid_citations"]},
    }
    (RESULTS_DIR / f"{stem}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Ghi eval/results/{stem}.csv (.json) | lời gọi mới {meta['fresh_calls_this_invocation']}, từ cache "
          f"{meta['cached_calls_this_invocation']} | chi phí kết quả ≈ ${meta['cost_usd']} (lần chạy này "
          f"${meta['cost_usd_this_invocation']}) | trích dẫn cả điều: {meta['n_coarse_citations']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
