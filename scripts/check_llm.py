"""Check that the LLM API keys in .env work: one tiny call to Claude and one to Gemini.

Run from the repo root:  python scripts/check_llm.py

For each provider, prints the model and its reply, then ĐẠT (OK) or LỖI (error).
API keys are never printed; any key value found in an error message is masked.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent

# Model IDs from docs/PLAN.md section 4, decision #2
CLAUDE_MODEL = "claude-haiku-5-5"
GEMINI_MODEL = "gemini-3.8-flash"

PROMPT = "Trả lời đúng một từ: thủ đô của Việt Nam là gì?"
SECRET_VARS = ("ANTHROPIC_API_KEY", "GEMINI_API_KEY")


def mask_secrets(text: str) -> str:
    """Replace any API key value that leaked into a message with ***."""
    for name in SECRET_VARS:
        value = os.getenv(name)
        if value:
            text = text.replace(value, "***")
    return text


def fail(provider: str, reason: str) -> bool:
    print(f"[{provider}] LỖI: {mask_secrets(reason)}")
    return False


def check_claude() -> bool:
    provider = "Claude"
    if not os.getenv("ANTHROPIC_API_KEY"):
        return fail(provider, "thiếu ANTHROPIC_API_KEY trong .env")
    try:
        import anthropic
    except ImportError:
        return fail(provider, "chưa cài gói anthropic")

    client = anthropic.Anthropic(timeout=30.0, max_retries=1)
    try:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=32,
            # Haiku 5.5 thinks by default; disabling is allowed at its default effort
            # and keeps this one-word reply well inside max_tokens.
            thinking={"type": "disabled"},
            messages=[{"role": "user", "content": PROMPT}],
        )
    except anthropic.AuthenticationError:
        return fail(provider, "key không hợp lệ (HTTP 401)")
    except anthropic.PermissionDeniedError:
        return fail(provider, "key không có quyền dùng model này (HTTP 403)")
    except anthropic.NotFoundError:
        return fail(provider, f"không tìm thấy model {CLAUDE_MODEL} (HTTP 404)")
    except anthropic.RateLimitError:
        return fail(provider, "vượt giới hạn hoặc hết credit (HTTP 429)")
    except anthropic.APIStatusError as e:
        return fail(provider, f"HTTP {e.status_code}: {e.message}")
    except anthropic.APIConnectionError as e:
        return fail(provider, f"không kết nối được API: {e}")

    if response.stop_reason == "refusal":
        return fail(provider, "model từ chối trả lời (stop_reason=refusal)")
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    print(f"[{provider}] model: {response.model} | trả lời: {text!r} | stop_reason: {response.stop_reason}")
    print(f"[{provider}] ĐẠT")
    return True


def check_gemini() -> bool:
    provider = "Gemini"
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return fail(provider, "thiếu GEMINI_API_KEY trong .env")
    try:
        from google import genai
        from google.genai import errors, types
    except ImportError:
        return fail(provider, "chưa cài gói google-genai")

    # Pass the key explicitly so a GOOGLE_API_KEY set elsewhere cannot take precedence.
    # HttpOptions.timeout is in milliseconds; without it a stalled request can hang indefinitely.
    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=30_000))

    # Metadata lookup first: it validates the key without spending a generation request,
    # so an overloaded model (503/504 below) is not mistaken for a bad key.
    try:
        client.models.get(model=GEMINI_MODEL)
    except errors.APIError as e:
        return fail(provider, f"key hoặc model không hợp lệ, HTTP {getattr(e, 'code', '?')}: {getattr(e, 'message', e)}")
    except Exception as e:
        return fail(provider, f"{type(e).__name__}: {e}")
    print(f"[{provider}] key hợp lệ, model {GEMINI_MODEL} tồn tại")

    try:
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=PROMPT,
            # Thinking models spend output tokens on reasoning; leave headroom for the reply.
            config=types.GenerateContentConfig(max_output_tokens=256),
        )
    except errors.APIError as e:
        code = getattr(e, "code", "?")
        if code in (503, 504):
            return fail(provider, f"model đang quá tải (HTTP {code}), key vẫn hợp lệ; thử lại sau")
        return fail(provider, f"HTTP {code}: {getattr(e, 'message', e)}")
    except Exception as e:  # network or SDK errors outside the API error hierarchy
        return fail(provider, f"{type(e).__name__}: {e}")

    text = (response.text or "").strip()
    model = getattr(response, "model_version", None) or GEMINI_MODEL
    finish = response.candidates[0].finish_reason if response.candidates else None
    print(f"[{provider}] model: {model} | trả lời: {text!r} | finish_reason: {finish}")
    if not text:
        print(f"[{provider}] Lưu ý: gọi được API nhưng không có chữ trả về")
    print(f"[{provider}] ĐẠT")
    return True


def main() -> int:
    # Windows consoles may not default to UTF-8; force it so Vietnamese prints correctly.
    # Line buffering shows each result as soon as it is printed, even when output is piped.
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    load_dotenv(REPO_ROOT / ".env")

    results = [check_claude(), check_gemini()]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
