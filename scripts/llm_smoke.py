"""Real-call smoke test of src/llm: one answer call, one judge call with a JSON schema, the answer call
again (run=0, must come from the disk cache), one Anthropic call with a JSON schema (structured output),
and one judge_fallback (Gemini) call. Costs a few hundred tokens.

Run from the repo root:  python scripts/llm_smoke.py
"""

import sys
from pathlib import Path

# Entry-script convention: put the repo root on sys.path, then import from src.*
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.llm import LLMError, LLMResult, complete  # noqa: E402

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "enum": ["dung", "sai", "mot_phan"]},
        "reason": {"type": "string"},
    },
    "required": ["label", "reason"],
    "additionalProperties": False,
}

CAPITAL_SCHEMA = {
    "type": "object",
    "properties": {"thu_do": {"type": "string"}},
    "required": ["thu_do"],
    "additionalProperties": False,
}

ANSWER_SYSTEM = "Trả lời ngắn gọn bằng tiếng Việt, đúng một câu."
ANSWER_MESSAGES = [{"role": "user", "content": "Thủ đô của Việt Nam là thành phố nào?"}]

JUDGE_SYSTEM = (
    "Bạn là giám khảo. So câu trả lời với đáp án mẫu và gán nhãn: \"dung\" nếu đúng và đủ ý, "
    "\"mot_phan\" nếu đúng nhưng thiếu ý, \"sai\" nếu sai. Trả về JSON {\"label\", \"reason\"}."
)
JUDGE_MESSAGES = [{
    "role": "user",
    "content": "CÂU HỎI: Thủ đô của Việt Nam là thành phố nào?\nĐÁP ÁN MẪU: Hà Nội.\nCÂU TRẢ LỜI CẦN CHẤM: Hồ Chí Minh.",
}]


def show(title: str, result: LLMResult) -> None:
    info = result.to_dict()
    text = info.pop("text")
    print(f"--- {title}")
    print(f"    text: {text[:120]!r}{' …' if len(text) > 120 else ''}")
    for key, value in info.items():
        print(f"    {key}: {value}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    ok = True
    try:
        first = complete("answer", ANSWER_SYSTEM, ANSWER_MESSAGES, run=0)
        show("answer (run=0)", first)
        judge = complete("judge", JUDGE_SYSTEM, JUDGE_MESSAGES, json_schema=VERDICT_SCHEMA, run=0)
        show("judge (run=0, json_schema)", judge)
        again = complete("answer", ANSWER_SYSTEM, ANSWER_MESSAGES, run=0)
        show("answer again (run=0)", again)
    except LLMError as e:
        print(f"LỖI [{e.role}/{e.provider}] HTTP {e.http_code} sau {e.attempts} lần: {e}")
        return 1

    # (c) Anthropic structured output with a small schema (falls back to prompt JSON on 400/422)
    try:
        structured = complete("answer", "Trả về JSON.", [{"role": "user", "content": "Thủ đô của Việt Nam là gì?"}],
                              json_schema=CAPITAL_SCHEMA, run=0)
        show("answer + json_schema (Anthropic structured output)", structured)
    except LLMError as e:
        print(f"LỖI [{e.role}/{e.provider}] HTTP {e.http_code} sau {e.attempts} lần: {e}")
        return 1

    # (d) judge_fallback (Gemini) through complete(); 503/504 is Google overload, recorded but not a failure
    fallback_note = None
    try:
        fallback = complete("judge_fallback", JUDGE_SYSTEM, JUDGE_MESSAGES, json_schema=VERDICT_SCHEMA, run=0)
        show("judge_fallback (Gemini, json_schema)", fallback)
    except LLMError as e:
        fallback = None
        fallback_note = f"HTTP {e.http_code} sau {e.attempts} lần: {e}"
        print(f"--- judge_fallback: LỖI {fallback_note}")

    checks = {
        "Haiku trả lời có chữ": bool(first.text.strip()),
        "GLM trả JSON hợp lệ": judge.parsed is not None,
        "Lần gọi lại lấy từ cache": again.from_cache,
        "Anthropic structured output trả JSON hợp lệ": structured.parsed is not None,
    }
    if fallback is not None:
        checks["Gemini (judge_fallback) trả JSON hợp lệ"] = fallback.parsed is not None
    elif fallback_note and any(f"HTTP {c}" in fallback_note for c in (503, 504)):
        print(f"Ghi nhận: judge_fallback quá tải ({fallback_note[:12]}) — không tính là lỗi")
    else:
        checks["Gemini (judge_fallback) gọi được"] = False
    print()
    for name, passed in checks.items():
        print(f"{'ĐẠT' if passed else 'LỖI'}: {name}")
        ok &= passed
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
