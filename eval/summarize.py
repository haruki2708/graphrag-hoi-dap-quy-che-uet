"""Summarise the judged dev results of both baselines (T2 "Xong khi").

Run from the repo root:  python eval/summarize.py [--date 2026-10-27] [--judge-prompt v1|v2] [--agreement <json>]

v1 (default): reads eval/results/<date>_<system>_dev_judged.csv (+ the run_eval .json for cost) for llm_only and
rag_baseline; writes eval/results/<date>_summary_dev.json and prints the tables. Unchanged since T2.
v2: reads <date>_<system>_dev_judged_v2.csv (made by judge_answers.py --judge-prompt v2) and writes
<date>_summary_dev_judge_v2.json, never the v1 summary. The metadata holds the prompt version, the sha256 of the
judge system prompt (from the <stem>_judged_v2.json files, which must agree) and the calibration result read from
the agreement file (eval/judge_agreement.py --label v2).
Judge score: dung = 1, mot_phan = 0.5, sai = 0. Per-run metrics are averaged over the runs, with min-max.
"""

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "eval" / "results"
AGREEMENT_V2 = RESULTS_DIR / "2026-11-01_judge_agreement_v2.json"  # calibration of GLM + prompt v2 (owner labels, 30 answers)
SYSTEMS = ("llm_only", "rag_baseline")
LABELS = ("dung", "mot_phan", "sai")
LABEL_SCORE = {"dung": 1.0, "mot_phan": 0.5, "sai": 0.0}
GROUP_KEYS = ("origin", "type", "delegated")
FLOAT_FIELDS = ("recall@5", "recall@10", "mrr", "citation_recall", "citation_precision", "latency_s")
INT_FIELDS = ("input_tokens", "output_tokens", "run")
LIST_FIELDS = ("source_ids", "retrieved_ids", "cited_ids", "coarse_citations", "invalid_citations")


def parse_row(raw: dict) -> dict:
    row = dict(raw)
    for key in FLOAT_FIELDS:
        row[key] = float(raw[key]) if raw.get(key) not in (None, "") else None
    for key in INT_FIELDS:
        row[key] = int(raw[key]) if raw.get(key) not in (None, "") else None
    for key in LIST_FIELDS:
        row[key] = raw.get(key, "").split() if raw.get(key) else []
    row["delegated"] = str(raw.get("delegated")).lower() == "true"
    row["from_cache"] = str(raw.get("from_cache")).lower() == "true"
    row["failed"] = bool(raw.get("error")) or bool(raw.get("judge_error"))
    return row


def mean(values: list[float]) -> float | None:
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 4) if values else None


def spread(per_run: list[float | None]) -> dict:
    """Mean over runs and min-max between runs."""
    values = [v for v in per_run if v is not None]
    if not values:
        return {"mean": None, "min": None, "max": None}
    return {"mean": round(sum(values) / len(values), 4), "min": round(min(values), 4), "max": round(max(values), 4)}


def by_run(rows: list[dict]) -> dict[int, list[dict]]:
    runs = defaultdict(list)
    for r in rows:
        runs[r["run"]].append(r)
    return dict(sorted(runs.items()))


def label_distribution(rows: list[dict]) -> dict:
    """Per label: count per run averaged over runs, with min-max."""
    runs = by_run(rows)
    return {label: spread([sum(r["judge_label"] == label for r in rs) for rs in runs.values()]) for label in LABELS}


def judge_score(rows: list[dict]) -> dict:
    runs = by_run(rows)
    return spread([mean([LABEL_SCORE[r["judge_label"]] for r in rs if r["judge_label"] in LABEL_SCORE])
                   for rs in runs.values()])


def unstable_questions(rows: list[dict]) -> list[str]:
    """Questions whose judge label is not the same in every run."""
    labels = defaultdict(set)
    for r in rows:
        labels[r["id"]].add(r["judge_label"])
    return sorted(qid for qid, seen in labels.items() if len(seen) > 1)


