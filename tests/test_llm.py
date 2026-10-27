"""Unit tests for src/llm: retries, Retry-After, cache keys, key masking. No real network calls.

Run from the repo root:  python -m unittest discover -s tests -t .
"""

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

import httpx

from src.llm import core, providers
from src.llm.config import LLMConfig, RetryConfig, RoleConfig

FAKE_KEY = "nvapi-UNITTEST-SECRET-0123456789"
SCHEMA = {
    "type": "object",
    "properties": {"label": {"type": "string", "enum": ["dung", "sai"]}, "reason": {"type": "string"}},
    "required": ["label", "reason"],
}
URL = "https://nim.example/v1/chat/completions"


def make_config() -> LLMConfig:
    role = RoleConfig(name="judge", provider="nvidia", model="test/model", max_tokens=64, timeout_s=5,
                      base_url="https://nim.example/v1")
    return LLMConfig(retry=RetryConfig(max_retries=3, base_delay_s=2.0, max_delay_s=60.0), roles={"judge": role})


def http_response(status: int, body: dict | None = None, headers: dict | None = None, text: str | None = None):
    request = httpx.Request("POST", URL)
    if text is not None:
        return httpx.Response(status, text=text, headers=headers, request=request)
    return httpx.Response(status, json=body or {}, headers=headers, request=request)


def ok_body(content: str = '{"label": "dung", "reason": "ok"}') -> dict:
    return {
        "model": "test/model",
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7},
    }


class LLMTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patches = [
            mock.patch.object(core, "CACHE_DIR", Path(self.tmp.name)),
            mock.patch.object(core, "load_config", make_config),
            mock.patch.object(core, "load_dotenv", lambda *a, **k: None),
            mock.patch.dict("os.environ", {"NVIDIA_API_KEY": FAKE_KEY}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        self.sleep = mock.patch.object(core, "_sleep").start()
        self.addCleanup(mock.patch.stopall)

    def call(self, run: int = 0, use_cache: bool = True, schema: dict | None = SCHEMA):
        return core.complete("judge", "system", [{"role": "user", "content": "hi"}],
                             json_schema=schema, run=run, use_cache=use_cache)


class RetryTests(LLMTestCase):
    def test_429_then_success(self):
        with mock.patch.object(providers.httpx, "post",
                               side_effect=[http_response(429), http_response(200, ok_body())]) as post:
            out = io.StringIO()
            with redirect_stdout(out):
                result = self.call()
        self.assertEqual(post.call_count, 2)
        self.assertEqual(result.attempts, 2)
        self.assertEqual(result.parsed, {"label": "dung", "reason": "ok"})
        self.assertEqual(self.sleep.call_count, 1)
        wait = self.sleep.call_args.args[0]
        self.assertTrue(1.0 <= wait <= 2.0, wait)  # first retry: equal jitter on a 2 s step
        self.assertIn("nvidia HTTP 429", out.getvalue())

    def test_401_is_not_retried(self):
        with mock.patch.object(providers.httpx, "post", return_value=http_response(401, {"error": "bad key"})) as post:
            with self.assertRaises(core.LLMError) as ctx:
                self.call()
        self.assertEqual(post.call_count, 1)
        self.assertEqual(ctx.exception.http_code, 401)
        self.assertEqual(ctx.exception.attempts, 1)
        self.sleep.assert_not_called()

    def test_retry_after_header_is_honoured(self):
        responses = [http_response(503, headers={"Retry-After": "7"}), http_response(200, ok_body())]
        with mock.patch.object(providers.httpx, "post", side_effect=responses):
            with redirect_stdout(io.StringIO()):
                self.call()
        self.sleep.assert_called_once_with(7.0)

    def test_gives_up_after_max_retries(self):
        with mock.patch.object(providers.httpx, "post", return_value=http_response(504)) as post:
            with redirect_stdout(io.StringIO()), self.assertRaises(core.LLMError) as ctx:
                self.call()
        self.assertEqual(post.call_count, 4)  # 1 attempt + max_retries=3
        self.assertEqual(ctx.exception.http_code, 504)
        waits = [c.args[0] for c in self.sleep.call_args_list]
        self.assertEqual(len(waits), 3)
        self.assertTrue(all(lo <= w <= hi for w, (lo, hi) in zip(waits, [(1, 2), (2, 4), (4, 8)])), waits)

    def test_timeout_is_retried(self):
        side_effect = [httpx.ReadTimeout("slow", request=httpx.Request("POST", URL)), http_response(200, ok_body())]
        with mock.patch.object(providers.httpx, "post", side_effect=side_effect):
            with redirect_stdout(io.StringIO()):
                result = self.call()
        self.assertEqual(result.attempts, 2)

    def test_structured_output_rejected_falls_back_to_prompt(self):
        responses = [http_response(400, {"error": "response_format unsupported"}), http_response(200, ok_body())]
        with mock.patch.object(providers.httpx, "post", side_effect=responses) as post:
            result = self.call()
        self.assertFalse(result.structured_output)
        second_body = post.call_args_list[1].kwargs["json"]
        self.assertNotIn("response_format", second_body)
        self.assertIn("JSON Schema", second_body["messages"][0]["content"])
        self.sleep.assert_not_called()  # the fallback is not a retry

    def test_invalid_json_gives_parsed_none(self):
        with mock.patch.object(providers.httpx, "post", return_value=http_response(200, ok_body('{"label": "maybe"}'))):
            result = self.call()
        self.assertIsNone(result.parsed)


class CacheTests(LLMTestCase):
    def test_cache_hit_skips_the_call(self):
        with mock.patch.object(providers.httpx, "post", return_value=http_response(200, ok_body())) as post:
            first = self.call()
            second = self.call()
        self.assertEqual(post.call_count, 1)
        self.assertFalse(first.from_cache)
        self.assertTrue(second.from_cache)
        self.assertEqual(second.text, first.text)
        self.assertEqual(second.called_at, first.called_at)

    def test_run_is_part_of_the_key(self):
        with mock.patch.object(providers.httpx, "post", return_value=http_response(200, ok_body())) as post:
            r0 = self.call(run=0)
            r1 = self.call(run=1)
        self.assertEqual(post.call_count, 2)
        self.assertFalse(r1.from_cache)
        self.assertEqual((r0.run, r1.run), (0, 1))
        self.assertEqual(len(list((Path(self.tmp.name) / "judge").glob("*.json"))), 2)

    def test_errors_are_not_cached(self):
        with mock.patch.object(providers.httpx, "post", return_value=http_response(401)):
            with self.assertRaises(core.LLMError):
                self.call()
        self.assertFalse((Path(self.tmp.name) / "judge").exists())


class TruncationAndQuotaTests(LLMTestCase):
    def test_truncated_reply_is_returned_but_not_cached(self):
        body = ok_body()
        body["choices"][0]["finish_reason"] = "length"
        with mock.patch.object(providers.httpx, "post", return_value=http_response(200, body)) as post:
            first = self.call()
            second = self.call()
        self.assertTrue(first.truncated)
        self.assertFalse(second.from_cache)
        self.assertEqual(post.call_count, 2)
        self.assertFalse((Path(self.tmp.name) / "judge").exists())

    def test_retry_after_above_cap_raises_without_sleeping(self):
        with mock.patch.object(providers.httpx, "post", return_value=http_response(429, headers={"Retry-After": "300"})):
            with self.assertRaises(core.LLMError) as ctx:
                self.call()
        self.assertIn("cap", str(ctx.exception))
        self.assertEqual(ctx.exception.http_code, 429)
        self.sleep.assert_not_called()

    def test_params_override_changes_cache_key(self):
        with mock.patch.object(providers.httpx, "post", return_value=http_response(200, ok_body())) as post:
            base = core.complete("judge", "s", [{"role": "user", "content": "hi"}])
            low = core.complete("judge", "s", [{"role": "user", "content": "hi"}], params_override={"x": 1})
        self.assertEqual(post.call_count, 2)
        self.assertFalse(low.from_cache)
        self.assertEqual((base.params, low.params), ({}, {"x": 1}))


class RoleConfigTests(unittest.TestCase):
    """Per-role settings in the real configs/llm.toml."""

    def test_judge_fallback_has_its_own_retries_and_timeout(self):
        from src.llm.config import load_config

        config = load_config()
        fallback = config.role("judge_fallback")
        self.assertEqual((fallback.max_retries, fallback.timeout_s), (2, 60.0))
        for name in ("answer", "extract", "judge"):
            self.assertIsNone(config.role(name).max_retries, name)  # global [retry].max_retries applies
        self.assertEqual(config.retry.max_retries, 6)
        self.assertEqual((config.role("answer").timeout_s, config.role("judge").timeout_s), (120.0, 90.0))

    def test_role_max_retries_overrides_global(self):
        cfg = make_config()
        role = RoleConfig(name="judge", provider="nvidia", model="test/model", max_tokens=64, timeout_s=5,
                          base_url="https://nim.example/v1", max_retries=1)
        custom = LLMConfig(retry=cfg.retry, roles={"judge": role})
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        with mock.patch.object(core, "CACHE_DIR", Path(tmp.name)), mock.patch.object(core, "load_config", lambda: custom), \
                mock.patch.object(core, "load_dotenv", lambda *a, **k: None), \
                mock.patch.dict("os.environ", {"NVIDIA_API_KEY": FAKE_KEY}), mock.patch.object(core, "_sleep"), \
                mock.patch.object(providers.httpx, "post", return_value=http_response(503)) as post, \
                redirect_stdout(io.StringIO()):
            with self.assertRaises(core.LLMError):
                core.complete("judge", "s", [{"role": "user", "content": "hi"}])
        self.assertEqual(post.call_count, 2)  # 1 attempt + max_retries=1 (global is 3 in make_config)


class AnthropicStructuredFallbackTests(unittest.TestCase):
    def test_400_on_schema_falls_back_to_prompt_json(self):
        import anthropic
        import httpx2
        from types import SimpleNamespace

        cfg = RoleConfig(name="answer", provider="anthropic", model="claude-haiku-5-5", max_tokens=64, timeout_s=5,
                         params={"effort": "low"})
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        rejected = anthropic.APIStatusError("bad schema", response=httpx2.Response(400, request=request), body=None)
        ok = SimpleNamespace(content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text='{"a": 1}')],
                             model="claude-haiku-5-5", stop_reason="end_turn",
                             usage=SimpleNamespace(input_tokens=5, output_tokens=3))
        with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}), \
                mock.patch.object(anthropic.resources.Messages, "create", side_effect=[rejected, ok]) as create:
            raw = providers.call_anthropic(cfg, "sys", [{"role": "user", "content": "hi"}], {"type": "object"})
        self.assertFalse(raw.structured_output)
        self.assertEqual(raw.text, '{"a": 1}')
        first, second = create.call_args_list
        self.assertEqual(first.kwargs["output_config"]["format"]["type"], "json_schema")
        self.assertNotIn("format", second.kwargs["output_config"])
        self.assertEqual(second.kwargs["output_config"]["effort"], "low")
        self.assertIn("JSON Schema", second.kwargs["system"])


