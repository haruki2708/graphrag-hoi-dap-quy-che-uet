"""Check that the LLM API keys in .env work: one tiny call per provider (Claude, Gemini, NVIDIA NIM).

Run from the repo root:
    python scripts/check_llm.py                                     # all providers (default)
    python scripts/check_llm.py --only gemini                       # Gemini only, no Claude credit spent
    python scripts/check_llm.py --only claude                       # Claude only
    python scripts/check_llm.py --only nvidia                       # NVIDIA NIM only
    python scripts/check_llm.py --gemini-model gemini-3.6-flash     # diagnose with another Gemini model
    python scripts/check_llm.py --nvidia-model z-ai/glm-5.3-flash   # choose the NVIDIA model
    python scripts/check_llm.py --only gemini --log                 # also append results to the log

For each provider, prints the model and its reply, then ĐẠT (OK) or LỖI (error).
--log appends one row per provider to data/cache/check_llm_log.csv
(time +07:00, provider, model, status, HTTP code on error).
API keys are never printed or logged; any key value found in an error message is masked.
"""

import argparse
import csv
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = REPO_ROOT / "data" / "cache" / "check_llm_log.csv"
LOG_FIELDS = ("time", "provider", "model", "status", "http_code")
# Vietnam has no daylight saving time, so a fixed offset is exact (zoneinfo needs tzdata on Windows)
VN_TZ = timezone(timedelta(hours=7))

# Model IDs from docs/PLAN.md section 4, decision #2
CLAUDE_MODEL = "claude-haiku-5-5"
GEMINI_MODEL = "gemini-3.8-flash"
# Judge (decision #2, changed 24/10/2026): GLM-5.3-Flash on NVIDIA NIM; Gemini above is the fallback
NVIDIA_MODEL = "z-ai/glm-5.3-flash"
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

PROMPT = "Trả lời đúng một từ: thủ đô của Việt Nam là gì?"
SECRET_VARS = ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "NVIDIA_API_KEY")


@dataclass
class CheckResult:
    provider: str
    model: str
    ok: bool
    http_code: int | None = None


def mask_secrets(text: str) -> str:
    """Replace any API key value that leaked into a message with ***."""
    for name in SECRET_VARS:
        value = os.getenv(name)
        if value:
            text = text.replace(value, "***")
    return text


def fail(provider: str, model: str, reason: str, http_code: int | None = None) -> CheckResult:
    print(f"[{provider}] LỖI: {mask_secrets(reason)}")
    return CheckResult(provider, model, ok=False, http_code=http_code)


def check_claude() -> CheckResult:
    provider, model_id = "Claude", CLAUDE_MODEL
    if not os.getenv("ANTHROPIC_API_KEY"):
        return fail(provider, model_id, "thiếu ANTHROPIC_API_KEY trong .env")
    try:
        import anthropic
    except ImportError:
        return fail(provider, model_id, "chưa cài gói anthropic")

    client = anthropic.Anthropic(timeout=30.0, max_retries=1)
    try:
        response = client.messages.create(
            model=model_id,
            max_tokens=32,
            # Haiku 5.5 thinks by default; disabling is allowed at its default effort
            # and keeps this one-word reply well inside max_tokens.
            thinking={"type": "disabled"},
            messages=[{"role": "user", "content": PROMPT}],
        )
    except anthropic.AuthenticationError:
        return fail(provider, model_id, "key không hợp lệ (HTTP 401)", 401)
    except anthropic.PermissionDeniedError:
        return fail(provider, model_id, "key không có quyền dùng model này (HTTP 403)", 403)
    except anthropic.NotFoundError:
        return fail(provider, model_id, f"không tìm thấy model {model_id} (HTTP 404)", 404)
    except anthropic.RateLimitError:
        return fail(provider, model_id, "vượt giới hạn hoặc hết credit (HTTP 429)", 429)
    except anthropic.APIStatusError as e:
        return fail(provider, model_id, f"HTTP {e.status_code}: {e.message}", e.status_code)
    except anthropic.APIConnectionError as e:
        return fail(provider, model_id, f"không kết nối được API: {e}")

    if response.stop_reason == "refusal":
        return fail(provider, model_id, "model từ chối trả lời (stop_reason=refusal)")
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    print(f"[{provider}] model: {response.model} | trả lời: {text!r} | stop_reason: {response.stop_reason}")
    print(f"[{provider}] ĐẠT")
    return CheckResult(provider, model_id, ok=True)


