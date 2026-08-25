"""tests/test_lang_parser.py — 语言文件解析单元测试"""

import json
import pytest

from mc_translator_mcp.lang_parser import LangParser, LangParseError


class TestLangParser:
    def test_parse_json_valid(self):
        data = json.dumps({"item.stick": "Stick", "block.grass": "Grass Block"}).encode()
        result = LangParser.parse_json(data)
        assert result == {"item.stick": "Stick", "block.grass": "Grass Block"}

    def test_parse_json_invalid_root(self):
        with pytest.raises(LangParseError):
            LangParser.parse_json(b"[1, 2]")

    def test_parse_json_invalid_json(self):
        with pytest.raises(LangParseError):
            LangParser.parse_json(b"{not valid json")

    def test_parse_lang_valid(self):
        data = b"item.stick=Stick\n# comment\nblock.grass=Grass Block\n"
        result = LangParser.parse_lang(data)
        assert result == {"item.stick": "Stick", "block.grass": "Grass Block"}

    def test_parse_lang_skips_comments_and_blanks(self):
        data = b"key1=val1\n\n# comment\nkey2=val2\n"
        result = LangParser.parse_lang(data)
        assert list(result.keys()) == ["key1", "key2"]

    def test_parse_unknown_format_raises(self):
        with pytest.raises(ValueError):
            LangParser.parse(b"{}", "yaml")

    def test_serialize_json_roundtrip(self):
        entries = {"item.stick": "Stick", "block.grass": " Grass Block "}
        serialized = LangParser.serialize_json(entries)
        restored = json.loads(serialized.decode("utf-8"))
        assert restored == entries

    def test_serialize_lang_roundtrip(self):
        entries = {"item.stick": "Stick", "block.grass": "Grass Block"}
        serialized = LangParser.serialize_lang(entries)
        restored = LangParser.parse_lang(serialized)
        assert restored == entries

    def test_merge_with_existing(self):
        existing = b'{"item.stick": "\u68D2\u5B50", "item.iron_ingot": "Tiebar"}'
        new = {"item.stick": "New Stick", "item.gold_ingot": "Gold Bar"}
        result = LangParser.merge_with_existing(new, existing, "json")
        # 已有的 item.stick 应保留旧值（不覆盖），新增的 gold_ingot 加入
        assert result["item.stick"] == "棒子"
        assert result["item.iron_ingot"] == "Tiebar"
        assert result["item.gold_ingot"] == "Gold Bar"

    def test_merge_none_existing_returns_new(self):
        new = {"a": "A"}
        result = LangParser.merge_with_existing(new, None, "json")
        assert result == {"a": "A"}
