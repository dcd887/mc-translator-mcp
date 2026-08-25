"""tests/test_pack_builder.py — 资源包生成单元测试"""

import json
import tempfile
from pathlib import Path

import pytest

from mc_translator_mcp.jar_parser import JARParser, LanguageFile
from mc_translator_mcp.pack_builder import PackBuilder, TranslateResult


def _make_lf(modid: str = "testmod", fmt: str = "json", has_zh: bool = False) -> LanguageFile:
    path = f"assets/{modid}/lang/en_us.json"
    return LanguageFile(
        modid=modid,
        format=fmt,
        zip_path=path,
        has_zh_cn=has_zh,
        existing_zh_cn_path=path.replace("en_us", "zh_cn") if has_zh else None,
    )


class TestPackBuilder:
    def test_build_resource_pack_json(self, tmp_path: Path):
        builder = PackBuilder(tmp_path / "out", mode="pack")
        lf = _make_lf("mymod")
        translated = {"item.stick": "木棍", "block.grass": "草地方块"}
        original = json.dumps({"item.stick": "Stick", "block.grass": "Grass Block"}).encode()
        result = builder.build(lf, translated, original)
        assert result.status == "success"
        assert result.modid == "mymod"
        pack_dir = tmp_path / "out" / "mymod-zh-cn"
        assert pack_dir.exists()
        zh_file = pack_dir / "assets" / "mymod" / "lang" / "zh_cn.json"
        assert zh_file.exists()
        content = json.loads(zh_file.read_text(encoding="utf-8"))
        assert content == translated
        mcmeta = json.loads((pack_dir / "pack.mcmeta").read_text(encoding="utf-8"))
        assert mcmeta["pack"]["pack_format"] == 15

    def test_build_resource_pack_lang(self, tmp_path: Path):
        builder = PackBuilder(tmp_path / "out", mode="pack")
        lf = _make_lf("mymod2", fmt="lang")
        translated = {"item.stick": "StickCN", "block.dirt": " DirtCN"}
        original = b"item.stick=Stick\nblock.dirt=Dirt\n"
        result = builder.build(lf, translated, original)
        assert result.status == "success"
        zh_file = tmp_path / "out" / "mymod2-zh-cn" / "assets" / "mymod2" / "lang" / "zh_cn.lang"
        assert zh_file.exists()
        assert b"item.stick=StickCN" in zh_file.read_bytes()

    def test_merge_with_existing(self, tmp_path: Path):
        builder = PackBuilder(tmp_path / "out", mode="pack")
        # 创建含已有 zh_cn 的测试 jar
        jar = tmp_path / "merge_test.jar"
        import zipfile
        with zipfile.ZipFile(jar, "w") as zf:
            zf.writestr("assets/merge_test/lang/en_us.json",
                        json.dumps({"item.already": "Already", "item.new": "New"}))
            zf.writestr("assets/merge_test/lang/zh_cn.json",
                        json.dumps({"item.already": "已有翻译"}))
        lf = JARParser(jar).find_language_files()[0]
        translated = {"item.already": "被覆盖了", "item.new": "新翻译"}
        result = builder.build(lf, translated, b"")
        assert result.status == "success"
        zh_file = tmp_path / "out" / "merge_test-zh-cn" / "assets" / "merge_test" / "lang" / "zh_cn.json"
        content = json.loads(zh_file.read_text(encoding="utf-8"))
        # 已有翻译应保留，不被覆盖
        assert content["item.already"] == "已有翻译"
        assert content["item.new"] == "新翻译"

    def test_summarize(self, tmp_path: Path):
        builder = PackBuilder(tmp_path / "out", mode="pack")
        r1 = TranslateResult("ok", "success", "json", 10, 10, "path/ok")
        r2 = TranslateResult("skip", "skipped", "json", 0, 0, "", "已有汉化")
        r3 = TranslateResult("fail", "failed", "json", 0, 0, "", "oops")
        summary = builder.summarize([r1, r2, r3])
        assert "成功：1" in summary
        assert "跳过（已有汉化）：1" in summary
        assert "失败：1" in summary
