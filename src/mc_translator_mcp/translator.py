# -*- coding: utf-8 -*-
"""翻译模块：支持任意 OpenAI 兼容供应商的批量翻译 + 本地缓存。

核心设计：**不绑定任何特定供应商**。所有配置从环境变量 / `.env` 读取，
由使用者自备 API Key / base_url / 模型名，任何环境均可本地跑通、可移植。
优先级：custom（通用）> agnes > dashscope，可选 DeepSeek 做 fallback。

设计要点：
  - 批量翻译：每次最多 BATCH_SIZE 个 key-value，一次 API 调用返回全部
  - 缓存层：translator_cache.json 记录已翻译的 (modid, key, hash)，避免重复消耗 token
  - 容错：单次批次失败只回退该批次，不影响其他批次；主供应商失败可切 DeepSeek 兜底
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional

from openai import OpenAI

try:
    from pydantic_settings import BaseSettings
except ImportError:  # fallback
    from typing import Any

    class BaseSettings:  # type: ignore
        model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

        @classmethod
        def settings_customise_sources(cls, settings, *args, **kwargs):
            return ()


class TranslConfig(BaseSettings):
    """翻译配置：支持任意 OpenAI 兼容供应商，从环境变量 / `.env` 读取。

    完全不绑定某一家。优先级：custom（通用，最灵活）> agnes > dashscope。
    可选 DeepSeek 做 fallback。你在哪配了 key，就用哪家。

    配置项：
      - 通用（推荐）：TRANSLATOR_API_KEY / TRANSLATOR_BASE_URL / TRANSLATOR_MODEL
      - agnes ai：AGNES_API_KEY / AGNES_BASE_URL / AGNES_MODEL
      - 通义千问：DASHSCOPE_API_KEY / QWEN_MODEL
      - DeepSeek（fallback）：DEEPSEEK_API_KEY / DEEPSEEK_MODEL
    """
    # 通用 OpenAI 兼容供应商（最灵活，优先）
    translator_api_key: str = ""
    translator_base_url: str = ""
    translator_model: str = ""
    # agnes ai 别名
    agnes_api_key: str = ""
    agnes_base_url: str = "https://apihub.agnes-ai.com/v1"
    agnes_model: str = "agnes-2.5-flash"
    # 通义千问 别名
    dashscope_api_key: str = ""
    qwen_model: str = "qwen-plus"
    # DeepSeek 别名（可选 fallback）
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"

    batch_size: int = 15
    output_dir: str = "output"
    cache_file: str = "translator_cache.json"
    use_deepseek_fallback: bool = True
    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


class TranslationCache:
    """本地词条缓存，避免重复翻译相同的原文。"""

    def __init__(self, cache_path: Path) -> None:
        self.cache_path = cache_path
        self._cache: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if self.cache_path.exists():
            try:
                self._cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._cache = {}

    def _save(self) -> None:
        try:
            self.cache_path.write_text(
                json.dumps(self._cache, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass  # 缓存写失败不阻塞主流程

    @staticmethod
    def _key_hash(modid: str, key: str, value: str) -> str:
        """生成缓存 key：modid + 原文指纹。"""
        raw = f"{modid}:{key}:{value}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def get(self, modid: str, key: str, value: str) -> Optional[str]:
        h = self._key_hash(modid, key, value)
        entry = self._cache.get(h)
        if entry and isinstance(entry, dict):
            return entry.get("translation")
        return None

    def set(self, modid: str, key: str, value: str, translation: str) -> None:
        h = self._key_hash(modid, key, value)
        self._cache[h] = {"original": value, "translation": translation, "ts": time.time()}
        self._save()

    def flush(self) -> None:
        self._save()


class Translator:
    """调用通义千问进行批量翻译。"""

    SYSTEM_PROMPT = (
        "你是一位专业的 Minecraft 游戏汉化工程师，精通 Minecraft《我的世界》"
        "官方中文术语表与社区的通用译法。\n"
        "任务：将下列英文游戏文本准确翻译成简体中文。\n"
        "规则：\n"
        "1. 保持 Minecraft 风格术语（如 \"Block\"→\"方块\"，\"Item\"→\"物品\"，"
        "\"Inventory\"→\"背包\"，\"Health\"→\"生命\"，\"Craft\"→\"合成\"，"
        "\"Enchant\"→\"附魔\"，\"Tool\"→\"工具\"，\"Armor\"→\"盔甲\"，\"Chunk\"→\"区块\"）。\n"
        "2. 术语一致性：同一英文术语在本模组内必须始终使用同一个中文译名，"
        "不得一会儿「背包」一会儿「物品栏」；请为整批条目统一术语后一次性翻译。\n"
        "3. 保留所有格式占位符：{0}、%s、$variable$、§颜色码、\\n 等绝对不能修改。\n"
        "4. 只输出翻译结果，每行格式为：<原key>:<中文翻译>，无需任何解释。\n"
        "5. 如某条目为空格或纯占位符，原样返回。\n"
        "6. 专有名词（人名、地名、物品名）优先采用 Minecraft 官方中文译名；"
        "没有官方译名的按社区通用译法，再按中文表达习惯意译。\n"
        "7. 知名模组、系列名、科技/化学等专业名词保持社区通认译法，不得随意直译或乱改。"
        "例如资源/合成表、元素类内容保持官方译名（如 \"Sodium\"→\"钠\"，\"Copper\"→\"铜\"）。"
    )

    def __init__(
        self,
        config: TranslConfig,
        cache: TranslationCache,
    ) -> None:
        self.cfg = config
        self.cache = cache
        self._primary: Optional[tuple[OpenAI, str]] = None   # (client, model)
        self._fallback: Optional[tuple[OpenAI, str]] = None  # DeepSeek 兜底
        self._init_clients()
        if not self._primary:
            raise ValueError(
                "未配置可用的 API Key。请通过 `.env` 或环境变量至少设置一个供应商：\n"
                "  TRANSLATOR_API_KEY / TRANSLATOR_BASE_URL / TRANSLATOR_MODEL（通用，推荐）\n"
                "  AGNES_API_KEY / DASHSCOPE_API_KEY / DEEPSEEK_API_KEY"
            )

    def _init_clients(self) -> None:
        """按优先级装配主/备用客户端：custom > agnes > dashscope，可选 DeepSeek 兜底。"""
        c = self.cfg

        if c.translator_api_key:
            self._primary = (
                OpenAI(api_key=c.translator_api_key, base_url=c.translator_base_url or None),
                c.translator_model,
            )
        elif c.agnes_api_key:
            self._primary = (
                OpenAI(api_key=c.agnes_api_key, base_url=c.agnes_base_url),
                c.agnes_model,
            )
        elif c.dashscope_api_key:
            self._primary = (
                OpenAI(api_key=c.dashscope_api_key,
                       base_url="https://dashscope.aliyuncs.com/compatible-mode/v1"),
                c.qwen_model,
            )

        if c.use_deepseek_fallback and c.deepseek_api_key:
            self._fallback = (
                OpenAI(api_key=c.deepseek_api_key,
                       base_url="https://api.deepseek.com/v1"),
                c.deepseek_model,
            )

    def translate_batch(
        self,
        modid: str,
        entries: dict[str, str],
    ) -> dict[str, str]:
        """批量翻译一组条目，返回 {key: zh_cn}。"""
        if not entries:
            return {}

        # 先从缓存取已有的
        cached: dict[str, str] = {}
        missing: dict[str, str] = {}
        for k, v in entries.items():
            hit = self.cache.get(modid, k, v)
            if hit is not None:
                cached[k] = hit
            else:
                missing[k] = v

        if not missing:
            return cached

        # 分批请求
        items = list(missing.items())
        batch_size = max(1, self.cfg.batch_size)
        all_result: dict[str, str] = {}
        for i in range(0, len(items), batch_size):
            batch = dict(items[i: i + batch_size])
            translated = self._call_llm(modid, batch)
            for k, v in batch.items():
                t = translated.get(k)
                if t:
                    all_result[k] = t
                    self.cache.set(modid, k, v, t)
                # 未翻译的 key 直接跳过（保留空白，不污染缓存）
        return {**cached, **all_result}

    def _call_llm(self, modid: str, batch: dict[str, str]) -> dict[str, str]:
        """调用 LLM 翻译一个批次，返回 {key: translation}。

        主供应商失败时，若有 DeepSeek fallback 则自动切换重试一次。
        """
        lines = "\n".join(f"{k}={v}" for k, v in batch.items())
        user_msg = (
            f"模组 ID: {modid}（这是 Minecraft 《我的世界》1.20.1 整合包中的一个模组，"
            f"请用 Minecraft 官方中文术语风格翻译）\n"
            f"请翻译以下条目，同一术语保持全模组统一，每行返回格式：<key>:<translation>\n"
            f"```\n{lines}\n```"
        )
        messages = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ]

        candidates = [self._primary]
        if self._fallback and self._fallback != self._primary:
            candidates.append(self._fallback)

        for idx, (client, model) in enumerate(candidates):
            if client is None:
                continue
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0.2,
                    max_tokens=2048,
                )
                text = resp.choices[0].message.content or ""
                return self._parse_response(text, batch)
            except Exception as e:
                label = "primary" if idx == 0 else "fallback"
                print(f"[WARN] LLM 调用失败（{label}，{modid}，{len(batch)} 条）: {e}")

        return {}

    @classmethod
    def _parse_response(cls, text: str, expected: dict[str, str]) -> dict[str, str]:
        """解析模型返回，严格对照 expected 的 key。"""
        result: dict[str, str] = {}
        for raw_line in text.splitlines():
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            # 支持两种分隔符：key:value 或 key=value
            sep = ":" if ":" in raw_line else "="
            if sep not in raw_line:
                continue
            k, _, v = raw_line.partition(sep)
            k = k.strip()
            v = v.strip()
            # 只接受在 expected 中存在的 key
            if k in expected:
                result[k] = v
        return result
