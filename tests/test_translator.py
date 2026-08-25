"""tests/test_translator.py — 翻译器单元测试（mock API）"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mc_translator_mcp.translator import TranslConfig, TranslationCache, Translator


def _make_cfg(**overrides) -> TranslConfig:
    # _env_file=None：测试与本地 .env 隔离，避免受真实配置影响
    base = {"_env_file": None, "dashscope_api_key": "fake-key-for-testing"}
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
            _env_file=None,
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
            _env_file=None,
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


class TestEnglishLeak:
    """译文英文残留检测。"""

    def test_pure_chinese_no_leak(self):
        assert Translator._has_english_leak("能量线缆（硝酸）") is False
        assert Translator._has_english_leak("熔炉发电机") is False

    def test_english_word_leak(self):
        assert Translator._has_english_leak("沥青铀矿 ore（贫矿）") is True
        assert Translator._has_english_leak("powered by OreX") is True

    def test_placeholders_not_flagged(self):
        assert Translator._has_english_leak("还需要 %s 个方块") is False
        assert Translator._has_english_leak("消耗 {0} FE 能量") is False
        assert Translator._has_english_leak("§a把 <powah:wrench> 装上去") is False

    def test_allowlist_abbreviations_not_flagged(self):
        assert Translator._has_english_leak("每秒产出 20 FE/t") is False
        assert Translator._has_english_leak("GUI 已打开") is False
        # 中英相邻时也能正确识别缩写（Python \b 对中文不生效，需特殊处理）
        assert Translator._has_english_leak("升级末影终端GUI来扩容") is False
        assert Translator._has_english_leak("需连接Forge能量（FE）方块") is False
        assert Translator._has_english_leak("shift+右键打开界面") is False


class TestCallLlmFixes:
    """漏译补翻 + 英文残留纠正。"""

    def _make_translator(self, tmp_path: Path) -> Translator:
        return Translator(_make_cfg(), TranslationCache(tmp_path / "c.json"))

    def test_missing_keys_are_retried(self, tmp_path: Path):
        t = self._make_translator(tmp_path)
        batch = {"item.a": "Alpha", "item.b": "Beta", "wiki.c": "<powah:wrench> C"}
        # 第一轮只返回 2 条，漏了 wiki.c；第二轮补回 wiki.c
        with patch.object(t, "_chat", side_effect=[
            "item.a:阿尔法\nitem.b:贝塔",
            "wiki.c:使用 <powah:wrench> 的 C",
        ]) as mock_chat:
            result = t._call_llm("mod", batch)
        assert result == {
            "item.a": "阿尔法",
            "item.b": "贝塔",
            "wiki.c": "使用 <powah:wrench> 的 C",
        }
        assert mock_chat.call_count == 2

    def test_english_leak_is_corrected(self, tmp_path: Path):
        t = self._make_translator(tmp_path)
        batch = {"block.a": "A ore", "block.b": "B"}
        # 第一轮 block.a 残留英文 "ore"；第二轮纠正
        with patch.object(t, "_chat", side_effect=[
            "block.a:沥青铀矿 ore（贫矿）\nblock.b:B 方块",
            "block.a:沥青铀矿（贫矿）",
        ]) as mock_chat:
            result = t._call_llm("mod", batch)
        assert result["block.a"] == "沥青铀矿（贫矿）"
        assert result["block.b"] == "B 方块"
        assert mock_chat.call_count == 2

    def test_no_fix_when_clean(self, tmp_path: Path):
        t = self._make_translator(tmp_path)
        batch = {"item.a": "Alpha"}
        with patch.object(t, "_chat", return_value="item.a:阿尔法") as mock_chat:
            result = t._call_llm("mod", batch)
        assert result == {"item.a": "阿尔法"}
        mock_chat.assert_called_once()


class TestGlossary:
    """模组定制术语表。"""

    def test_load_glossary_flat(self, tmp_path: Path):
        p = tmp_path / "g.json"
        p.write_text('{"Niotic": "钻石", "Blazing": "烈焰"}', encoding="utf-8")
        assert Translator._load_glossary(str(p)) == {"Niotic": "钻石", "Blazing": "烈焰"}

    def test_load_glossary_terms_key(self, tmp_path: Path):
        p = tmp_path / "g.json"
        p.write_text('{"terms": {"Starter": "初级"}}', encoding="utf-8")
        assert Translator._load_glossary(str(p)) == {"Starter": "初级"}

    def test_load_glossary_missing_or_bad(self, tmp_path: Path):
        assert Translator._load_glossary(str(tmp_path / "nope.json")) == {}
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        assert Translator._load_glossary(str(bad)) == {}

    def test_glossary_injected_into_system_prompt(self, tmp_path: Path):
        t = Translator(_make_cfg(glossary_file=str(tmp_path / "g.json")), TranslationCache(tmp_path / "c.json"))
        p = tmp_path / "g.json"
        p.write_text('{"Niotic": "钻石"}', encoding="utf-8")
        t._glossary = Translator._load_glossary(str(p))
        t._system_prompt = t._build_system_prompt(t._glossary)
        assert "Niotic → 钻石" in t._system_prompt

    def test_no_glossary_prompt_unchanged(self, tmp_path: Path):
        t = Translator(
            _make_cfg(glossary_file=str(tmp_path / "none.json")),
            TranslationCache(tmp_path / "c.json"),
        )
        assert t._glossary == {}
        assert t._system_prompt == Translator.SYSTEM_PROMPT
