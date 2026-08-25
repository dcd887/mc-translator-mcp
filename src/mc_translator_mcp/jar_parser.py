# -*- coding: utf-8 -*-
"""JAR 解析模块：读取模组 jar 包，发现语言文件。

支持的 jar 语言文件路径（按优先级排序）：
  - assets/<modid>/lang/en_us.json
  - assets/<modid>/lang/en_us.lang
  - assets/<modid>/lang/en_US.json   （部分老模组用此命名）
  - assets/<modid>/lang/en_US.lang

发现规则：
  1. 扫描 jar 中所有 assets/<id>/lang/ 子目录
  2. 优先找 en_us / en_US 的 .json 或 .lang
  3. 记录 modid、格式、路径、是否存在 zh_cn
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class LanguageFile:
    """一个语言文件的元信息。"""
    modid: str
    format: str                # "json" | "lang"
    zip_path: str              # jar 内路径，如 assets/mymod/lang/en_us.json
    has_zh_cn: bool = False
    existing_zh_cn_path: Optional[str] = field(default=None)
    # 运行时挂载，非 frozen（用于 pack_builder 反向定位源 jar）
    _jar_path: Optional[str] = field(default=None, repr=False)


class JARParser:
    """解析单个 jar 包的语言文件。"""

    SOURCE_NAMES: set[str] = {
        "en_us.json",
        "en_us.lang",
        "en_US.json",
        "en_US.lang",
        "en_us.properties",
        "en_US.properties",
    }

    ZH_NAME = "zh_cn.json"

    def __init__(self, jar_path: Path) -> None:
        if not jar_path.is_file():
            raise ValueError(f"jar 文件不存在: {jar_path}")
        self.jar_path = jar_path.resolve()

    def find_language_files(self) -> list[LanguageFile]:
        """扫描 jar 包，返回所有含英文源文本的语言文件。"""
        result: list[LanguageFile] = []
        with zipfile.ZipFile(self.jar_path, "r") as zf:
            names = zf.namelist()
            # 预建 modid -> 文件名集合（小写）+ 实际名称映射
            mod_lang: dict[str, dict[str, str]] = {}  # lower_name -> actual_name
            for n in names:
                if not n.startswith("assets/") or "/lang/" not in n:
                    continue
                parts = n.split("/")
                if len(parts) < 4 or parts[1] == "" or parts[3] == "":
                    continue
                modid = parts[1]
                fname = parts[3]
                mod_lang.setdefault(modid, {})[fname.lower()] = fname

            for modid, fname_map in sorted(mod_lang.items()):
                # 找源语言文件（en_us / en_US）
                source_lower = next(
                    (f for f in self.SOURCE_NAMES if f.lower() in fname_map),
                    None,
                )
                if source_lower is None:
                    continue
                actual_name = fname_map[source_lower.lower()]
                name_lower = actual_name.lower()
                if name_lower.endswith(".json"):
                    fmt = "json"
                elif name_lower.endswith(".lang"):
                    fmt = "lang"
                else:
                    fmt = "properties"
                # 找对应中文路径
                zh_path = self._find_zh_cn(names, modid, actual_name)
                result.append(LanguageFile(
                    modid=modid,
                    format=fmt,
                    zip_path=f"assets/{modid}/lang/{actual_name}",
                    has_zh_cn=zh_path is not None,
                    existing_zh_cn_path=zh_path,
                    _jar_path=str(self.jar_path),
                ))
        return result

    def _find_zh_cn(self, names: list[str], modid: str, en_name: str) -> Optional[str]:
        """在同 modid 的 lang 目录下查找 zh_cn.json。"""
        prefix = f"assets/{modid}/lang/"
        for n in names:
            if n.startswith(prefix) and n.lower().endswith("zh_cn.json"):
                return n
        return None

    def read_language_bytes(self, lang_file: LanguageFile) -> bytes:
        """从 jar 中读取指定语言文件的原始字节。"""
        with zipfile.ZipFile(self.jar_path, "r") as zf:
            return zf.read(lang_file.zip_path)

    def get_all_modids(self) -> list[str]:
        """返回 jar 中所有包含语言文件的 modid 列表（按首次出现顺序）。"""
        return [lf.modid for lf in self.find_language_files()]
