"""Provider adapters: one HTTP call each, no retries.

Each adapter takes a RoleConfig plus a canonical request (system prompt, user/assistant messages with
string content, optional JSON schema) and returns a RawResponse, or raises TransientError (worth
retrying) / PermanentError (not worth retrying). Retries, caching and metadata live in core.py.
"""

import json
import os
from dataclasses import dataclass

import httpx

from .config import RoleConfig

# HTTP codes worth retrying; anything else 4xx is permanent
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504, 529})

API_KEY_VARS = {"anthropic": "ANTHROPIC_API_KEY", "nvidia": "NVIDIA_API_KEY", "gemini": "GEMINI_API_KEY"}


@dataclass
class RawResponse:
    text: str
    model_served: str | None
    finish_reason: str | None
    input_tokens: int | None
    output_tokens: int | None
    structured_output: bool  # True when the provider enforced the JSON schema


class ProviderError(Exception):
    def __init__(self, message: str, http_code: int | None = None, retry_after_s: float | None = None):
        super().__init__(message)
        self.http_code = http_code
        self.retry_after_s = retry_after_s


class TransientError(ProviderError):
    """Timeouts, network errors and RETRYABLE_STATUS codes."""


class PermanentError(ProviderError):
    """Bad request, auth, not found, unprocessable: retrying cannot help."""


def status_error(message: str, code: int, retry_after: str | None = None) -> ProviderError:
    cls = TransientError if code in RETRYABLE_STATUS else PermanentError
    return cls(message, code, parse_retry_after(retry_after))


def parse_retry_after(value: str | None) -> float | None:
    """Retry-After in seconds; the HTTP-date form is ignored (falls back to backoff)."""
    try:
        return max(0.0, float(value)) if value is not None else None
    except ValueError:
        return None


def api_key(provider: str) -> str:
    var = API_KEY_VARS[provider]
    value = os.getenv(var)
    if not value:
        raise PermanentError(f"missing {var} in environment (.env)")
    return value


def json_instruction(schema: dict) -> str:
    return (
        "\n\nReturn only one JSON object that matches this JSON Schema, with no other text:\n"
        + json.dumps(schema, ensure_ascii=False)
    )


# --- Anthropic -----------------------------------------------------------------------------------

def call_anthropic(cfg: RoleConfig, system: str, messages: list[dict], json_schema: dict | None) -> RawResponse:
    import anthropic

    # max_retries=0: core.py is the only retry layer. Explicit api_key: a v2 client holds one credential.
    client = anthropic.Anthropic(api_key=api_key("anthropic"), max_retries=0, timeout=cfg.timeout_s)
    params = dict(cfg.params)
    output_config = {}
    if "effort" in params:
        output_config["effort"] = params.pop("effort")
    if json_schema is not None:
        output_config["format"] = {"type": "json_schema", "schema": json_schema}
    kwargs = {}
    if "thinking" in params:
        kwargs["thinking"] = {"type": params.pop("thinking")}
    if params:
        raise PermanentError(f"unsupported anthropic params in config: {sorted(params)}")
    if output_config:
        kwargs["output_config"] = output_config
    if system:
        kwargs["system"] = system

    try:
        response = client.messages.create(model=cfg.model, max_tokens=cfg.max_tokens, messages=messages, **kwargs)
    except anthropic.APIStatusError as e:
        raise status_error(f"anthropic HTTP {e.status_code}: {e.message}", e.status_code,
                           e.response.headers.get("retry-after")) from e
    except anthropic.APIConnectionError as e:  # includes APITimeoutError
        raise TransientError(f"anthropic {type(e).__name__}: {e}") from e

    # Responses can start with thinking blocks: read text blocks by type, never by position
    text = "".join(b.text for b in response.content if b.type == "text")
    return RawResponse(
        text=text,
        model_served=response.model,
        finish_reason=response.stop_reason,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        structured_output=json_schema is not None,
    )


