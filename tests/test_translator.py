"""tests/test_translator.py — 翻译器单元测试（mock API）"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mc_translator_mcp.translator import TranslConfig, TranslationCache, Translator


def _make_cfg(**overrides) -> TranslConfig:
    base = {"dashscope_api_key": "fake-key-for-testing"}
    base.update(overrides)
    return TranslConfig(**base)


class TestTranslationCache:
    def test_set_get_roundtrip(self, tmp_path: Path):
        cache = TranslationCache(tmp_path / "cache.json")
        cache.set("mod", "key1", "Original", "翻译结果")
        assert cache.get("mod", "key1", "Original") == "翻译结果"
        # hash 不同原文不会命中缓存
        assert cache.get("mod", "key1", "Different") is None

    def test_flush_persists(self, tmp_path: Path):
        p = tmp_path / "cache.json"
        cache = TranslationCache(p)
        cache.set("mod", "k", "v", "t")
        del cache
        cache2 = TranslationCache(p)
        assert cache2.get("mod", "k", "v") == "t"


class TestTranslator:
    def test_init_requires_api_key(self):
        with pytest.raises(ValueError, match="API Key"):
            Translator(_make_cfg(dashscope_api_key=""), TranslationCache(Path(tempfile.mktemp())))

    @patch("mc_translator_mcp.translator.OpenAI")
    def test_custom_vendor_takes_priority(self, mock_openai_cls, tmp_path: Path):
        """通用自定义供应商优先于 dashscope，并正确使用其 base_url / model。"""
        cfg = TranslConfig(
            translator_api_key="custom-key",
            translator_base_url="https://my.gateway.example/v1",
            translator_model="my-model",
            dashscope_api_key="dash-key",
        )
        client = MagicMock()
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content="item.stick:木棍"))]
        client.chat.completions.create.return_value = resp
        mock_openai_cls.return_value = client

        t = Translator(cfg, TranslationCache(tmp_path / "c.json"))
        result = t.translate_batch("testmod", {"item.stick": "Stick"})
        assert result["item.stick"] == "木棍"

        # 主供应商必须用自定义 base_url（而非 dashscope 的固定地址）
        assert t._primary[1] == "my-model"
        call_kwargs = mock_openai_cls.call_args_list[0].kwargs
        assert call_kwargs["base_url"] == "https://my.gateway.example/v1"
        assert call_kwargs["api_key"] == "custom-key"

    @patch("mc_translator_mcp.translator.OpenAI")
    def test_fallback_used_when_primary_fails(self, mock_openai_cls, tmp_path: Path):
        """主供应商抛异常时，应切换到 DeepSeek fallback。"""
        cfg = TranslConfig(
            translator_api_key="custom-key",
            translator_base_url="https://custom/v1",
            translator_model="custom-model",
            deepseek_api_key="ds-key",
            use_deepseek_fallback=True,
        )
        primary_client = MagicMock()
        primary_client.chat.completions.create.side_effect = Exception("boom")
        fallback_client = MagicMock()
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content="item.stick:木棍"))]
        fallback_client.chat.completions.create.return_value = resp
        mock_openai_cls.side_effect = [primary_client, fallback_client]

        t = Translator(cfg, TranslationCache(tmp_path / "c.json"))
        result = t.translate_batch("testmod", {"item.stick": "Stick"})
        assert result["item.stick"] == "木棍"
        fallback_client.chat.completions.create.assert_called_once()

    def test_translate_batch_empty(self, tmp_path: Path):
        t = Translator(_make_cfg(), TranslationCache(tmp_path / "c.json"))
        assert t.translate_batch("mod", {}) == {}

    @patch("mc_translator_mcp.translator.OpenAI")
    def test_translate_batch_single(self, mock_openai_cls, tmp_path: Path):
        client = MagicMock()
        resp = MagicMock()
        resp.choices = [MagicMock(message=MagicMock(content="item.stick:木棍"))]
        client.chat.completions.create.return_value = resp
        mock_openai_cls.return_value = client

        t = Translator(_make_cfg(), TranslationCache(tmp_path / "c.json"))
        entries = {"item.stick": "Stick", "item.iron_ingot": "Iron Ingot"}
        result = t.translate_batch("testmod", entries)
        assert "item.stick" in result
        assert result["item.stick"] == "木棍"

    @patch("mc_translator_mcp.translator.OpenAI")
    def test_translation_cached_on_repeated_call(self, mock_openai_cls, tmp_path: Path):
        client = MagicMock()
        mock_openai_cls.return_value = client

        t = Translator(_make_cfg(), TranslationCache(tmp_path / "c.json"))
        entries = {"item.stick": "Stick"}
        # 首次调用（会走 LLM）
        with patch.object(t, "_call_llm", return_value={"item.stick": "木棍"}):
            t.translate_batch("mod", entries)
        # 第二次相同原文不应再调 LLM
        with patch.object(t, "_call_llm") as mock_llm:
            t.translate_batch("mod", entries)
        mock_llm.assert_not_called()

    @patch("mc_translator_mcp.translator.OpenAI")
    def test_batch_size_respected(self, mock_openai_cls, tmp_path: Path):
        client = MagicMock()
        mock_openai_cls.return_value = client

        t = Translator(_make_cfg(batch_size=2), TranslationCache(tmp_path / "c.json"))
        entries = {f"key{i}": f"Value{i}" for i in range(5)}
        with patch.object(t, "_call_llm", side_effect=lambda mod, b: {k: f"TR_{k}" for k in b}) as mock_llm:
            t.translate_batch("mod", entries)
        # 5 条按 batch_size=2 应调用 3 次
        assert mock_llm.call_count == 3
