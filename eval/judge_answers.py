"""Grade a run_eval CSV with the judge role (GLM-5.3-Flash via NVIDIA NIM, decision #2).

Run from the repo root:  python eval/judge_answers.py eval/results/<date>_<system>_dev.csv [--overwrite]

Labels and prompt come from scripts/judge_smoke.py. "Nguyên văn quy chế" = text of the question's
source_ids units; out_of_scope questions get the dev reference answer and no regulation text.
Writes <stem>_judged.csv (input columns + judge_label, judge_reason, judge_model) and <stem>_judged.json;
existing files are not overwritten unless --overwrite is given.
The judge is NOT calibrated yet (30-answer calibration pending, PLAN #2) - recorded in the metadata.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import csv  # noqa: E402
import json  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

from eval import retrieval_metrics as rm  # noqa: E402
from src.llm import LLMError, complete  # noqa: E402

UNITS_PATH = REPO_ROOT / "data" / "processed" / "qc3626_content_units.jsonl"
VN_TZ = timezone(timedelta(hours=7))
LABELS = ("dung", "sai", "mot_phan")
VERDICT_SCHEMA = {
    "type": "object",
    "properties": {"label": {"type": "string", "enum": list(LABELS)}, "reason": {"type": "string"}},
    "required": ["label", "reason"],
    "additionalProperties": False,
}

# Same wording as scripts/judge_smoke.py SYSTEM_PROMPT
JUDGE_SYSTEM = """Bạn là giám khảo chấm câu trả lời về quy chế đào tạo đại học.
Chỉ dựa vào ĐÁP ÁN MẪU và NGUYÊN VĂN QUY CHẾ được cung cấp, không dùng hiểu biết bên ngoài.
Gán đúng một nhãn cho CÂU TRẢ LỜI CẦN CHẤM:
- "dung": kết luận đúng và nêu đủ các ý chính của đáp án mẫu (căn cứ và ngưỡng).
- "mot_phan": kết luận đúng nhưng thiếu căn cứ hoặc ngưỡng, hoặc thiếu ý chính.
- "sai": kết luận sai, hoặc nêu căn cứ/ngưỡng mâu thuẫn với quy chế.
Chỉ trả về một đối tượng JSON: {"label": "dung" | "sai" | "mot_phan", "reason": "<một câu ngắn bằng tiếng Việt>"}."""

NO_SOURCE_TEXT = "(Không có nguyên văn: câu hỏi nằm ngoài nội dung quy chế; chấm theo đáp án mẫu.)"


def judge_prompt(question: dict, answer: str, unit_text: dict[str, str]) -> str:
    sources = "\n\n".join(f"[{uid}]\n{unit_text[uid]}" for uid in question["source_ids"]) or NO_SOURCE_TEXT
    return (f"CÂU HỎI:\n{question['question']}\n\nĐÁP ÁN MẪU:\n{question['answer']}\n\n"
            f"NGUYÊN VĂN QUY CHẾ:\n{sources}\n\nCÂU TRẢ LỜI CẦN CHẤM:\n{answer}")


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stderr.reconfigure(encoding="utf-8")
    overwrite = "--overwrite" in argv
    args = [a for a in argv if a != "--overwrite"]
    if len(args) != 1:
        print("Cách dùng: python eval/judge_answers.py <file csv của run_eval> [--overwrite]", file=sys.stderr)
        return 2
    path = Path(args[0])
    if not path.name.endswith("_dev.csv"):
        print("LỖI: chỉ chấm file kết quả trên dev (*_dev.csv).", file=sys.stderr)
        return 2
    stem = path.with_suffix("")
    outputs = [Path(f"{stem}_judged.csv"), Path(f"{stem}_judged.json")]
    existing = [p.name for p in outputs if p.exists()]
    if existing and not overwrite:
        print(f"LỖI: đã có {existing}; không ghi đè (dùng --overwrite nếu muốn thay).", file=sys.stderr)
        return 2
    questions = {q["id"]: q for q in rm.load_dev_questions()}
    with UNITS_PATH.open(encoding="utf-8") as f:
        unit_text = {u["id"]: u["text"] for u in map(json.loads, f)}
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    print(f"Chấm {len(rows)} câu trả lời → tối đa {len(rows)} lời gọi judge (cache dùng lại nếu có)")

    out_rows, fresh, errors, first_called = [], 0, [], ""
    for row in rows:
        question = questions[row["id"]]  # KeyError if the CSV holds a non-dev id
        try:
            result = complete("judge", JUDGE_SYSTEM,
                              [{"role": "user", "content": judge_prompt(question, row["answer"], unit_text)}],
                              json_schema=VERDICT_SCHEMA, run=0)
            fresh += not result.from_cache
            first_called = min(first_called, result.called_at) if first_called else result.called_at
            verdict = result.parsed or {}
            out_rows.append({**row, "judge_label": verdict.get("label", ""), "judge_reason": verdict.get("reason", ""),
                             "judge_model": result.model_served or result.model_requested})
        except LLMError as e:
            errors.append({"id": row["id"], "run": row["run"], "http_code": e.http_code, "error": str(e)[:200]})
            out_rows.append({**row, "judge_label": "", "judge_reason": f"LỖI: {e}"[:200], "judge_model": ""})

    out_csv = outputs[0]
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(out_rows[0]))
        writer.writeheader()
        writer.writerows(out_rows)
    Path(f"{stem}_judged.json").write_text(json.dumps({
        "judged_at": datetime.now(VN_TZ).isoformat(timespec="seconds"), "input_csv": path.name,
        "judge_role": "judge", "judge_calibrated": False,
        "note": "Giám khảo CHƯA được hiệu chỉnh (hiệu chỉnh 30 câu theo PLAN #2 chưa làm); nhãn chỉ để tham khảo.",
        "judge_system_prompt": JUDGE_SYSTEM, "fresh_judge_calls_this_invocation": fresh,
        "judge_calls_total": len(out_rows) - len(errors),
        "judge_first_called_at": first_called or None, "errors": errors,
        "labels": {label: sum(r["judge_label"] == label for r in out_rows) for label in LABELS},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Ghi {out_csv.name} | lời gọi judge mới: {fresh} | lỗi: {len(errors)}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
