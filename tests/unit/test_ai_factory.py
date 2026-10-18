"""Unit tests for OpenAI-compatible adapters and AI factory (R20, R21, R64, R65)."""

import os
import unittest
from unittest.mock import patch

import httpx

from waifubot.adapters.ai_factory import create_chat_model, create_enhancer
from waifubot.adapters.chat.anthropic import AnthropicChatModel
from waifubot.adapters.chat.mock import MockChatModel
from waifubot.adapters.chat.openai_compatible import OpenAICompatibleChatModel
from waifubot.adapters.enhance.anthropic import AnthropicEnhancer
from waifubot.adapters.enhance.mock import MockEnhancer
from waifubot.adapters.enhance.openai_compatible import OpenAICompatibleEnhancer


class TestAIFactoryAndOpenAICompatible(unittest.IsolatedAsyncioTestCase):
    def test_factory_detects_providers_from_env(self) -> None:
        """create_chat_model and create_enhancer detect provider from environment variables."""
        # 1. No env -> defaults to mock
        with patch.dict(os.environ, {}, clear=True):
            chat = create_chat_model()
            enhancer = create_enhancer()
            self.assertIsInstance(chat, MockChatModel)
            self.assertIsInstance(enhancer, MockEnhancer)

        # 2. OPENROUTER_API_KEY -> openrouter
        with patch.dict(
            os.environ,
            {
                "OPENROUTER_API_KEY": "sk-or-test",
                "AI_CHAT_MODEL": "google/gemini-2.0-flash-001",
            },
            clear=True,
        ):
            chat = create_chat_model()
            self.assertIsInstance(chat, OpenAICompatibleChatModel)
            self.assertEqual(chat.api_key, "sk-or-test")
            self.assertEqual(chat.base_url, "https://openrouter.ai/api/v1")
            self.assertEqual(chat.model, "google/gemini-2.0-flash-001")

        # 3. GEMINI_API_KEY -> google
        with patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY": "test-gemini-key",
                "AI_CHAT_MODEL": "gemini-2.0-flash",
            },
            clear=True,
        ):
            chat = create_chat_model()
            enhancer = create_enhancer()
            self.assertIsInstance(chat, OpenAICompatibleChatModel)
            self.assertIsInstance(enhancer, OpenAICompatibleEnhancer)
            self.assertIn("generativelanguage.googleapis.com", chat.base_url)
            self.assertEqual(chat.model, "gemini-2.0-flash")

        # 4. ANTHROPIC_API_KEY -> anthropic
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-ant-test"}, clear=True):
            chat = create_chat_model()
            enhancer = create_enhancer()
            self.assertIsInstance(chat, AnthropicChatModel)
            self.assertIsInstance(enhancer, AnthropicEnhancer)

        # 5. Explicit provider override
        chat_custom = create_chat_model(
            provider="openai",
            api_key="sk-openai-custom",
            base_url="https://api.openai.com/v1",
            model="gpt-4o-mini",
        )
        self.assertIsInstance(chat_custom, OpenAICompatibleChatModel)
        self.assertEqual(chat_custom.model, "gpt-4o-mini")

    async def test_openai_compatible_chat_complete(self) -> None:
        """OpenAICompatibleChatModel sends standard payload and extracts completion and usage."""

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/v1/chat/completions")
            self.assertEqual(request.headers.get("authorization"), "Bearer sk-test-key")
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "پاسخ مدل هوش مصنوعی"}}],
                    "usage": {
                        "prompt_tokens": 42,
                        "completion_tokens": 15,
                        "prompt_tokens_details": {"cached_tokens": 10},
                    },
                },
            )

        transport = httpx.MockTransport(handler)
        model = OpenAICompatibleChatModel(
            api_key="sk-test-key",
            base_url="https://mock-provider.com/api/v1",
            model="test-model",
        )

        with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=transport)):
            reply, usage = await model.complete(
                system_prefix="System instructions",
                messages=[{"role": "user", "content": "سلام"}],
            )
            self.assertEqual(reply, "پاسخ مدل هوش مصنوعی")
            self.assertEqual(usage["input_tokens"], 42)
            self.assertEqual(usage["output_tokens"], 15)
            self.assertEqual(usage["cached_tokens"], 10)

    async def test_openai_compatible_compact_memory(self) -> None:
        """OpenAICompatibleChatModel compacts memory and returns structured diff."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": '{"summary": "خلاصه جدید", "facts_add": ["فکت جدید"], "facts_remove": []}'
                            }
                        }
                    ],
                },
            )

        transport = httpx.MockTransport(handler)
        model = OpenAICompatibleChatModel(
            api_key="sk-test-key",
            base_url="https://mock-provider.com/api/v1",
        )

        with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=transport)):
            result = await model.compact_memory(
                old_summary="قدیمی",
                durable_facts=[],
                leaving_turns=[{"role": "user", "content": "موضوع"}],
            )
            self.assertEqual(result["summary"], "خلاصه جدید")
            self.assertEqual(result["facts_add"], ["فکت جدید"])

    async def test_openai_compatible_enhancer(self) -> None:
        """OpenAICompatibleEnhancer enhances prompt via chat completions endpoint."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": "masterpiece, best quality, 1girl, smiling, anime art"
                            }
                        }
                    ]
                },
            )

        transport = httpx.MockTransport(handler)
        enhancer = OpenAICompatibleEnhancer(
            api_key="sk-test-key",
            base_url="https://mock-provider.com/api/v1",
            model="google/gemini-2.0-flash-001",
        )

        with patch("httpx.AsyncClient", return_value=httpx.AsyncClient(transport=transport)):
            res = await enhancer.enhance_prompt(scene="girl smiling")
            self.assertEqual(res, "masterpiece, best quality, 1girl, smiling, anime art")


if __name__ == "__main__":
    unittest.main()
