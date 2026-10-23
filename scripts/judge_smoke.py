"""Smoke test of the LLM-judge role: one question, three candidate answers, several judge models.

This is NOT the official evaluation. It only checks that a candidate judge model returns valid
JSON labels that match hand-set expectations. Run from the repo root:
    python scripts/judge_smoke.py

Prints a table (model | A | B | C | correct /3 | valid JSON | time per answer | finish_reason | error)
and caches every call (model ID, run time, raw reply) to data/cache/judge_smoke_<timestamp>.json.
API keys are never printed or saved.
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# scripts/ is on sys.path when this file is run directly; reuse the key masking and NIM helper
from check_llm import REPO_ROOT, VN_TZ, mask_secrets, nvidia_chat

CACHE_DIR = REPO_ROOT / "data" / "cache"
PROCESSED_JSON = REPO_ROOT / "data" / "processed" / "qc3626.json"

JUDGES = [
    ("nvidia", "z-ai/glm-5.3-flash"),
    ("gemini", "gemini-3.6-flash"),
    ("gemini", "gemini-3.8-flash"),
]
MAX_OUTPUT_TOKENS = 4096  # reasoning models spend output tokens before the JSON answer
TIMEOUT_S = 90

LABELS = ("dung", "sai", "mot_phan")
VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "enum": list(LABELS)},
        "reason": {"type": "string"},
    },
    "required": ["label", "reason"],
    "additionalProperties": False,
}
# Gemini's response_schema uses the OpenAPI subset (no additionalProperties)
GEMINI_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "label": {"type": "STRING", "enum": list(LABELS)},
        "reason": {"type": "STRING"},
    },
    "required": ["label", "reason"],
}

QUESTION = (
    "Sinh viên chương trình chất lượng cao đã tích lũy 50 tín chỉ, điểm TBC tích lũy 1,35 "
    "có bị cảnh báo học vụ không?"
)
REFERENCE = (
    "Có. CLC tích lũy 40–80 tín chỉ là sinh viên năm thứ hai (Điều 17 khoản 2); năm thứ hai bị cảnh báo "
    "khi TBC tích lũy dưới 1,40 (Điều 44 khoản 1 điểm a); 1,35 < 1,40."
)
SOURCE_IDS = ("qc3626.d17.k2", "qc3626.d44.k1.a")

# (key, candidate answer, expected label)
CANDIDATES = [
    (
        "A",
        "Có, sinh viên này bị cảnh báo học vụ. Theo Điều 17 khoản 2, sinh viên chương trình chất lượng cao "
        "tích lũy từ 40 đến 80 tín chỉ là sinh viên năm thứ hai. Theo Điều 44 khoản 1 điểm a, sinh viên năm thứ hai "
        "bị cảnh báo học vụ khi điểm trung bình chung tích lũy dưới 1,40. Điểm 1,35 nhỏ hơn 1,40 nên bị cảnh báo.",
        "dung",
    ),
    ("B", "Không bị cảnh báo vì ngưỡng là dưới 1,20.", "sai"),
    ("C", "Có bị cảnh báo.", "mot_phan"),
]

SYSTEM_PROMPT = """Bạn là giám khảo chấm câu trả lời về quy chế đào tạo đại học.
Chỉ dựa vào ĐÁP ÁN MẪU và NGUYÊN VĂN QUY CHẾ được cung cấp, không dùng hiểu biết bên ngoài.
Gán đúng một nhãn cho CÂU TRẢ LỜI CẦN CHẤM:
- "dung": kết luận đúng và nêu đủ các ý chính của đáp án mẫu (căn cứ và ngưỡng).
- "mot_phan": kết luận đúng nhưng thiếu căn cứ hoặc ngưỡng, hoặc thiếu ý chính.
- "sai": kết luận sai, hoặc nêu căn cứ/ngưỡng mâu thuẫn với quy chế.
Chỉ trả về một đối tượng JSON: {"label": "dung" | "sai" | "mot_phan", "reason": "<một câu ngắn bằng tiếng Việt>"}."""


def load_sources() -> str:
    units = {u["id"]: u for u in json.loads(PROCESSED_JSON.read_text(encoding="utf-8"))["units"]}
    # The table row lives in the khoản's full text; the threshold is the point's own text
    texts = {"qc3626.d17.k2": units["qc3626.d17.k2"]["text"], "qc3626.d44.k1.a": units["qc3626.d44.k1.a"]["own_text"]}
    return "\n\n".join(f"[{uid}]\n{texts[uid]}" for uid in SOURCE_IDS)


def build_user_prompt(sources: str, answer: str) -> str:
    return (
        f"CÂU HỎI:\n{QUESTION}\n\nĐÁP ÁN MẪU:\n{REFERENCE}\n\n"
        f"NGUYÊN VĂN QUY CHẾ:\n{sources}\n\nCÂU TRẢ LỜI CẦN CHẤM:\n{answer}"
    )


def parse_verdict(text: str) -> dict | None:
    """Return {"label", "reason"} if the reply holds a valid verdict JSON, else None."""
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)  # tolerate code fences or stray prose
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("label") not in LABELS or not isinstance(data.get("reason"), str):
        return None
    return {"label": data["label"], "reason": data["reason"]}


class JudgeError(Exception):
    """A call failed; carries an HTTP code when there is one."""

    def __init__(self, message: str, http_code: int | None = None):
        super().__init__(message)
        self.http_code = http_code


def call_nvidia(model_id: str, user_prompt: str) -> tuple[str, str, bool]:
    """Return (reply text, finish_reason, structured_output_used)."""
    import httpx

    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}]
    response_format = {"type": "json_schema", "json_schema": {"name": "verdict", "schema": VERDICT_SCHEMA, "strict": True}}
    for structured in (True, False):
        extra = {"response_format": response_format} if structured else {}
        try:
            body = nvidia_chat(model_id, messages, MAX_OUTPUT_TOKENS, timeout=TIMEOUT_S, **extra)
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            # Endpoint may reject json_schema; retry once with the prompt-only JSON instruction
            if structured and code in (400, 422):
                continue
            raise JudgeError(f"HTTP {code}: {e.response.text[:200]}", code) from e
        except httpx.HTTPError as e:
            raise JudgeError(f"{type(e).__name__}: {e}") from e
        choice = (body.get("choices") or [{}])[0]
        return ((choice.get("message") or {}).get("content") or ""), str(choice.get("finish_reason")), structured
    raise JudgeError("unreachable")


_gemini_client = None


def call_gemini(model_id: str, user_prompt: str) -> tuple[str, str, bool]:
    """Return (reply text, finish_reason, structured_output_used)."""
    global _gemini_client
    from google import genai
    from google.genai import errors, types

    if _gemini_client is None:
        _gemini_client = genai.Client(
            api_key=os.environ["GEMINI_API_KEY"], http_options=types.HttpOptions(timeout=TIMEOUT_S * 1000)
        )
    try:
        response = _gemini_client.models.generate_content(
            model=model_id,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                response_mime_type="application/json",
                response_schema=GEMINI_SCHEMA,
            ),
        )
    except errors.APIError as e:
        code = getattr(e, "code", None)
        raise JudgeError(f"HTTP {code}: {getattr(e, 'message', e)}", code) from e
    except Exception as e:
        raise JudgeError(f"{type(e).__name__}: {e}") from e
    finish = response.candidates[0].finish_reason if response.candidates else None
    return (response.text or ""), getattr(finish, "name", str(finish)), True


CALLERS = {"nvidia": call_nvidia, "gemini": call_gemini}


def run_judge(provider: str, model_id: str, sources: str) -> dict:
    """Judge A, B, C with one model; stop at the first error and move on to the next model."""
    row = {"provider": provider, "model": model_id, "calls": [], "error": None}
    for key, answer, expected in CANDIDATES:
        start = time.perf_counter()
        try:
            text, finish, structured = CALLERS[provider](model_id, build_user_prompt(sources, answer))
        except JudgeError as e:
            row["error"] = f"{key}: {mask_secrets(str(e))}"
            row["calls"].append({"key": key, "expected": expected, "error": mask_secrets(str(e)),
                                 "http_code": e.http_code, "seconds": round(time.perf_counter() - start, 1)})
            print(f"  {model_id} {key}: LỖI {mask_secrets(str(e))[:120]}")
            break
        seconds = round(time.perf_counter() - start, 1)
        verdict = parse_verdict(text)
        row["calls"].append({
            "key": key, "expected": expected, "label": verdict["label"] if verdict else None,
            "reason": verdict["reason"] if verdict else None, "json_ok": verdict is not None,
            "structured_output": structured, "finish_reason": finish, "seconds": seconds, "raw": text,
        })
        print(f"  {model_id} {key}: {verdict['label'] if verdict else 'JSON lỗi'} (kỳ vọng {expected}) {seconds}s {finish}")
    return row


def print_table(rows: list[dict]) -> None:
    header = ["model", "A", "B", "C", "đúng /3", "JSON hợp lệ?", "thời gian mỗi câu", "finish_reason", "lỗi"]
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for row in rows:
        by_key = {c["key"]: c for c in row["calls"]}
        cells = [row["model"]]
        for key, _, expected in CANDIDATES:
            c = by_key.get(key)
            if c is None or "label" not in c:
                cells.append("—")
            else:
                label = c["label"] or "JSON lỗi"
                cells.append(f"{label} {'✓' if c['label'] == expected else '✗'}")
        done = [c for c in row["calls"] if "label" in c]
        if done:
            cells.append(f"{sum(c['label'] == c['expected'] for c in done)}/3")
            cells.append(f"{sum(c['json_ok'] for c in done)}/{len(done)}"
                         + ("" if all(c["structured_output"] for c in done) else " (không ép schema)"))
        else:
            # Nothing was judged (error on the first call): show a dash, not a misleading 0/3
            cells.extend(["—", "—"])
        cells.append(", ".join(f"{c['key']} {c['seconds']}s" for c in row["calls"]) or "—")
        cells.append(", ".join(sorted({c["finish_reason"] for c in done})) or "—")
        cells.append(row["error"] or "")
        lines.append("| " + " | ".join(cells) + " |")
    print("\n".join(lines))


def main() -> int:
    # Windows consoles may not default to UTF-8; line buffering shows progress as it happens
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    load_dotenv(REPO_ROOT / ".env")
    sources = load_sources()
    started = datetime.now(VN_TZ)

    rows = []
    for provider, model_id in JUDGES:
        key_var = "NVIDIA_API_KEY" if provider == "nvidia" else "GEMINI_API_KEY"
        if not os.getenv(key_var):
            rows.append({"provider": provider, "model": model_id, "calls": [], "error": f"thiếu {key_var}"})
            continue
        print(f"[{provider}] {model_id}")
        rows.append(run_judge(provider, model_id, sources))

    print()
    print_table(rows)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = CACHE_DIR / f"judge_smoke_{started.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({
        "run_started": started.isoformat(timespec="seconds"),
        "question": QUESTION, "reference": REFERENCE, "source_ids": SOURCE_IDS,
        "max_output_tokens": MAX_OUTPUT_TOKENS, "results": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nĐã lưu chi tiết vào {out.relative_to(REPO_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
