"""tests/test_mcp_server.py — MCP服务器功能测试（mock API）"""

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mc_translator_mcp.jar_parser import JARParser
from mc_translator_mcp.lang_parser import LangParser
from mc_translator_mcp.translator import TranslConfig, TranslationCache, Translator
from mc_translator_mcp.pack_builder import PackBuilder


def test_translate_single_mod():
    """测试单个模组的翻译流程（mock LLM）。"""
    import zipfile
    
    # 创建测试jar
    tmp = Path(tempfile.mkdtemp()) / "testmod.jar"
    en_us = {"item.stick": "Stick", "block.grass": "Grass Block", "effect.infection": "Infection"}
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("assets/testmod/lang/en_us.json", json.dumps(en_us))
    
    # mock LLM
    cfg = TranslConfig(dashscope_api_key="fake-key")
    cache = TranslationCache(Path(tempfile.mktemp()) / "cache.json")
    translator = Translator(cfg, cache)
    
    with patch.object(translator, "_call_llm", return_value={"item.stick": "木棍", "block.grass": "草地", "effect.infection": "感染"}):
        parser = JARParser(tmp)
        files = parser.find_language_files()
        builder = PackBuilder(Path(tempfile.mkdtemp()) / "out", mode="pack")
        
        results = []
        for lf in files:
            data = parser.read_language_bytes(lf)
            entries = LangParser.parse(data, lf.format)
            translated = translator.translate_batch(lf.modid, entries)
            result = builder.build(lf, translated, data)
            results.append(result)
        
        assert len(results) == 1
        assert results[0].status == "success"
        assert results[0].modid == "testmod"
        print(f"✅ test_translate_single_mod: {results[0].output_path}")


def test_skip_existing_zh_cn():
    """测试已有zh_cn时跳过。"""
    import zipfile
    
    tmp = Path(tempfile.mkdtemp()) / "existing_zhcn.jar"
    en_us = {"item.stick": "Stick"}
    zh_cn = {"item.stick": "木棍"}
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("assets/exmod/lang/en_us.json", json.dumps(en_us))
        zf.writestr("assets/exmod/lang/zh_cn.json", json.dumps(zh_cn))
    
    parser = JARParser(tmp)
    files = parser.find_language_files()
    assert files[0].has_zh_cn is True
    assert files[0].existing_zh_cn_path == "assets/exmod/lang/zh_cn.json"
    print("✅ test_skip_existing_zh_cn: has_zh_cn=True")


def test_cache_prevents_retranslation():
    """测试缓存防止重复翻译。"""
    tmp = Path(tempfile.mkdtemp()) / "cache_test.jar"
    en_us = {"item.new": "New Item"}
    import zipfile
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("assets/cmod/lang/en_us.json", json.dumps(en_us))
    
    cache_path = Path(tempfile.mktemp()) / "cache.json"
    cache = TranslationCache(cache_path)
    cfg = TranslConfig(dashscope_api_key="fake-key")
    translator = Translator(cfg, cache)
    
    # 首次翻译
    parser = JARParser(tmp)
    lf = parser.find_language_files()[0]
    data = parser.read_language_bytes(lf)
    entries = LangParser.parse(data, lf.format)
    
    call_count = [0]
    def mock_llm(modid, batch):
        call_count[0] += 1
        return {k: f"TR_{k}" for k in batch}
    
    with patch.object(translator, "_call_llm", side_effect=mock_llm):
        t1 = translator.translate_batch("cmod", entries)
        t2 = translator.translate_batch("cmod", entries)
    
    assert call_count[0] == 1, f"Expected 1 LLM call, got {call_count[0]}"
    assert t1 == t2
    print(f"✅ test_cache_prevents_retranslation: LLM调用次数={call_count[0]}")


if __name__ == "__main__":
    test_translate_single_mod()
    test_skip_existing_zh_cn()
    test_cache_prevents_retranslation()
    print("\n所有MCP服务器测试通过 ✅")
