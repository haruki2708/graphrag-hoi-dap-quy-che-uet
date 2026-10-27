"""Grade a run_eval CSV with the judge role (GLM-5.3-Flash via NVIDIA NIM, decision #2).

Run from the repo root:
  python eval/judge_answers.py eval/results/<date>_<system>_dev.csv [more csv ...] [--overwrite]
                               [--judge-prompt v1|v2] [--manifest <calibration manifest json>]

Labels and prompt come from scripts/judge_smoke.py. "Nguyên văn quy chế" = text of the question's
source_ids units; out_of_scope questions get the dev reference answer and no regulation text.
Writes <stem>_judged.csv (input columns + judge_label, judge_reason, judge_model) and <stem>_judged.json;
existing files are not overwritten unless --overwrite is given.
The judge is NOT calibrated yet (PLAN #2) - recorded in the metadata.

Judge prompts: v1 (default; the wording of every result so far, unchanged) and v2 (= v1 + the grounding rules the
project owner fixed on 27/10/2026, and a "Căn cứ đúng:" line in the message). The version and the sha256 of the
system prompt go into the metadata; the LLM cache key contains the system prompt and the message, so v1 and v2
never share cache entries. v2 outputs are named <stem>_judged_v2.* (v2 rows also get a judge_prompt column), so the
v1 files are never touched. --manifest keeps only the rows listed in a calibration manifest (the system comes from
the csv name <date>_<system>_dev.csv) and adds "_calib" to the output names.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import csv  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402
from datetime import datetime, timedelta, timezone  # noqa: E402

from eval import retrieval_metrics as rm  # noqa: E402
from src.llm import LLMError, complete  # noqa: E402

UNITS_PATH = REPO_ROOT / "data" / "processed" / "qc3626_content_units.jsonl"
VN_TZ = timezone(timedelta(hours=7))
DOC_ID = "qc3626"
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

# v2 = v1 with these rules inserted before the final output-format line. The 3 label definitions are untouched.
GROUNDING_RULES = """Quy tắc căn cứ (chỉ áp dụng khi tin nhắn có dòng "Căn cứ đúng:"; câu ngoài phạm vi quy chế không có dòng này thì bỏ qua):
- Câu trả lời viện dẫn cho một ý chính một điều/khoản KHÁC với căn cứ đúng: "sai", kể cả khi kết luận đúng.
- Câu trả lời không nêu căn cứ cho một ý chính: tối đa "mot_phan".
- Trích thêm điều/khoản khác ngoài căn cứ đúng không bị trừ, nếu ý chính đã có căn cứ đúng.
- Điểm nằm trong khoản đúng (ví dụ "điểm đ" của khoản đúng) là căn cứ đúng."""
_V1_BODY, _V1_FORMAT_LINE = JUDGE_SYSTEM.rsplit("\n", 1)
JUDGE_SYSTEM_V2 = f"{_V1_BODY}\n{GROUNDING_RULES}\n{_V1_FORMAT_LINE}"
JUDGE_SYSTEMS = {"v1": JUDGE_SYSTEM, "v2": JUDGE_SYSTEM_V2}

MAX_JUDGE_CALLS_PER_MIN = 37.5  # stay under 40 new judge calls per minute on the NIM free tier
MAX_CONSECUTIVE_ERRORS = 5      # GLM failing this many times in a row: stop (never switch judge mid-run)

NO_SOURCE_TEXT ="(Không có nguyên văn: câu hỏi nằm ngoài nội dung quy chế; chấm theo đáp án mẫu.)"


def readable_source(unit_id: str) -> str:
    """qc3626.d43.k1 -> "Điều 43 khoản 1"; qc3626.d16 -> "Điều 16"; qc3626.d46.k1.đ -> "Điều 46 khoản 1 điểm đ"."""
    match = re.fullmatch(rf"{DOC_ID}\.d(\d+)(?:\.k(\d+)(?:\.(\w+))?)?", unit_id)
    if not match:
        raise ValueError(f"cannot read unit id {unit_id!r}")
    article, clause, point = match.groups()
    return " ".join(filter(None, [f"Điều {article}", clause and f"khoản {clause}", point and f"điểm {point}"]))


def judge_prompt(question: dict, answer: str, unit_text: dict[str, str], version: str = "v1") -> str:
    """The user message. v2 adds a "Căn cứ đúng:" line after the model answer (omitted for out_of_scope questions,
    which have no source units); everything else is identical to v1."""
    sources = "\n\n".join(f"[{uid}]\n{unit_text[uid]}" for uid in question["source_ids"]) or NO_SOURCE_TEXT
    grounds = ""
    if version == "v2" and question["source_ids"]:
        grounds = "Căn cứ đúng: " + "; ".join(readable_source(uid) for uid in question["source_ids"]) + "\n\n"
    return (f"CÂU HỎI:\n{question['question']}\n\nĐÁP ÁN MẪU:\n{question['answer']}\n\n{grounds}"
            f"NGUYÊN VĂN QUY CHẾ:\n{sources}\n\nCÂU TRẢ LỜI CẦN CHẤM:\n{answer}")


def prompt_sha256(version: str) -> str:
    return hashlib.sha256(JUDGE_SYSTEMS[version].encode("utf-8")).hexdigest()


def output_paths(path: Path, version: str, filtered: bool) -> list[Path]:
    """<stem>_judged.* for v1 as before; _v2 and _calib are added only for v2 / manifest-filtered runs."""
    suffix = "_judged" + ("_v2" if version == "v2" else "") + ("_calib" if filtered else "")
    stem = path.with_suffix("")
    return [Path(f"{stem}{suffix}.csv"), Path(f"{stem}{suffix}.json")]


def system_of(path: Path) -> str:
    match = re.fullmatch(r"\d{4}-\d{2}-\d{2}_(\w+?)_dev\.csv", path.name)
    if not match:
        raise ValueError(f"cannot read the system name from {path.name!r} (expected <date>_<system>_dev.csv)")
    return match.group(1)


def load_selection(manifest_path: Path) -> set[tuple[str, str, int]]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return {(m["system"], m["id"], int(m["run"])) for m in manifest["samples"].values()}


def select_rows(rows: list[dict], system: str, selection: set[tuple[str, str, int]]) -> list[dict]:
    """Rows of this system's csv that the manifest lists; every listed row must be present."""
    wanted = {key for key in selection if key[0] == system}
    kept = [r for r in rows if (system, r["id"], int(r["run"])) in wanted]
    if len(kept) != len(wanted):
        missing = sorted(wanted - {(system, r["id"], int(r["run"])) for r in kept})
        raise ValueError(f"{len(missing)} manifest row(s) of {system} not found in the csv: {missing[:3]}...")
    return kept


