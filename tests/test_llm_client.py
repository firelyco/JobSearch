"""Tests for src/llm_client.py provider dispatch.

Verifies call() routes by client shape: OpenAI-compatible (NVIDIA/DeepSeek)
clients have .chat; Anthropic clients have .messages. Also checks <think>
reasoning blocks are stripped and OpenAI token usage is mapped correctly.

Run with: python -m unittest tests.test_llm_client
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import unittest
from tests.test_verify import FakeClient  # Anthropic-shaped fake (has .messages)

from src import llm_client


# --- OpenAI-compatible (NVIDIA) shaped fake ---
class _Msg:
    def __init__(self, content): self.content = content

class _Choice:
    def __init__(self, content): self.message = _Msg(content)

class _Usage:
    def __init__(self, p, c): self.prompt_tokens = p; self.completion_tokens = c

class _OpenAIResp:
    def __init__(self, content, p=10, c=5):
        self.choices = [_Choice(content)]
        self.usage = _Usage(p, c)

class _Completions:
    def __init__(self, responses): self._responses = list(responses); self.calls = []
    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _OpenAIResp(self._responses.pop(0))

class _Chat:
    def __init__(self, responses): self.completions = _Completions(responses)

class FakeOpenAIClient:
    def __init__(self, responses): self.chat = _Chat(responses)
    @property
    def calls(self): return self.chat.completions.calls


class TestProviderDispatch(unittest.TestCase):

    def test_openai_path_returns_content(self):
        client = FakeOpenAIClient(["hello from deepseek"])
        resp = llm_client.call(client, model="deepseek-ai/deepseek-v4-pro",
                               system="sys", user="usr")
        self.assertEqual(resp.text, "hello from deepseek")
        self.assertEqual(resp.model, "deepseek-ai/deepseek-v4-pro")

    def test_openai_maps_token_usage(self):
        client = FakeOpenAIClient(["x"])
        resp = llm_client.call(client, model="m", system="s", user="u")
        self.assertEqual(resp.input_tokens, 10)   # from prompt_tokens
        self.assertEqual(resp.output_tokens, 5)   # from completion_tokens

    def test_openai_sends_system_and_user_messages(self):
        client = FakeOpenAIClient(["x"])
        llm_client.call(client, model="m", system="SYSTEM TEXT", user="USER TEXT")
        msgs = client.calls[0]["messages"]
        self.assertEqual(msgs[0]["role"], "system")
        self.assertEqual(msgs[0]["content"], "SYSTEM TEXT")
        self.assertEqual(msgs[1]["role"], "user")
        self.assertEqual(msgs[1]["content"], "USER TEXT")

    def test_strips_think_block(self):
        client = FakeOpenAIClient(['<think>let me reason {about} this</think>{"recommendation": "strong"}'])
        resp = llm_client.call(client, model="m", system="s", user="u")
        self.assertNotIn("<think>", resp.text)
        self.assertNotIn("let me reason", resp.text)
        self.assertEqual(resp.text, '{"recommendation": "strong"}')

    def test_strips_multiline_think(self):
        client = FakeOpenAIClient(["<think>\nline1\nline2\n</think>\nactual answer"])
        resp = llm_client.call(client, model="m", system="s", user="u")
        self.assertEqual(resp.text, "actual answer")

    def test_openai_ignores_cache_system_flag(self):
        # cache_system is Anthropic-only; OpenAI path must not choke on it
        client = FakeOpenAIClient(["ok"])
        resp = llm_client.call(client, model="m", system="s", user="u", cache_system=True)
        self.assertEqual(resp.text, "ok")
        # system is a plain string message, not a cache-control list
        self.assertIsInstance(client.calls[0]["messages"][0]["content"], str)

    def test_anthropic_path_still_works(self):
        # FakeClient has .messages (no .chat) -> anthropic branch
        import json
        client = FakeClient([json.dumps({"ok": True})])
        resp = llm_client.call(client, model="claude-haiku-4-5", system="s", user="u",
                               cache_system=True)
        self.assertEqual(resp.text, '{"ok": true}')
        # cache_system=True wraps system in a cache-control list on the anthropic path
        self.assertIsInstance(client.calls[0]["system"], list)


class TestAnthropicModelShapes(unittest.TestCase):

    def _call(self, model, **kw):
        client = FakeClient(["ok"])
        llm_client.call(client, model=model, system="s", user="u",
                        max_tokens=400, temperature=0.4, **kw)
        return client.calls[0]

    def test_haiku_keeps_temperature_and_cap(self):
        sent = self._call("claude-haiku-4-5")
        # SDK 1.x rejects a temperature kwarg; it must ride in extra_body.
        self.assertNotIn("temperature", sent)
        self.assertEqual(sent["extra_body"], {"temperature": 0.4})
        self.assertEqual(sent["max_tokens"], 400)
        self.assertNotIn("fallbacks", sent)

    def test_sonnet_5_5_drops_temperature_floors_tokens_and_falls_back(self):
        # Sonnet 5.5 400s on a non-default temperature; thinking needs headroom.
        sent = self._call("claude-sonnet-5-5")
        self.assertNotIn("temperature", sent)
        self.assertNotIn("extra_body", sent)
        self.assertGreaterEqual(sent["max_tokens"], 16000)
        self.assertEqual(sent["fallbacks"], "default")
        self.assertEqual(sent["betas"], ["server-side-fallback-2026-07-01"])


class TestActiveProvider(unittest.TestCase):

    def test_default_is_anthropic(self):
        os.environ.pop("LLM_PROVIDER", None)
        self.assertEqual(llm_client.active_provider(), "anthropic")

    def test_reads_env(self):
        os.environ["LLM_PROVIDER"] = "nvidia"
        try:
            self.assertEqual(llm_client.active_provider(), "nvidia")
        finally:
            os.environ.pop("LLM_PROVIDER", None)


if __name__ == "__main__":
    unittest.main()