def check_gemini(model_id: str = GEMINI_MODEL) -> CheckResult:
    provider = "Gemini"
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return fail(provider, model_id, "thiếu GEMINI_API_KEY trong .env")
    try:
        from google import genai
        from google.genai import errors, types
    except ImportError:
        return fail(provider, model_id, "chưa cài gói google-genai")

    # Pass the key explicitly so a GOOGLE_API_KEY set elsewhere cannot take precedence.
    # HttpOptions.timeout is in milliseconds; without it a stalled request can hang indefinitely.
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=30_000))

    # Metadata lookup first: it validates the key without spending a generation request,
    # so an overloaded model (503/504 below) is not mistaken for a bad key.
    try:
        client.models.get(model=model_id)
    except errors.APIError as e:
        code = getattr(e, "code", None)
        return fail(provider, model_id, f"key hoặc model không hợp lệ, HTTP {code}: {getattr(e, 'message', e)}", code)
    except Exception as e:
        return fail(provider, model_id, f"{type(e).__name__}: {e}")
    print(f"[{provider}] key hợp lệ, model {model_id} tồn tại")

    try:
        response = client.models.generate_content(
            model=model_id,
            contents=PROMPT,
            # Thinking models spend output tokens on reasoning; leave headroom for the reply.
            config=types.GenerateContentConfig(max_output_tokens=256),
        )
    except errors.APIError as e:
        code = getattr(e, "code", None)
        if code in (503, 504):
            return fail(provider, model_id, f"model đang quá tải (HTTP {code}), key vẫn hợp lệ; thử lại sau", code)
        return fail(provider, model_id, f"HTTP {code}: {getattr(e, 'message', e)}", code)
    except Exception as e:  # network or SDK errors outside the API error hierarchy
        return fail(provider, model_id, f"{type(e).__name__}: {e}")

    text = (response.text or "").strip()
    served = getattr(response, "model_version", None) or model_id
    finish = response.candidates[0].finish_reason if response.candidates else None
    print(f"[{provider}] model: {served} | trả lời: {text!r} | finish_reason: {finish}")
    if not text:
        print(f"[{provider}] Lưu ý: gọi được API nhưng không có chữ trả về")
    print(f"[{provider}] ĐẠT")
    return CheckResult(provider, model_id, ok=True)


def nvidia_chat(model_id: str, messages: list[dict], max_tokens: int, timeout: float = 60.0, **extra) -> dict:
    """POST one chat completion to the NVIDIA NIM OpenAI-compatible endpoint; return the JSON body.

    Raises httpx.HTTPStatusError on 4xx/5xx and httpx.HTTPError on network errors.
    """
    import httpx

    response = httpx.post(
        f"{NVIDIA_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['NVIDIA_API_KEY']}", "Accept": "application/json"},
        json={"model": model_id, "messages": messages, "max_tokens": max_tokens, **extra},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def check_nvidia(model_id: str = NVIDIA_MODEL) -> CheckResult:
    provider = "NVIDIA"
    if not os.getenv("NVIDIA_API_KEY"):
        return fail(provider, model_id, "thiếu NVIDIA_API_KEY trong .env")
    try:
        import httpx
    except ImportError:
        return fail(provider, model_id, "chưa có gói httpx")

    try:
        # Reasoning models spend output tokens before answering; leave headroom for the reply.
        body = nvidia_chat(model_id, [{"role": "user", "content": PROMPT}], max_tokens=512, timeout=60.0)
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        detail = e.response.text[:300]
        if code in (401, 403):
            return fail(provider, model_id, f"key không hợp lệ hoặc không có quyền (HTTP {code}): {detail}", code)
        if code == 404:
            return fail(provider, model_id, f"không tìm thấy model {model_id} (HTTP 404): {detail}", code)
        if code in (429, 503, 504):
            return fail(provider, model_id, f"quá tải hoặc vượt giới hạn (HTTP {code}): {detail}", code)
        return fail(provider, model_id, f"HTTP {code}: {detail}", code)
    except httpx.HTTPError as e:
        return fail(provider, model_id, f"không kết nối được API: {type(e).__name__}: {e}")

    choice = (body.get("choices") or [{}])[0]
    text = ((choice.get("message") or {}).get("content") or "").strip()
    print(f"[{provider}] model: {body.get('model', model_id)} | trả lời: {text!r} | finish_reason: {choice.get('finish_reason')}")
    if not text:
        print(f"[{provider}] Lưu ý: gọi được API nhưng không có chữ trả về")
    print(f"[{provider}] ĐẠT")
    return CheckResult(provider, model_id, ok=True)


def append_log(results: list[CheckResult]) -> None:
    """Append one CSV row per result. Holds no key or message text, only codes."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    is_new = not LOG_PATH.exists() or LOG_PATH.stat().st_size == 0
    now = datetime.now(VN_TZ).isoformat(timespec="seconds")
    # utf-8-sig so Excel shows ĐẠT/LỖI correctly; the BOM is only written to a new, empty file
    with LOG_PATH.open("a", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(LOG_FIELDS)
        for r in results:  
            writer.writerow([now, r.provider, r.model, "ĐẠT" if r.ok else "LỖI", r.http_code or ""])
    print(f"Đã ghi {len(results)} dòng vào {LOG_PATH.relative_to(REPO_ROOT).as_posix()}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Check that the LLM API keys in .env work.")
    parser.add_argument(
        "--only",
        choices=("all", "claude", "gemini", "nvidia"),
        default="all",
        help="which provider to call (default: all)",
    )
    parser.add_argument(
        "--gemini-model",
        default=GEMINI_MODEL,
        help=f"Gemini model to call (default: {GEMINI_MODEL}, the judge fallback from decision #2); "
        "another model is for diagnosis only",
    )
    parser.add_argument(
        "--nvidia-model",
        default=NVIDIA_MODEL,
        help=f"NVIDIA NIM model to call (default: {NVIDIA_MODEL}, the judge from decision #2)",
    )
    parser.add_argument(
        "--log",
        action="store_true",
        help=f"append results to {LOG_PATH.relative_to(REPO_ROOT).as_posix()}",
    )
    args = parser.parse_args()

    # Windows consoles may not default to UTF-8; force it so Vietnamese prints correctly.
    # Line buffering shows each result as soon as it is printed, even when output is piped.
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    load_dotenv(REPO_ROOT / ".env")

    results = []
    if args.only in ("all", "claude"):
        results.append(check_claude())
    if args.only in ("all", "gemini"):
        results.append(check_gemini(args.gemini_model))
    if args.only in ("all", "nvidia"):
        results.append(check_nvidia(args.nvidia_model))
    if args.log:
        append_log(results)
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
