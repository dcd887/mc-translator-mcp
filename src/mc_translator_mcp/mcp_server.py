# -*- coding: utf-8 -*-
"""MCP 服务器入口：暴露翻译与预览工具。

工具：
  - translate_mod                    : 翻译单个模组 jar，生成中文资源包
  - translate_all_mods_in_directory  : 批量翻译目录下所有 jar
  - preview_mod                      : （零成本）预览汉化范围与预估 token，不调 AI 不写文件
  - dry_run_mod                      : 抽样翻译预览质量，不写入任何文件（会消耗少量 token）

启动方式：
  python -m mc_translator_mcp
  或通过 Trae/Claude Desktop 配置 MCP server 链接到：
  ["python", "-m", "mc_translator_mcp"]
"""

from __future__ import annotations

import argparse
import asyncio
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


def _get_cache(cache_file: str | None = None) -> TranslationCache:
    global _cache
    # 若显式指定了缓存路径（来自 config.cache_file），按配置创建，不复用全局单例
    if cache_file:
        path = Path(cache_file).expanduser().resolve()
        return TranslationCache(path)
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
        translator = Translator(cfg, _get_cache(cfg.cache_file))
    except ValueError as e:
        return f"❌ 配置错误：{e}\n请复制 .env.example 为 .env 并填入至少一个供应商的 API Key。"

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
async def preview_mod(jar_path: str) -> str:
    """（零成本）预览单个模组的汉化范围：列出每个语言文件、条目数与预估 token。

    只做 jar 扫描与文本解析，**不调用任何 AI 翻译 API、不消耗 token、不写任何文件**，
    用于在真正翻译前评估工作量与费用。

    Args:
        jar_path: 模组 jar 文件的绝对路径
    Returns:
        预览摘要 JSON（含每个 modid 的格式、条目数、是否已有 zh_cn、社区估算 token）
    """
    jar = Path(jar_path)
    if not jar.exists():
        return json.dumps({"error": f"文件不存在: {jar}"}, ensure_ascii=False, indent=2)

    try:
        parser = JARParser(jar)
        lang_files = parser.find_language_files()
    except ValueError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False, indent=2)

    if not lang_files:
        return json.dumps({"message": f"{jar.name} 中未找到 en_us/en_US 语言文件"}, ensure_ascii=False, indent=2)

    mods = []
    total_entries = 0
    total_chars = 0
    for lf in lang_files:
        try:
            data = parser.read_language_bytes(lf)
            entries = LangParser.parse(data, lf.format)
            n = len(entries)
            total_chars += sum(len(v) for v in entries.values())
        except Exception:
            n = 0
        total_entries += n
        mods.append({
            "modid": lf.modid,
            "source_format": lf.format,
            "zip_path": lf.zip_path,
            "has_zh_cn": lf.has_zh_cn,
            "entries_estimated": n,
        })

    # 粗略估算：英文约 4 字符/token，中文约 1 字符/token；仅给量级参考
    est_tokens = max(1, round(total_chars / 4))
    return json.dumps({
        "jar": str(jar),
        "mod_count": len(lang_files),
        "total_entries": total_entries,
        "total_source_chars": total_chars,
        "estimated_tokens_rough": est_tokens,
        "mods": mods,
        "note": "这是未调用 AI 的预览估算；实际 token 消耗以执行时供应商计费为准。",
    }, ensure_ascii=False, indent=2)


@mcp.tool()
async def dry_run_mod(
    jar_path: str,
    limit: int = 20,
    batch_size: int = 15,
) -> str:
    """预览翻译效果：解析单个模组并抽样翻译一小部分，**不写入任何文件**。

    注意：本工具**会调用 AI 翻译 API 并消耗少量 token**（最多 `limit` 条样例），
    只用来快速感受翻译质量，不会生成资源包、不改动原 jar。

    Args:
        jar_path: 模组 jar 文件的绝对路径
        limit: 最多预览（抽样翻译）的条目总数（默认 20）
        batch_size: 每批翻译的词条数（默认 15）
    Returns:
        预览 JSON，含原文-译文对照样例
    """
    jar = Path(jar_path)
    if not jar.exists():
        return json.dumps({"error": f"文件不存在: {jar}"}, ensure_ascii=False, indent=2)

    cfg = TranslConfig()
    if batch_size != 15:
        cfg.batch_size = batch_size
    try:
        translator = Translator(cfg, _get_cache(cfg.cache_file))
    except ValueError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False, indent=2)

    try:
        parser = JARParser(jar)
        lang_files = parser.find_language_files()
    except ValueError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False, indent=2)

    samples = []
    remaining = limit
    for lf in lang_files:
        if remaining <= 0:
            break
        try:
            data = parser.read_language_bytes(lf)
            entries = LangParser.parse(data, lf.format)
            sample = dict(list(entries.items())[:min(remaining, 10)])
            if sample:
                translated = translator.translate_batch(lf.modid, sample)
                samples.append({
                    "modid": lf.modid,
                    "format": lf.format,
                    "zip_path": lf.zip_path,
                    "samples": [{"key": k, "original": v, "translation": translated.get(k, "")} for k, v in sample.items()],
                })
                remaining -= len(sample)
        except Exception:
            continue

    return json.dumps({
        "jar": str(jar),
        "sample_count": sum(len(s["samples"]) for s in samples),
        "has_more": remaining <= 0 and sum(len(s["samples"]) for s in samples) > 0,
        "note": "dry-run 会调用 AI 并消耗少量 token，仅作质量预览；未写入任何文件。",
        "samples": samples,
    }, ensure_ascii=False, indent=2)


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
        translator = Translator(cfg, _get_cache(cfg.cache_file))
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

    p_preview = sub.add_parser("preview", help="预览汉化范围与预估 token（零成本，不调 AI）")
    p_preview.add_argument("jar_path", help="模组 jar 路径")

    p_dry = sub.add_parser("dry-run", help="抽样翻译预览质量（会消耗少量 token，不写文件）", aliases=["dry_run"])
    p_dry.add_argument("jar_path", help="模组 jar 路径")
    p_dry.add_argument("--limit", type=int, default=20)
    p_dry.add_argument("--batch-size", type=int, default=15)

    args = parser.parse_args()

    if args.command == "preview":
        print(asyncio.run(preview_mod(args.jar_path)))
        sys.exit(0)

    if args.command in ("dry-run", "dry_run"):
        print(asyncio.run(dry_run_mod(args.jar_path, limit=args.limit, batch_size=args.batch_size)))
        sys.exit(0)

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