def check_retrieval_consistent(rows: list[dict]) -> None:
    """Retrieval does not depend on the run: same retrieved ids and scores for a question in every run."""
    seen = {}
    for r in rows:
        key = (tuple(r["retrieved_ids"]), r["recall@5"], r["recall@10"], r["mrr"])
        if r["id"] in seen and seen[r["id"]] != key:
            raise AssertionError(f"{r['id']}: retrieval differs between runs")
        seen[r["id"]] = key


def group_summary(rows: list[dict], with_retrieval: bool) -> dict:
    scored = [r for r in rows if r["type"] != "out_of_scope"]
    first_run = [r for r in scored if r["run"] == min(x["run"] for x in rows)]
    out = {
        "n_questions": len({r["id"] for r in rows}),
        "judge_score": judge_score(rows),
        "labels": label_distribution(rows),
        "citation_recall": spread([mean([r["citation_recall"] for r in rs]) for rs in by_run(scored).values()]),
        "citation_precision": spread([mean([r["citation_precision"] for r in rs]) for rs in by_run(scored).values()]),
    }
    if with_retrieval:
        out["retrieval"] = {k: mean([r[k] for r in first_run]) for k in ("recall@5", "recall@10", "mrr")}
        out["retrieval"]["n"] = len(first_run)
    return out


def summarise_system(rows: list[dict], with_retrieval: bool, cost_usd: float | None) -> dict:
    ok = [r for r in rows if not r["failed"]]
    if with_retrieval:
        check_retrieval_consistent(ok)
    out_of_scope = [r for r in ok if r["type"] == "out_of_scope"]
    groups = {"total": group_summary(ok, with_retrieval)}
    for key in GROUP_KEYS:
        values = sorted({str(r[key]).lower() for r in ok})
        groups[key] = {v: group_summary([r for r in ok if str(r[key]).lower() == v], with_retrieval) for v in values}
    return {
        "rows": len(rows), "failed_rows": len(rows) - len(ok), "runs": sorted(by_run(rows)),
        "groups": groups,
        "out_of_scope_dung_rate": spread([mean([1.0 if r["judge_label"] == "dung" else 0.0 for r in rs])
                                          for rs in by_run(out_of_scope).values()]),
        "n_coarse_citations": sum(len(r["coarse_citations"]) for r in ok),
        "n_invalid_citations": sum(len(r["invalid_citations"]) for r in ok),
        "unstable_questions": unstable_questions(ok),
        "tokens": {"input": sum(r["input_tokens"] or 0 for r in ok), "output": sum(r["output_tokens"] or 0 for r in ok)},
        "cost_usd": cost_usd,
        "latency_median_s": round(statistics.median([r["latency_s"] for r in ok if r["latency_s"] is not None]), 3)
        if ok else None,
    }


def lowest(rows: list[dict], n: int = 5) -> list[dict]:
    """Questions with the lowest mean judge score over runs (ties: lower citation recall first)."""
    per_q = defaultdict(list)
    for r in rows:
        if not r["failed"]:
            per_q[r["id"]].append(r)
    ranked = sorted(per_q.items(), key=lambda item: (
        mean([LABEL_SCORE.get(r["judge_label"], 0.0) for r in item[1]]),
        mean([r["citation_recall"] for r in item[1]]) or 0.0, item[0]))
    return [{"id": qid, "type": rs[0]["type"], "judge_score": mean([LABEL_SCORE.get(r["judge_label"], 0.0) for r in rs]),
             "labels": [r["judge_label"] for r in sorted(rs, key=lambda r: r["run"])], "source_ids": rs[0]["source_ids"],
             "retrieved_ids": rs[0]["retrieved_ids"], "answer_run0": rs[0]["answer"]} for qid, rs in ranked[:n]]


def fmt(s: dict) -> str:
    return "–" if s["mean"] is None else f"{s['mean']:.3f} ({s['min']:.3f}–{s['max']:.3f})"