class SecretTests(LLMTestCase):
    def test_key_never_printed_or_raised(self):
        # The server echoes the key back in its error body and in a retryable error first
        leaky = f"invalid token {FAKE_KEY}"
        responses = [http_response(503, text=leaky), http_response(403, text=leaky)]
        out = io.StringIO()
        with mock.patch.object(providers.httpx, "post", side_effect=responses), redirect_stdout(out):
            with self.assertRaises(core.LLMError) as ctx:
                self.call()
        self.assertNotIn(FAKE_KEY, str(ctx.exception))
        self.assertIn("***", str(ctx.exception))
        self.assertNotIn(FAKE_KEY, out.getvalue())

    def test_key_not_in_cache_file(self):
        with mock.patch.object(providers.httpx, "post", return_value=http_response(200, ok_body())):
            self.call()
        for path in Path(self.tmp.name).rglob("*.json"):
            self.assertNotIn(FAKE_KEY, path.read_text(encoding="utf-8"))
            self.assertIn("called_at", json.loads(path.read_text(encoding="utf-8"))["result"])


class AnthropicErrorMappingTests(unittest.TestCase):
    """529 overloaded and 429 are transient; 400 is permanent (no network: client call is mocked)."""

    def test_status_mapping(self):
        import anthropic
        import httpx2

        cfg = RoleConfig(name="answer", provider="anthropic", model="claude-haiku-5-5", max_tokens=64, timeout_s=5)
        request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
        cases = [(529, providers.TransientError), (429, providers.TransientError), (400, providers.PermanentError)]
        for status, expected in cases:
            response = httpx2.Response(status, headers={"retry-after": "3"}, request=request)
            error = anthropic.APIStatusError(f"status {status}", response=response, body=None)
            with self.subTest(status=status), \
                    mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test"}), \
                    mock.patch.object(anthropic.resources.Messages, "create", side_effect=error):
                with self.assertRaises(expected) as ctx:
                    providers.call_anthropic(cfg, "", [{"role": "user", "content": "hi"}], None)
                self.assertEqual(ctx.exception.http_code, status)
                self.assertEqual(ctx.exception.retry_after_s, 3.0)


if __name__ == "__main__":
    unittest.main()
