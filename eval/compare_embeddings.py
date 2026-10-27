"""Compare two embedding models for decision #4 on dev v1 (retrieval only, no LLM or API calls).

Run from the repo root:  python eval/compare_embeddings.py

Four configurations: {AITeamVN/Vietnamese_Embedding, BAAI/bge-m3} x {with_heading False, True}.
Top-10 per dev question over the 192 :DonViNoiDung units; Recall@5, Recall@10, Hit@5, MRR overall and
by origin, type and delegated; pairwise rank comparisons; encode/query time and peak VRAM.
Reads only data/eval/questions_v1_dev.jsonl (asserts split == "dev"); the test split is never opened.
Writes eval/results/<date>_embedding_compare_dev.csv and .json.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

# HF_HOME must be in the environment before any HuggingFace import (src.retrieval.dense does this too)
load_dotenv(REPO_ROOT / ".env")

import csv  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402
from importlib.metadata import version  # noqa: E402

from eval import retrieval_metrics as rm  # noqa: E402
from src.retrieval import dense  # noqa: E402

MODELS = ("AITeamVN/Vietnamese_Embedding", "BAAI/bge-m3")
HEADINGS = (False, True)
TOP_K = 10
EXPECTED_HF_ROOT = Path(r"D:\hf_cache")
RESULTS_DIR = REPO_ROOT / "eval" / "results"
VN_TZ = timezone(timedelta(hours=7))


def config_name(model_id: str, with_heading: bool) -> str:
    short = "vn_emb" if model_id.startswith("AITeamVN") else "bge_m3"
    return f"{short}{'+heading' if with_heading else ''}"


def git_commit() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True).stdout.strip()


def run_config(model_id: str, with_heading: bool, questions: list[dict]) -> tuple[list[dict], dict]:
    import torch

    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    index = dense.DenseIndex(model_id, with_heading)
    known = set(index.unit_ids)

    rows, query_seconds = [], []
    for q in questions:
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        hits = index.search(q["question"], TOP_K)
        torch.cuda.synchronize()
        query_seconds.append(time.perf_counter() - t0)
        retrieved = [uid for uid, _ in hits]
        assert set(retrieved) <= known
        rows.append({
            "config": config_name(model_id, with_heading), "question_id": q["id"], "origin": q["origin"],
            "type": q["type"], "delegated": q["delegated"], "source_ids": q["source_ids"], "top10": retrieved,
            **rm.score_question(q, retrieved),
        })
    meta = index.metadata()
    meta.update({
        "encode_seconds_192_units": round(index.encode_seconds, 3),
        "mean_query_seconds": round(sum(query_seconds) / len(query_seconds), 4),
        "peak_vram_mb": round(torch.cuda.max_memory_allocated() / 2**20, 1),
    })
    del index
    torch.cuda.empty_cache()
    return rows, meta


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = ["config", "question_id", "origin", "type", "delegated", "scored", "source_ids", "top10",
              "first_hit_rank", "recall@5", "recall@10"]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(fields)
        for r in rows:
            writer.writerow([r["config"], r["question_id"], r["origin"], r["type"], r["delegated"], r["scored"],
                             " ".join(r["source_ids"]), " ".join(r["top10"]),
                             "" if r["first_hit_rank"] is None else r["first_hit_rank"],
                             "" if r["recall@5"] is None else round(r["recall@5"], 4),
                             "" if r["recall@10"] is None else round(r["recall@10"], 4)])


def fmt(summary: dict) -> str:
    return (f"n={summary['n']:>2}  R@5 {summary['recall@5']:.3f}  R@10 {summary['recall@10']:.3f}  "
            f"Hit@5 {summary['hit@5']:.3f}  MRR {summary['mrr']:.3f}") if summary["n"] else "n= 0"


def main() -> int:
    import torch

    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    cache_root = Path(dense.hf_hub_cache()).resolve()
    print(f"HF_HUB_CACHE = {cache_root}")
    if EXPECTED_HF_ROOT.resolve() not in [cache_root, *cache_root.parents]:
        print(f"LỖI: cache HuggingFace không nằm dưới {EXPECTED_HF_ROOT} — dừng.", file=sys.stderr)
        return 1
    if not torch.cuda.is_available():
        print("LỖI: torch.cuda.is_available() == False — dừng (không chạy CPU).", file=sys.stderr)
        return 1
    print(f"GPU: {torch.cuda.get_device_name(0)} | torch {torch.__version__} (CUDA {torch.version.cuda})")

    questions = rm.load_dev_questions()
    print(f"Câu dev: {len(questions)} | bị loại khỏi Recall/MRR (out_of_scope): "
          f"{sum(not rm.is_scored(q) for q in questions)}")

    all_rows, by_config, metas = [], {}, {}
    for model_id in MODELS:
        for with_heading in HEADINGS:
            name = config_name(model_id, with_heading)
            rows, meta = run_config(model_id, with_heading, questions)
            by_config[name], metas[name] = rows, meta
            all_rows += rows
            print(f"[{name}] rev {meta['revision'][:10]} | max_seq {meta['max_seq_length']} | token dài nhất "
                  f"{meta['max_unit_tokens']} | bị cắt {meta['n_truncated_units']} | encode "
                  f"{meta['encode_seconds_192_units']}s{' (cache)' if meta['from_cache'] else ''} | truy vấn "
                  f"{meta['mean_query_seconds'] * 1000:.1f} ms | VRAM đỉnh {meta['peak_vram_mb']} MB")

    summaries = {name: rm.breakdown(rows) for name, rows in by_config.items()}
    pairs = {}
    for with_heading in HEADINGS:
        a, b = config_name(MODELS[0], with_heading), config_name(MODELS[1], with_heading)
        pairs[f"{a} vs {b}"] = rm.compare_ranks(by_config[a], by_config[b])
    for model_id in MODELS:
        a, b = config_name(model_id, True), config_name(model_id, False)
        pairs[f"{a} vs {b}"] = rm.compare_ranks(by_config[a], by_config[b])

    print()
    for name, s in summaries.items():
        print(f"== {name}")
        print(f"   tổng           {fmt(s['total'])}")
        for key in rm.GROUP_KEYS:
            for value, group in s[key].items():
                print(f"   {key}={value:<11} {fmt(group)}")
    print("\nSo từng câu theo hạng nguồn đúng đầu tiên (A tốt hơn / kém hơn / bằng):")
    for label, p in pairs.items():
        print(f"   {label}: {p['a_better']} / {p['a_worse']} / {p['same']}")

    best = max(summaries, key=lambda n: summaries[n]["total"]["recall@5"])
    misses = [r for r in by_config[best] if r["scored"] and r["first_hit_rank"] is None]
    print(f"\nCấu hình Recall@5 tổng cao nhất: {best}. Câu có nguồn đúng KHÔNG nằm trong top-10: {len(misses)}")
    for r in misses:
        print(f"   {r['question_id']}: nguồn {[s[7:] for s in r['source_ids']]} | top-5 {[t[7:] for t in r['top10'][:5]]}")

    now = datetime.now(VN_TZ)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"{now:%Y-%m-%d}_embedding_compare_dev"
    write_csv(RESULTS_DIR / f"{stem}.csv", all_rows)
    (RESULTS_DIR / f"{stem}.json").write_text(json.dumps({
        "run_at": now.isoformat(timespec="seconds"), "git_commit": git_commit(), "split": "dev",
        "questions_file": rm.DEV_PATH.relative_to(REPO_ROOT).as_posix(), "n_questions": len(questions),
        "top_k": TOP_K, "units_file": dense.UNITS_PATH.relative_to(REPO_ROOT).as_posix(),
        "environment": {
            "gpu": torch.cuda.get_device_name(0), "torch": torch.__version__, "torch_cuda": torch.version.cuda,
            "sentence_transformers": version("sentence-transformers"), "transformers": version("transformers"),
            "numpy": version("numpy"), "python": sys.version.split()[0],
        },
        "configs": metas, "summary": summaries, "pairwise_first_hit_rank": pairs,
        "best_by_total_recall@5": best,
        "misses_top10_best": [{"question_id": r["question_id"], "source_ids": r["source_ids"], "top5": r["top10"][:5]}
                              for r in misses],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nĐã ghi eval/results/{stem}.csv và .json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