class MissingInput(Exception):
    """A file the summary needs is not there; the message says which one and how to make it."""


def judged_suffix(version: str) -> str:
    return "_judged" if version == "v1" else "_judged_v2"


def read_judged_rows(path: Path, version: str) -> list[dict]:
    if not path.exists():
        hint = ("" if version == "v1" else
                f" Tạo bằng: python eval/judge_answers.py eval/results/{path.name.replace('_judged_v2.csv', '.csv')} --judge-prompt v2")
        raise MissingInput(f"thiếu file {path.name} (prompt {version}).{hint}")
    with path.open(encoding="utf-8") as f:
        rows = [parse_row(r) for r in csv.DictReader(f)]
    wrong = {r.get("judge_prompt") for r in rows if r.get("judge_prompt") and r["judge_prompt"] != version}
    if wrong:
        raise MissingInput(f"{path.name}: cột judge_prompt có {sorted(wrong)}, không phải {version}")
    return rows


def judge_v2_metadata(results_dir: Path, date: str, agreement_path: Path) -> dict:
    """Prompt version + hash (identical in both systems' judged_v2.json) and the calibration read from the agreement file."""
    hashes = {}
    for system in SYSTEMS:
        meta_path = results_dir / f"{date}_{system}_dev{judged_suffix('v2')}.json"
        if not meta_path.exists():
            raise MissingInput(f"thiếu file {meta_path.name} (metadata của lượt chấm v2). Tạo bằng: python eval/judge_answers.py "
                               f"eval/results/{date}_{system}_dev.csv --judge-prompt v2")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("judge_prompt_version") != "v2" or not meta.get("judge_prompt_sha256"):
            raise MissingInput(f"{meta_path.name}: không phải kết quả chấm prompt v2 (thiếu judge_prompt_version/sha256)")
        hashes[system] = meta["judge_prompt_sha256"]
    if len(set(hashes.values())) != 1:
        raise MissingInput(f"hash prompt giám khảo khác nhau giữa các hệ: {hashes}")
    if not agreement_path.exists():
        raise MissingInput(f"thiếu file hiệu chỉnh {agreement_path.name} (python eval/judge_agreement.py ... --label v2)")
    agreement = json.loads(agreement_path.read_text(encoding="utf-8"))
    if agreement.get("judge_prompt_versions") != ["v2"]:
        raise MissingInput(f"{agreement_path.name}: không phải hiệu chỉnh của prompt v2 ({agreement.get('judge_prompt_versions')})")
    overall = agreement["overall"]
    return {
        "judge_prompt_version": "v2", "judge_prompt_sha256": next(iter(hashes.values())),
        "judge_models": agreement.get("judge_models"),
        "calibration": {
            "agreement_file": agreement_path.relative_to(REPO_ROOT).as_posix() if agreement_path.is_relative_to(REPO_ROOT)
            else str(agreement_path),
            "computed_at": agreement.get("computed_at"), "n": overall["n"], "agree": overall["agree"],
            "agreement": round(overall["agreement"], 4), "kappa": round(overall["kappa"], 4),
            "kappa_linear_weighted": round(overall["kappa_linear_weighted"], 4),
            "note": "Chỉnh prompt trên chính 30 mẫu hiệu chỉnh nên số này có phần lạc quan; kiểm thêm khoảng 10 mẫu mới ở T5.",
        },
    }


