# -*- coding: utf-8 -*-
"""资源包/改写模块：把翻译结果写回 jar 或生成独立中文资源包。

默认输出「资源包」模式（不破坏原 jar，符合用户偏好）：
  output/<modid>-zh-cn/<pack.mcmeta>
  output/<modid>-zh-cn/assets/<modid>/lang/zh_cn.json

可选 jar 改写模式（谨慎使用，会生成一个新 jar）：
  output/<modid>-zh-cn.jar（在原 jar 基础上追加 zh_cn.json）
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .lang_parser import LangParser
from .jar_parser import LanguageFile


@dataclass
class TranslateResult:
    """单模组翻译结果。"""
    modid: str
    status: str                      # "success" | "skipped" | "failed"
    source_format: str               # "json" | "lang"
    entries_total: int
    entries_translated: int
    output_path: str                 # 资源包目录 或 改写后的 jar 路径
    message: str = ""


def _count_entries(data: bytes, fmt: str) -> int:
    """统计条目数（用于报告）。"""
    if fmt == "json":
        try:
            obj = json.loads(data.decode("utf-8"))
            return len(obj) if isinstance(obj, dict) else 0
        except Exception:
            return 0
    if fmt == "properties":
        try:
            return len(LangParser.parse(data, "properties"))
        except Exception:
            return 0
    # .lang
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    return sum(
        1 for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    )


class PackBuilder:
    """生成资源包或改写 jar。"""

    PACK_FORMAT_1_20_1 = 15  # Minecraft 1.20.1 对应 pack format

    def __init__(
        self,
        output_root: Path,
        mode: str = "pack",   # "pack" | "jar"
    ) -> None:
        self.output_root = output_root.resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.mode = mode

    def build(
        self,
        lang_file: LanguageFile,
        translated: dict[str, str],
        original_data: bytes,
    ) -> TranslateResult:
        """产出翻译结果（资源包模式或 jar 改写模式）。"""
        fmt = lang_file.format
        existing_data: Optional[bytes] = None
        if lang_file.has_zh_cn and lang_file.existing_zh_cn_path:
            # 优先从源 jar 读取（更可靠）
            if lang_file._jar_path and Path(lang_file._jar_path).exists():
                try:
                    with zipfile.ZipFile(lang_file._jar_path, "r") as zf:
                        existing_data = zf.read(lang_file.existing_zh_cn_path)
                except Exception:
                    existing_data = None
        merged = LangParser.merge_with_existing(
            translated,
            existing_data,
            fmt,
        )
        # Minecraft 不加载 .properties；.properties 源统一转为 .json 输出
        out_fmt = "json" if fmt == "properties" else fmt
        serialized = LangParser.serialize(merged, out_fmt)

        if self.mode == "jar":
            return self._write_to_jar(lang_file, serialized, original_data)
        return self._write_resource_pack(lang_file, serialized, out_fmt)

    def _write_resource_pack(
        self,
        lang_file: LanguageFile,
        data: bytes,
        fmt: str,
    ) -> TranslateResult:
        """生成独立资源包（推荐模式）。"""
        pack_dir = self.output_root / f"{lang_file.modid}-zh-cn"
        lang_dir = pack_dir / "assets" / lang_file.modid / "lang"
        lang_dir.mkdir(parents=True, exist_ok=True)
        out_file = lang_dir / ("zh_cn.json" if fmt == "json" else "zh_cn.lang")
        out_file.write_bytes(data)

        mcmeta = {
            "pack": {
                "pack_format": self.PACK_FORMAT_1_20_1,
                "description": (
                    f"{lang_file.modid} 中文汉化资源包"
                    f"（由 mc-translator-mcp 自动生成，{fmt.upper()} 格式）"
                ),
            }
        }
        (pack_dir / "pack.mcmeta").write_text(
            json.dumps(mcmeta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        total = _count_entries(data, fmt)
        return TranslateResult(
            modid=lang_file.modid,
            status="success",
            source_format=fmt,
            entries_total=total,
            entries_translated=len(data.decode("utf-8").splitlines()),
            output_path=str(pack_dir),
            message=f"资源包已生成：{pack_dir}",
        )

    def _write_to_jar(
        self,
        lang_file: LanguageFile,
        data: bytes,
        original_data: bytes,
    ) -> TranslateResult:
        """改写 jar：生成一个新的 jar，包含 zh_cn.json。"""
        src_jar = Path(lang_file.zip_path)  # LanguageFile.zip_path 实际是 jar 内的路径
        # 通过调用方传入 jar_path；这里用约定：在 LangFile 上挂载 _jar_path
        jar_src = getattr(lang_file, "_jar_path", None)
        if not jar_src or not Path(jar_src).exists():
            return TranslateResult(
                modid=lang_file.modid,
                status="failed",
                source_format=lang_file.format,
                entries_total=0,
                entries_translated=0,
                output_path="",
                message="无法定位源 jar（pack_builder 的 jar 改写模式需要完整路径）",
            )
        jar_src_path = Path(jar_src)
        dst = self.output_root / f"{lang_file.modid}-zh-cn.jar"
        zh_path = f"assets/{lang_file.modid}/lang/zh_cn.json"
        try:
            with zipfile.ZipFile(jar_src_path, "r") as zin:
                with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zf:
                    for info in zin.infolist():
                        if info.filename == zh_path:
                            continue  # 覆盖已有
                        zf.writestr(info, zin.read(info.filename))
                    zf.writestr(zh_path, data.decode("utf-8"))
        except Exception as e:
            return TranslateResult(
                modid=lang_file.modid,
                status="failed",
                source_format=lang_file.format,
                entries_total=0,
                entries_translated=0,
                output_path="",
                message=f"jar 写入失败: {e}",
            )
        return TranslateResult(
            modid=lang_file.modid,
            status="success",
            source_format=lang_file.format,
            entries_total=_count_entries(original_data, lang_file.format),
            entries_translated=_count_entries(data, lang_file.format),
            output_path=str(dst),
            message=f"改写 jar 已生成：{dst}",
        )

    def summarize(self, results: list[TranslateResult]) -> str:
        """生成汇总报告。"""
        lines = ["=== mc-translator-mcp 翻译汇总 ==="]
        ok = [r for r in results if r.status == "success"]
        skip = [r for r in results if r.status == "skipped"]
        fail = [r for r in results if r.status == "failed"]
        lines.append(
            f"成功：{len(ok)}  跳过（已有汉化）：{len(skip)}  失败：{len(fail)}"
        )
        for r in ok:
            lines.append(
                f"  ✅ {r.modid} ({r.source_format}) → {r.output_path}"
            )
        for r in skip:
            lines.append(f"  ⏭️  {r.modid} → {r.message}")
        for r in fail:
            lines.append(f"  ❌ {r.modid} → {r.message}")
        return "\n".join(lines)