# --- NVIDIA NIM (OpenAI-compatible) --------------------------------------------------------------

def call_nvidia(cfg: RoleConfig, system: str, messages: list[dict], json_schema: dict | None) -> RawResponse:
    if not cfg.base_url:
        raise PermanentError(f"role {cfg.name!r}: nvidia provider needs base_url")
    headers = {"Authorization": f"Bearer {api_key('nvidia')}", "Accept": "application/json"}

    attempts = [True, False] if json_schema is not None else [False]
    for structured in attempts:
        sys_text = system
        body = {"model": cfg.model, "max_tokens": cfg.max_tokens, **cfg.params}
        if structured:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": json_schema, "strict": True},
            }
        elif json_schema is not None:
            # Endpoint rejected response_format: ask for the JSON in the prompt instead
            sys_text = (system or "") + json_instruction(json_schema)
        body["messages"] = ([{"role": "system", "content": sys_text}] if sys_text else []) + messages

        try:
            response = httpx.post(f"{cfg.base_url}/chat/completions", headers=headers, json=body,
                                  timeout=cfg.timeout_s)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            if structured and code in (400, 422):
                continue
            raise status_error(f"nvidia HTTP {code}: {e.response.text[:300]}", code,
                               e.response.headers.get("retry-after")) from e
        except httpx.HTTPError as e:  # timeouts and network errors
            raise TransientError(f"nvidia {type(e).__name__}: {e}") from e

        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        usage = data.get("usage") or {}
        return RawResponse(
            text=(choice.get("message") or {}).get("content") or "",
            model_served=data.get("model"),
            finish_reason=choice.get("finish_reason"),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            structured_output=structured,
        )
    raise PermanentError("nvidia: no request was sent")  # unreachable: attempts is never empty


# --- Gemini --------------------------------------------------------------------------------------

def call_gemini(cfg: RoleConfig, system: str, messages: list[dict], json_schema: dict | None) -> RawResponse:
    from google import genai
    from google.genai import errors, types

    client = genai.Client(
        api_key=api_key("gemini"),
        http_options=types.HttpOptions(
            timeout=int(cfg.timeout_s * 1000),  # milliseconds
            retry_options=types.HttpRetryOptions(attempts=1),  # core.py is the only retry layer
        ),
    )
    params = dict(cfg.params)
    config = {"max_output_tokens": cfg.max_tokens}
    if system:
        config["system_instruction"] = system
    if "thinking_level" in params:
        config["thinking_config"] = types.ThinkingConfig(thinking_level=params.pop("thinking_level"))
    config.update(params)
    if json_schema is not None:
        config["response_mime_type"] = "application/json"
        config["response_json_schema"] = json_schema
    contents = [
        {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
        for m in messages
    ]

    try:
        response = client.models.generate_content(
            model=cfg.model, contents=contents, config=types.GenerateContentConfig(**config)
        )
    except errors.APIError as e:
        code = getattr(e, "code", None)
        if code is None:
            raise TransientError(f"gemini {type(e).__name__}: {e}") from e
        raise status_error(f"gemini HTTP {code}: {getattr(e, 'message', e)}", code) from e
    except httpx.HTTPError as e:
        raise TransientError(f"gemini {type(e).__name__}: {e}") from e

    candidate = response.candidates[0] if response.candidates else None
    finish = getattr(candidate, "finish_reason", None)
    usage = response.usage_metadata
    output_tokens = None
    if usage is not None and usage.candidates_token_count is not None:
        output_tokens = usage.candidates_token_count + (usage.thoughts_token_count or 0)
    return RawResponse(
        text=response.text or "",
        model_served=getattr(response, "model_version", None),
        finish_reason=getattr(finish, "name", finish),
        input_tokens=getattr(usage, "prompt_token_count", None),
        output_tokens=output_tokens,
        structured_output=json_schema is not None,
    )


ADAPTERS = {"anthropic": call_anthropic, "nvidia": call_nvidia, "gemini": call_gemini}
