# -*- coding: utf-8 -*-
"""语言文件解析模块：支持 Minecraft 两种主流语言格式。

.json 格式（现代）:
  {"item.my_mod.stick": "Stick", "block.my_mod.grass": "Grass Block", ...}

.lang 格式（传统）:
  item.my_mod.stick=Stick
  block.my_mod.grass=Grass Block
  # 支持注释行和空行
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional


class LangParseError(Exception):
    """语言文件解析错误。"""


class LangParser:
    """解析 .json / .lang 文件，返回 ordered dict（保留顺序）。"""

    # .lang 文件 key 正则（兼容常见规范）
    _LANG_KEY_RE = re.compile(r"^([a-z0-9_.]+)=.*$")

    @classmethod
    def parse_json(cls, data: bytes) -> dict[str, str]:
        """解析 JSON 格式语言文件。"""
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as e:
            raise LangParseError(f"JSON 解析失败: {e}") from e
        if not isinstance(obj, dict):
            raise LangParseError(f"JSON 顶层必须为对象，实际为 {type(obj).__name__}")
        # 强制所有 value 为字符串
        return {str(k): str(v) for k, v in obj.items()}

    @classmethod
    def parse_lang(cls, data: bytes) -> dict[str, str]:
        """解析 .lang 格式语言文件。"""
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        result: dict[str, str] = {}
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            m = cls._LANG_KEY_RE.match(stripped)
            if not m:
                continue  # 忽略格式异常行
            key = m.group(1)
            value = stripped[len(key) + 1:].strip()
            result[key] = value
        return result

    @classmethod
    def parse(cls, data: bytes, fmt: str) -> dict[str, str]:
        """统一入口：根据格式调用对应解析器。"""
        if fmt == "json":
            return cls.parse_json(data)
        if fmt == "lang":
            return cls.parse_lang(data)
        raise ValueError(f"不支持的格式: {fmt!r}")

    @classmethod
    def serialize_json(cls, entries: dict[str, str]) -> bytes:
        """将条目序列化为 UTF-8 JSON bytes（ensure_ascii=False 保留中文）。"""
        return json.dumps(entries, ensure_ascii=False, indent=2, sort_keys=False).encode("utf-8")

    @classmethod
    def serialize_lang(cls, entries: dict[str, str]) -> bytes:
        """将条目序列化为 .lang bytes（UTF-8）。"""
        lines: list[str] = []
        for k, v in entries.items():
            lines.append(f"{k}={v}")
        return "\n".join(lines).encode("utf-8")

    @classmethod
    def serialize(cls, entries: dict[str, str], fmt: str) -> bytes:
        """统一入口：序列化。"""
        if fmt == "json":
            return cls.serialize_json(entries)
        if fmt == "lang":
            return cls.serialize_lang(entries)
        raise ValueError(f"不支持的格式: {fmt!r}")

    @classmethod
    def merge_with_existing(
        cls,
        new_entries: dict[str, str],
        existing_zh_data: Optional[bytes | str],
        fmt: str,
    ) -> dict[str, str]:
        """将新翻译结果与已存在的 zh_cn 合并（已有 key 不覆盖，避免回退旧翻译）。

        ``existing_zh_data`` 可以是：
        - ``None``：无已有翻译，直接返回 new_entries
        - ``bytes``：已有 zh_cn 文件的原始字节（测试用）
        - ``str``：已有 zh_cn 文件的路径（生产用，由 pack_builder 传入）
        """
        if existing_zh_data is None:
            return new_entries
        if isinstance(existing_zh_data, str):
            # 路径模式：读取文件字节
            p = Path(existing_zh_data)
            if not p.exists():
                return new_entries
            raw = p.read_bytes()
        else:
            raw = existing_zh_data
        existing = cls.parse(raw, fmt)
        merged = dict(existing)
        for k, v in new_entries.items():
            merged.setdefault(k, v)
        return merged
