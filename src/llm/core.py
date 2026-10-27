"""Single entry point for every LLM call: complete(role, system, messages, ...).

Reads the role's provider/model from configs/llm.toml, retries transient failures with exponential
backoff and jitter (honouring Retry-After), caches successful results on disk, and returns an LLMResult
carrying the metadata that must go into every result file (PLAN section 4, decision #2).
"""

import dataclasses
import hashlib
import json
import os
import random
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

from .config import REPO_ROOT, RetryConfig, RoleConfig, load_config
from .providers import ADAPTERS, API_KEY_VARS, PermanentError, ProviderError, RawResponse, TransientError

CACHE_DIR = REPO_ROOT / "data" / "cache" / "llm"
# Vietnam has no daylight saving time, so a fixed offset is exact (zoneinfo needs tzdata on Windows)
VN_TZ = timezone(timedelta(hours=7))
CACHE_FORMAT = 1  # bump when the cached record layout changes
# finish_reason values meaning the reply hit the output cap (Anthropic, OpenAI-compatible NIM, Gemini)
TRUNCATED_REASONS = frozenset({"max_tokens", "length", "MAX_TOKENS"})


@dataclass
class LLMResult:
    text: str
    parsed: dict | None
    role: str
    provider: str
    model_requested: str
    model_served: str | None
    run: int
    finish_reason: str | None
    input_tokens: int | None
    output_tokens: int | None
    latency_s: float
    attempts: int
    called_at: str
    from_cache: bool
    structured_output: bool
    truncated: bool = False  # hit the output cap; returned but never cached
    params: dict | None = None  # provider params actually used (config merged with params_override)

    def to_dict(self) -> dict:
        return asdict(self)


class LLMError(Exception):
    """A call failed for good: permanent error, or transient errors past max_retries."""

    def __init__(self, message: str, role: str, provider: str, http_code: int | None, attempts: int):
        super().__init__(message)
        self.role = role
        self.provider = provider
        self.http_code = http_code
        self.attempts = attempts


def mask_secrets(text: str) -> str:
    """Replace any API key value found in text with ***."""
    for var in API_KEY_VARS.values():
        value = os.getenv(var)
        if value:
            text = text.replace(value, "***")
    return text


def _sleep(seconds: float) -> None:
    """Indirection so tests can patch the wait without patching time.sleep globally."""
    time.sleep(seconds)


def backoff_delay(retry_index: int, retry: RetryConfig, retry_after_s: float | None) -> float:
    """Seconds to wait before retry number retry_index (1-based)."""
    if retry_after_s is not None:
        return retry_after_s
    ceiling = min(retry.max_delay_s, retry.base_delay_s * 2 ** (retry_index - 1))
    # Equal jitter: at least half the exponential step, so waits keep growing
    return ceiling / 2 + random.uniform(0, ceiling / 2)


def cache_key(cfg: RoleConfig, system: str, messages: list[dict], json_schema: dict | None, run: int) -> str:
    """sha256 over everything that can change the output, including run (PLAN #2: 3 runs per config)."""
    payload = {
        "provider": cfg.provider,
        "model": cfg.model,
        "base_url": cfg.base_url,
        "max_tokens": cfg.max_tokens,
        "params": cfg.params,
        "system": system,
        "messages": messages,
        "json_schema": json_schema,
        "run": run,
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def parse_json(text: str, schema: dict) -> dict | None:
    """Return the JSON object in text if it fits the schema's top level, else None (never raises).

    Checks: an object, required keys present, enum values allowed. Tolerates code fences or stray prose.
    """
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if any(key not in data for key in schema.get("required", [])):
        return None
    for key, prop in (schema.get("properties") or {}).items():
        if key in data and "enum" in prop and data[key] not in prop["enum"]:
            return None
    return data


def _call_with_retry(cfg: RoleConfig, retry: RetryConfig, system: str, messages: list[dict],
                     json_schema: dict | None) -> tuple[RawResponse, int]:
    adapter = ADAPTERS[cfg.provider]
    attempt = 0
    while True:
        attempt += 1
        try:
            return adapter(cfg, system, messages, json_schema), attempt
        except PermanentError as e:
            raise LLMError(mask_secrets(str(e)), cfg.name, cfg.provider, e.http_code, attempt) from None
        except TransientError as e:
            if e.retry_after_s is not None and e.retry_after_s > retry.retry_after_cap_s:
                raise LLMError(
                    mask_secrets(f"server asked to wait {e.retry_after_s:.0f}s (> cap {retry.retry_after_cap_s:.0f}s), "
                                 f"quota likely exhausted: {e}"), cfg.name, cfg.provider, e.http_code, attempt
                ) from None
            if attempt > retry.max_retries:
                raise LLMError(
                    mask_secrets(f"gave up after {attempt} attempts: {e}"), cfg.name, cfg.provider, e.http_code, attempt
                ) from None
            wait = backoff_delay(attempt, retry, e.retry_after_s)
            what = f"HTTP {e.http_code}" if e.http_code else "timeout/network"
            print(f"[llm retry] {cfg.provider} {what} - retry {attempt}/{retry.max_retries} in {wait:.1f}s")
            _sleep(wait)


def complete(role: str, system: str, messages: list[dict], json_schema: dict | None = None,
             run: int = 0, use_cache: bool = True, params_override: dict | None = None) -> LLMResult:
    """Call the model configured for role and return the reply with its metadata.

    messages: [{"role": "user" | "assistant", "content": str}, ...]; system is passed separately.
    json_schema: JSON Schema of the expected object; parsed is None when the reply does not fit.
    run: repetition index (0, 1, 2 for the three runs per configuration); part of the cache key.
    params_override: provider params replacing the role's config for this call (e.g. {"effort": "low"});
    they are part of the cache key.
    """
    load_dotenv(REPO_ROOT / ".env")
    config = load_config()
    cfg = config.role(role)
    if params_override:
        cfg = dataclasses.replace(cfg, params={**cfg.params, **params_override})
    key = cache_key(cfg, system, messages, json_schema, run)
    cache_file = CACHE_DIR / role / f"{key}.json"

    if use_cache and cache_file.exists():
        record = json.loads(cache_file.read_text(encoding="utf-8"))
        result = LLMResult(**record["result"])
        result.from_cache = True
        return result

    retry = config.retry
    if cfg.max_retries is not None:
        retry = dataclasses.replace(retry, max_retries=cfg.max_retries)
    started = datetime.now(VN_TZ)
    t0 = time.perf_counter()
    raw, attempts = _call_with_retry(cfg, retry, system, messages, json_schema)
    result = LLMResult(
        text=raw.text,
        parsed=parse_json(raw.text, json_schema) if json_schema is not None else None,
        role=role,
        provider=cfg.provider,
        model_requested=cfg.model,
        model_served=raw.model_served,
        run=run,
        finish_reason=raw.finish_reason,
        input_tokens=raw.input_tokens,
        output_tokens=raw.output_tokens,
        latency_s=round(time.perf_counter() - t0, 3),
        attempts=attempts,
        called_at=started.isoformat(timespec="seconds"),
        from_cache=False,
        structured_output=raw.structured_output,
        truncated=raw.finish_reason in TRUNCATED_REASONS,
        params=dict(cfg.params),
    )

    # Errors raise above and are never cached; refusals and truncated replies are not cached either
    if use_cache and result.finish_reason != "refusal" and not result.truncated:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        record = {"format": CACHE_FORMAT, "key": key, "result": result.to_dict()}
        cache_file.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
