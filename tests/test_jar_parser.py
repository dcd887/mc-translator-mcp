"""tests/test_jar_parser.py — JAR 解析单元测试"""

import json
import tempfile
import zipfile
from pathlib import Path

import pytest

from mc_translator_mcp.jar_parser import JARParser, LanguageFile


def _make_test_jar(paths: dict[str, bytes]) -> Path:
    """在临时目录里生成一个测试用 jar（实际是 zip）。"""
    tmp = Path(tempfile.mkdtemp()) / "test.jar"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for p, data in paths.items():
            zf.writestr(p, data)
    return tmp


class TestJARParser:
    def test_find_en_us_json(self):
        jar = _make_test_jar({
            "assets/mymod/lang/en_us.json": json.dumps({"item.stick": "Stick"}),
        })
        parser = JARParser(jar)
        files = parser.find_language_files()
        assert len(files) == 1
        f = files[0]
        assert f.modid == "mymod"
        assert f.format == "json"
        assert f.has_zh_cn is False
        assert f._jar_path == str(jar.resolve())

    def test_skips_when_zh_cn_exists(self):
        jar = _make_test_jar({
            "assets/mymod/lang/en_us.json": json.dumps({"a": "1"}),
            "assets/mymod/lang/zh_cn.json": json.dumps({"a": "一"}),
        })
        parser = JARParser(jar)
        files = parser.find_language_files()
        assert files[0].has_zh_cn is True
        assert files[0].existing_zh_cn_path == "assets/mymod/lang/zh_cn.json"

    def test_missing_source_lang(self):
        jar = _make_test_jar({
            "assets/mymod/lang/zh_cn.json": json.dumps({"a": "1"}),
        })
        parser = JARParser(jar)
        files = parser.find_language_files()
        assert files == []

    def test_multiple_mods(self):
        jar = _make_test_jar({
            "assets/mod_a/lang/en_us.json": json.dumps({"x": "X"}),
            "assets/mod_b/lang/en_us.lang": "item.stick=Stick\n",
        })
        parser = JARParser(jar)
        files = parser.find_language_files()
        assert len(files) == 2
        mods = {f.modid for f in files}
        assert mods == {"mod_a", "mod_b"}

    def test_nonexistent_file_raises(self):
        with pytest.raises(ValueError):
            JARParser(Path("/nonexistent/path.jar"))

    def test_get_all_modids(self):
        jar = _make_test_jar({
            "assets/foo/lang/en_us.json": "{}",
            "assets/bar/lang/en_us.json": "{}",
        })
        parser = JARParser(jar)
        # 按 modid 字母序排序返回
        assert parser.get_all_modids() == ["bar", "foo"]