def pop_option(args: list[str], flag: str) -> str | None:
    """Remove `flag value` from args and return value (None if the flag is absent)."""
    if flag not in args:
        return None
    i = args.index(flag)
    if i + 1 >= len(args):
        raise ValueError(f"{flag} needs a value")
    value = args[i + 1]
    del args[i:i + 2]
    return value


def _utf8_console() -> None:
    """UTF-8 stdout/stderr on Windows consoles; skipped for streams that cannot be reconfigured (tests)."""
    for stream, kwargs in ((sys.stdout, {"encoding": "utf-8", "line_buffering": True}),
                           (sys.stderr, {"encoding": "utf-8"})):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(**kwargs)


def judge_file(path: Path, version: str, selection: set | None, overwrite: bool, questions: dict,
               unit_text: dict[str, str], manifest_name: str | None) -> int:
    if not path.name.endswith("_dev.csv"):
        print("LỖI: chỉ chấm file kết quả trên dev (*_dev.csv).", file=sys.stderr)
        return 2
    outputs = output_paths(path, version, selection is not None)
    existing = [p.name for p in outputs if p.exists()]
    if existing and not overwrite:
        print(f"LỖI: đã có {existing}; không ghi đè (dùng --overwrite nếu muốn thay).", file=sys.stderr)
        return 2
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    if selection is not None:
        try:
            rows = select_rows(rows, system_of(path), selection)
        except ValueError as e:
            print(f"LỖI: {e}", file=sys.stderr)
            return 2
    print(f"Chấm {len(rows)} câu trả lời (prompt {version}) → tối đa {len(rows)} lời gọi judge (cache dùng lại nếu có)")

    system_prompt = JUDGE_SYSTEMS[version]
    out_rows, fresh, errors, first_called = [], 0, [], ""
    consecutive_errors, next_fresh_at = 0, 0.0
    for row in rows:
        question = questions[row["id"]]  # KeyError if the CSV holds a non-dev id
        extra = {"judge_prompt": version} if version != "v1" else {}
        if row.get("error"):
            out_rows.append({**row, "judge_label": "", "judge_reason": "", "judge_model": "",
                             "judge_error": "answer call failed; not judged", **extra})
            continue
        # Keep new judge calls under MAX_JUDGE_CALLS_PER_MIN (cached results return at once)
        wait = next_fresh_at - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        started = time.monotonic()
        try:
            result = complete("judge", system_prompt,
                              [{"role": "user", "content": judge_prompt(question, row["answer"], unit_text, version)}],
                              json_schema=VERDICT_SCHEMA, run=0)
            consecutive_errors = 0
            if not result.from_cache:
                fresh += 1
                next_fresh_at = started + 60.0 / MAX_JUDGE_CALLS_PER_MIN
            first_called = min(first_called, result.called_at) if first_called else result.called_at
            verdict = result.parsed or {}
            out_rows.append({**row, "judge_label": verdict.get("label", ""), "judge_reason": verdict.get("reason", ""),
                             "judge_model": result.model_served or result.model_requested,
                             "judge_error": "" if result.parsed else "reply is not a valid verdict JSON", **extra})
        except LLMError as e:
            consecutive_errors += 1
            next_fresh_at = started + 60.0 / MAX_JUDGE_CALLS_PER_MIN
            errors.append({"id": row["id"], "run": row["run"], "http_code": e.http_code, "error": str(e)[:200]})
            out_rows.append({**row, "judge_label": "", "judge_reason": "", "judge_model": "",
                             "judge_error": f"HTTP {e.http_code}: {e}"[:200], **extra})
            print(f"  LỖI judge {row['id']} run {row['run']}: HTTP {e.http_code}")
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                print(f"DỪNG: GLM lỗi {consecutive_errors} lần liên tiếp; không chuyển sang giám khảo khác. "
                      "Không ghi file; phần đã chấm nằm trong cache.", file=sys.stderr)
                return 3

    out_csv, out_json = outputs
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(out_rows[0]))
        writer.writeheader()
        writer.writerows(out_rows)
    metadata = {
        "judged_at": datetime.now(VN_TZ).isoformat(timespec="seconds"), "input_csv": path.name,
        "judge_role": "judge", "judge_calibrated": False,
        "note": "Giám khảo CHƯA được hiệu chỉnh (hiệu chỉnh 30 câu theo PLAN #2 chưa làm); nhãn chỉ để tham khảo.",
        "judge_system_prompt": system_prompt, "fresh_judge_calls_this_invocation": fresh,
        "judge_calls_total": sum(bool(r["judge_label"]) or bool(r["judge_reason"]) for r in out_rows),
        "max_judge_calls_per_min": MAX_JUDGE_CALLS_PER_MIN,
        "not_judged_answer_errors": sum(r["judge_error"] == "answer call failed; not judged" for r in out_rows),
        "judge_first_called_at": first_called or None, "errors": errors,
        "labels": {label: sum(r["judge_label"] == label for r in out_rows) for label in LABELS},
    }
    if version != "v1" or selection is not None:  # v1 full-file metadata keeps its exact old shape
        metadata.update({
            "judge_prompt_version": version, "judge_prompt_sha256": prompt_sha256(version),
            "manifest": manifest_name, "n_rows": len(out_rows),
            "note": ("Prompt v2 (quy tắc căn cứ, chủ dự án chốt 27/10/2026) chưa được kiểm độ trùng với chấm tay; "
                     "nhãn chỉ để tham khảo.") if version == "v2" else metadata["note"],
        })
    out_json.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Ghi {out_csv.name} | lời gọi judge mới: {fresh} | lỗi: {len(errors)}")
    return 1 if errors else 0


def main(argv: list[str]) -> int:
    _utf8_console()
    args = [a for a in argv if a != "--overwrite"]
    overwrite = len(args) != len(argv)
    try:
        version = pop_option(args, "--judge-prompt") or "v1"
        manifest = pop_option(args, "--manifest")
    except ValueError as e:
        print(f"LỖI: {e}", file=sys.stderr)
        return 2
    if version not in JUDGE_SYSTEMS or not args:
        print("Cách dùng: python eval/judge_answers.py <file csv của run_eval> [file csv khác ...] [--overwrite] "
              "[--judge-prompt v1|v2] [--manifest <json>]", file=sys.stderr)
        return 2
    selection = load_selection(Path(manifest)) if manifest else None
    questions = {q["id"]: q for q in rm.load_dev_questions()}
    with UNITS_PATH.open(encoding="utf-8") as f:
        unit_text = {u["id"]: u["text"] for u in map(json.loads, f)}
    code = 0
    for arg in args:
        code = max(code, judge_file(Path(arg), version, selection, overwrite, questions, unit_text,
                                    Path(manifest).name if manifest else None))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