def build_summary(date: str, version: str = "v1", results_dir: Path = RESULTS_DIR,
                  agreement_path: Path = AGREEMENT_V2) -> dict:
    if version == "v1":
        summary = {"date": date, "split": "dev", "judge_calibrated": False,
                   "judge_score_mapping": LABEL_SCORE,
                   "note": "Giám khảo CHƯA được hiệu chỉnh; nhãn và điểm chỉ để tham khảo.",
                   "systems": {}, "lowest": {}}
    else:
        meta = judge_v2_metadata(results_dir, date, agreement_path)
        cal = meta["calibration"]
        summary = {"date": date, "split": "dev", "judge_calibrated": True, **meta,
                   "judge_score_mapping": LABEL_SCORE,
                   "note": (f"Giám khảo chính thức: GLM-5.3-Flash + prompt v2 (xác nhận 01/11/2026); hiệu chỉnh "
                            f"{cal['agree']}/{cal['n']}, kappa {cal['kappa']}. {cal['note']}"),
                   "systems": {}, "lowest": {}}
    for system in SYSTEMS:
        stem = results_dir / f"{date}_{system}_dev"
        rows = read_judged_rows(Path(f"{stem}{judged_suffix(version)}.csv"), version)
        run_json = Path(f"{stem}.json")
        if not run_json.exists():
            raise MissingInput(f"thiếu file {run_json.name} (kết quả run_eval, dùng cho chi phí)")
        cost = json.loads(run_json.read_text(encoding="utf-8")).get("cost_usd")
        summary["systems"][system] = summarise_system(rows, system == "rag_baseline", cost)
        summary["lowest"][system] = lowest(rows)
    return summary


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Summarise judged dev results.")
    parser.add_argument("--date", default="2026-10-27")
    parser.add_argument("--judge-prompt", choices=("v1", "v2"), default="v1")
    parser.add_argument("--agreement", type=Path, default=AGREEMENT_V2,
                        help="judge_agreement.py result for prompt v2 (only used with --judge-prompt v2)")
    args = parser.parse_args(argv)

    try:
        summary = build_summary(args.date, args.judge_prompt, RESULTS_DIR, args.agreement)
    except MissingInput as e:
        print(f"LỖI: {e}", file=sys.stderr)
        return 2

    out = RESULTS_DIR / (f"{args.date}_summary_dev.json" if args.judge_prompt == "v1"
                         else f"{args.date}_summary_dev_judge_v2.json")
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.judge_prompt == "v2":
        print(f"Giám khảo: prompt v2, sha256 {summary['judge_prompt_sha256'][:12]}…, hiệu chỉnh "
              f"{summary['calibration']['agree']}/{summary['calibration']['n']} "
              f"({summary['calibration']['agreement']:.3f}), kappa {summary['calibration']['kappa']} "
              f"[{summary['calibration']['agreement_file']}]")
    for system, s in summary["systems"].items():
        print(f"== {system} | dòng {s['rows']} (lỗi {s['failed_rows']}) | lượt {s['runs']} | token {s['tokens']} | "
              f"chi phí ${s['cost_usd']} | latency trung vị {s['latency_median_s']} s")
        for name, g in [("tổng", s["groups"]["total"])] + [(f"{k}={v}", g) for k in GROUP_KEYS
                                                           for v, g in s["groups"][k].items()]:
            ret = g.get("retrieval")
            ret_txt = f" | R@5 {ret['recall@5']} R@10 {ret['recall@10']} MRR {ret['mrr']}" if ret else ""
            labels = " / ".join(f"{g['labels'][lb]['mean']}" for lb in LABELS)
            print(f"   {name:<24} n={g['n_questions']:>2} | điểm judge {fmt(g['judge_score'])} | nhãn d/mp/s {labels} | "
                  f"cit R {fmt(g['citation_recall'])} P {fmt(g['citation_precision'])}{ret_txt}")
        print(f"   out_of_scope: tỷ lệ 'dung' {fmt(s['out_of_scope_dung_rate'])} | coarse {s['n_coarse_citations']} | "
              f"invalid {s['n_invalid_citations']} | câu đổi nhãn giữa các lượt {len(s['unstable_questions'])}: "
              f"{s['unstable_questions']}")
    print(f"\nĐã ghi {out.relative_to(REPO_ROOT).as_posix() if out.is_relative_to(REPO_ROOT) else out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
