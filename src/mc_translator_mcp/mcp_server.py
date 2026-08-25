# -*- coding: utf-8 -*-
"""MCP 服务器入口：暴露 translate_mod / translate_all_mods_in_directory 两个工具。

启动方式：
  python -m mc_translator_mcp
  或通过 Trae/Claude Desktop 配置 MCP server 链接到：
  ["python", "-m", "mc_translator_mcp"]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .jar_parser import JARParser
from .lang_parser import LangParser
from .translator import TranslConfig, TranslationCache, Translator
from .pack_builder import PackBuilder

mcp = FastMCP("mc-translator-mcp")

# 全局缓存（进程内）
_cache: TranslationCache | None = None


def _get_cache() -> TranslationCache:
    global _cache
    if _cache is None:
        cache_path = Path(__file__).resolve().parent.parent.parent / ".translator_cache.json"
        _cache = TranslationCache(cache_path)
    return _cache


@mcp.tool()
async def translate_mod(
    jar_path: str,
    batch_size: int = 15,
    force_retranslate: bool = False,
) -> str:
    """翻译单个 Minecraft 模组 jar 包，生成中文资源包。

    Args:
        jar_path: 模组 jar 文件的绝对路径（如 C:\\...\\mymod-1.2.0.jar）
        batch_size: 每批翻译的词条数（默认 15，越大越快但单次请求更长）
        force_retranslate: 强制重新翻译（忽略缓存，慎用）
    Returns:
        翻译结果摘要（包含每个模组的输出路径）
    """
    jar = Path(jar_path)
    if not jar.exists():
        return f"❌ 文件不存在：{jar}"

    cfg = TranslConfig()
    # 允许命令行覆盖 batch_size
    if batch_size != 15:
        cfg.batch_size = batch_size

    try:
        translator = Translator(cfg, _get_cache())
    except ValueError as e:
        return f"❌ 配置错误：{e}\n请复制 .env.example 为 .env 并填入 DASHSCOPE_API_KEY"

    builder = PackBuilder(Path(cfg.output_dir), mode="pack")
    parser = JARParser(jar)
    lang_files = parser.find_language_files()
    if not lang_files:
        return f"⚠️  {jar.name} 中未找到 en_us/en_US 语言文件，跳过。"

    results = []
    for lf in lang_files:
        if not force_retranslate and lf.has_zh_cn:
            results.append(type("R", (), {
                "modid": lf.modid, "status": "skipped",
                "source_format": lf.format, "entries_total": 0,
                "entries_translated": 0, "output_path": "",
                "message": "已有 zh_cn，跳过（用 --force-retranslate 强制重翻）",
            })())
            continue
        try:
            data = parser.read_language_bytes(lf)
            entries = LangParser.parse(data, lf.format)
            translated = translator.translate_batch(lf.modid, entries)
            res = builder.build(lf, translated, data)
            results.append(res)
        except Exception as e:
            results.append(type("R", (), {
                "modid": lf.modid, "status": "failed",
                "source_format": lf.format, "entries_total": 0,
                "entries_translated": 0, "output_path": "",
                "message": str(e),
            })())

    return builder.summarize(results)


@mcp.tool()
async def translate_all_mods_in_directory(
    directory: str,
    glob_pattern: str = "*.jar",
    batch_size: int = 15,
    force_retranslate: bool = False,
) -> str:
    """批量翻译目录下所有模组 jar 包。

    Args:
        directory: 包含模组 jar 的目录路径
        glob_pattern: jar 文件匹配模式（默认 *.jar）
        batch_size: 每批翻译的词条数
        force_retranslate: 强制重新翻译
    Returns:
        批量翻译汇总
    """
    dir_path = Path(directory)
    if not dir_path.is_dir():
        return f"❌ 目录不存在：{dir_path}"

    jars = sorted(dir_path.glob(glob_pattern))
    if not jars:
        return f"⚠️  {dir_path} 下未找到匹配的 jar 文件（{glob_pattern}）"

    cfg = TranslConfig()
    if batch_size != 15:
        cfg.batch_size = batch_size
    try:
        translator = Translator(cfg, _get_cache())
    except ValueError as e:
        return f"❌ 配置错误：{e}"

    builder = PackBuilder(Path(cfg.output_dir), mode="pack")
    all_results = []
    for jar in jars:
        print(f"🔍 处理: {jar.name}")
        try:
            parser = JARParser(jar)
            lang_files = parser.find_language_files()
            for lf in lang_files:
                if not force_retranslate and lf.has_zh_cn:
                    all_results.append(type("R", (), {
                        "modid": lf.modid, "status": "skipped",
                        "source_format": lf.format, "entries_total": 0,
                        "entries_translated": 0, "output_path": "",
                        "message": f"{jar.name} 已有 zh_cn，跳过",
                    })())
                    continue
                data = parser.read_language_bytes(lf)
                entries = LangParser.parse(data, lf.format)
                translated = translator.translate_batch(lf.modid, entries)
                res = builder.build(lf, translated, data)
                all_results.append(res)
        except Exception as e:
            print(f"⚠️  处理 {jar.name} 失败: {e}")
            all_results.append(type("R", (), {
                "modid": jar.stem, "status": "failed",
                "source_format": "", "entries_total": 0,
                "entries_translated": 0, "output_path": "",
                "message": str(e),
            })())

    return builder.summarize(all_results)


def main() -> None:
    """CLI 入口：支持 python -m mc_translator_mcp 直接运行。"""
    parser = argparse.ArgumentParser(
        prog="mc-translator-mcp",
        description="Minecraft 模组中文翻译 MCP 工具",
    )
    sub = parser.add_subparsers(dest="command")

    p_mod = sub.add_parser("mod", help="翻译单个 jar")
    p_mod.add_argument("jar_path", help="模组 jar 路径")
    p_mod.add_argument("--batch-size", type=int, default=15)
    p_mod.add_argument("--force-retranslate", action="store_true")

    p_dir = sub.add_parser("dir", help="批量翻译目录下所有 jar")
    p_dir.add_argument("directory", help="模组目录路径")
    p_dir.add_argument("--glob", default="*.jar")
    p_dir.add_argument("--batch-size", type=int, default=15)
    p_dir.add_argument("--force-retranslate", action="store_true")

    p_check = sub.add_parser("check", help="检查 jar 语言文件，不翻译")
    p_check.add_argument("jar_path", help="模组 jar 路径")

    args = parser.parse_args()

    if args.command == "check":
        jar = Path(args.jar_path)
        if not jar.exists():
            print(f"❌ 文件不存在：{jar}")
            sys.exit(1)
        parser = JARParser(jar)
        files = parser.find_language_files()
        if not files:
            print(f"⚠️  {jar.name} 中未找到英文语言文件。")
            sys.exit(0)
        print(f"📦 {jar.name} 发现 {len(files)} 个语言文件：")
        for lf in files:
            flag = "✅ 已有 zh_cn" if lf.has_zh_cn else "⬜ 无 zh_cn"
            print(f"   [{lf.format}] {lf.modid} — {lf.zip_path} {flag}")
        sys.exit(0)

    if args.command == "mod":
        result = translate_mod(
            jar_path=args.jar_path,
            batch_size=args.batch_size,
            force_retranslate=args.force_retranslate,
        )
        print(result)
        sys.exit(0 if "❌" not in result else 1)

    if args.command == "dir":
        result = translate_all_mods_in_directory(
            directory=args.directory,
            glob_pattern=args.glob,
            batch_size=args.batch_size,
            force_retranslate=args.force_retranslate,
        )
        print(result)
        sys.exit(0 if "❌" not in result else 1)

    # 无命令：启动 MCP server
    mcp.run()


if __name__ == "__main__":
    main()
