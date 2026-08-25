"""集成测试：用伪造 jar 验证完整翻译流程（不实际调用 LLM）。"""

import json
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mc_translator_mcp.jar_parser import JARParser
from mc_translator_mcp.lang_parser import LangParser
from mc_translator_mcp.translator import TranslConfig, TranslationCache, Translator
from mc_translator_mcp.pack_builder import PackBuilder


def create_test_jar(path: Path, mod_langs: dict[str, dict]):
    """创建测试 jar，含多个模组的语言文件。"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for modid, entries in mod_langs.items():
            zf.writestr(f"assets/{modid}/lang/en_us.json", json.dumps(entries))
            # 部分模组已有 zh_cn
            if "already_translated" in modid:
                zf.writestr(f"assets/{modid}/lang/zh_cn.json", json.dumps({"item.stick": "已有翻译"}))


def test_full_pipeline():
    """端到端测试：jar解析 → 翻译 → 资源包生成。"""
    tmp = Path(tempfile.mkdtemp())
    jar = tmp / "test_mod.jar"
    
    mod_langs = {
        "zombie_mod": {
            "entity.zombie.basic": "Basic Zombie",
            "entity.zombie.fast": "Fast Zombie",
            "item.zombie tooth": "Zombie Tooth",
            "death.attack.zombie": "%1$s was eaten by a zombie.",
        },
        "train_mod": {
            "block.train.engine": "Train Engine",
            "block.train.carriage": "Train Carriage",
        },
        "already_translated_zombie": {
            "entity.zombie.basic": "Basic Zombie",
        },
    }
    create_test_jar(jar, mod_langs)
    
    cache_path = tmp / "cache.json"
    cfg = TranslConfig(dashscope_api_key="fake-key")
    cache = TranslationCache(cache_path)
    translator = Translator(cfg, cache)
    builder = PackBuilder(tmp / "output", mode="pack")
    
    parser = JARParser(jar)
    lang_files = parser.find_language_files()
    print(f"发现 {len(lang_files)} 个语言文件：")
    for lf in lang_files:
        print(f"  {lf.modid}: {lf.format} ({'已有zh_cn' if lf.has_zh_cn else '需翻译'})")
    
    results = []
    for lf in lang_files:
        if lf.has_zh_cn:
            from mc_translator_mcp.pack_builder import TranslateResult
            results.append(TranslateResult(
                modid=lf.modid, status="skipped", source_format=lf.format,
                entries_total=0, entries_translated=0, output_path="",
                message="已有汉化，跳过"
            ))
            continue
        
        data = parser.read_language_bytes(lf)
        entries = LangParser.parse(data, lf.format)
        
        # mock LLM
        translated = {k: f"[CN]{v}" for k, v in entries.items()}
        result = builder.build(lf, translated, data)
        results.append(result)
    
    print("\n翻译结果：")
    print(builder.summarize(results))
    
    # 验证输出
    for r in results:
        if r.status == "success":
            pack_dir = Path(r.output_path)
            assert pack_dir.exists(), f"输出目录不存在: {pack_dir}"
            zh_file = list(pack_dir.rglob("zh_cn.json"))[0]
            content = json.loads(zh_file.read_text(encoding="utf-8"))
            assert all(v.startswith("[CN]") for v in content.values()), f"翻译结果不符合预期: {content}"
            print(f"✅ {r.modid}: {len(content)} 条翻译已写入 {zh_file}")
    
    print("\n✅ 端到端流程测试通过")


if __name__ == "__main__":
    test_full_pipeline()
