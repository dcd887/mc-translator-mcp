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
import re
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
    # 可选：模组定制术语表（JSON，key=英文术语，value=强制中文译名）
    glossary_file: str = "translator_glossary.json"
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
        "你是一位专业的 Minecraft 游戏汉化工程师，精通《我的世界》官方中文术语表与社区通用译法。\n"
        "任务：将下列英文游戏文本准确、自然地翻译成简体中文。\n"
        "规则：\n"
        "1. 输出必须是**纯简体中文**。除专有名词或通用缩写（如 FE、RF、GUI）外，"
        "译文里严禁残留任何英文单词（例如 \"ore\" 必须译为\"矿石\"，不允许出现\"沥青铀矿 ore\"这种中英混排）。\n"
        "2. 保持 Minecraft 风格术语（Block→方块，Item→物品，Inventory→背包，Health→生命，"
        "Craft→合成，Enchant→附魔，Tool→工具，Armor→盔甲，Chunk→区块，Ender→末影，Enderman→末影人）。\n"
        "3. 术语一致性：同一英文术语在本模组内必须始终使用同一个中文译名，"
        "请先为整批条目统一术语再一次性翻译，不得一会儿一个译名。\n"
        "4. 保留所有格式占位符与标签：{0}、%s、$variable$、§颜色码、\\n 以及 <modid:item> 物品标签，"
        "绝对不允许修改或删除。\n"
        "5. 物品/方块/机器名带升级等级后缀（如 Starter、Basic、Advanced、Hardened、Blazing、Nitro 等）时，"
        "主名与等级分别翻译，等级词全模组统一：例如 Starter→初级、Basic→基础、Advanced→高级、"
        "Hardened→硬化、Blazing→炽热，Nitro 等具体等级以该模组社区通用译法为准。\n"
        "6. 长描述/wiki/提示（tooltip）不要逐字直译，按中文表达习惯自然润色，避免机翻腔。"
        "例如\"When Applying A to B will make it C\"应译为\"把 A 装到 B 上，就能让它 C\"，而不是逐字硬译。\n"
        "7. 专有名词、知名模组/系列名、科技与化学名词优先采用官方与社区通认译名，不得随意直译"
        "（如 Sodium→钠、Copper→铜、Uraninite→晶质铀矿、Dielectric→绝缘、Furnator→熔炉发电机）。\n"
        "8. 如某条目为空格或纯占位符，原样返回。\n"
        "9. 只输出翻译结果，每行格式：<原key>:<中文翻译>，无需任何解释。"
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
        self._glossary = self._load_glossary(config.glossary_file)
        self._system_prompt = self._build_system_prompt(self._glossary)

    @staticmethod
    def _load_glossary(path: str) -> dict[str, str]:
        """加载模组定制术语表（JSON），返回 {术语: 强制译名}。

        支持两种格式：
          - 扁平映射：{"Niotic": "钻石", ...}
          - 带 "terms" 键：{"terms": {"Niotic": "钻石", ...}, ...}
        文件缺失或解析失败时返回空 dict，不影响使用。
        """
        p = Path(path)
        if not p.is_file():
            return {}
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        if isinstance(data, dict) and isinstance(data.get("terms"), dict):
            data = data["terms"]
        if not isinstance(data, dict):
            return {}
        return {
            str(k).strip(): str(v).strip()
            for k, v in data.items()
            if str(k).strip() and str(v).strip()
        }

    def _build_system_prompt(self, glossary: dict[str, str]) -> str:
        """在系统提示词末尾追加术语表，强制使用定制译名。"""
        prompt = self.SYSTEM_PROMPT
        if glossary:
            terms = "\n".join(f"  {k} → {v}" for k, v in glossary.items())
            prompt += (
                "\n\n10. 术语表（模组定制）：以下术语在本模组中必须严格使用右侧的中文译名，"
                "即使看起来不像字面直译，也不得换成其它说法。\n"
                + terms
            )
        return prompt

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

    # 允许保留的通用英文缩写/名词（游戏内约定俗成，不算“残留英文”）
    _EN_ALLOWLIST = {
        "FE", "RF", "GUI", "TNT", "FPS", "TPS", "PVP", "PVE", "RGB", "API",
        "NBT", "ID", "IP", "URL", "GPS", "CPU", "GPU", "RAM", "DNA", "AI", "MC",
        "Forge", "Fabric", "NeoForge", "Quilt", "Java",
        "Shift", "Ctrl", "Alt", "Esc",
    }
    _EN_LEAK_RE = re.compile(r"[A-Za-z]{2,}")

    @classmethod
    def _strip_placeholders(cls, text: str) -> str:
        """去掉占位符/物品标签/颜色码/缩写后，再检查是否残留英文。"""
        s = re.sub(r"<[^>]+>", "", text)                          # <powah:wrench>
        s = re.sub(r"%[-+0-9.]*(?:[0-9]+\$)?[a-zA-Z]", "", s)      # %s %1$s %2$d
        s = re.sub(r"\{[^}]*\}", "", s)                           # {0}
        s = re.sub(r"\$[A-Za-z_][\w]*\$", "", s)                  # $variable$
        s = re.sub(r"§[0-9a-fk-orK-OR]", "", s)                   # §颜色码
        # 注意：Python 的 \b/\w 是 Unicode 感知的，中文也算 \w，导致中英相邻时
        # 单词边界不存在。因此这里用「仅以 ASCII 字母数字为界」的环视来匹配缩写。
        for w in cls._EN_ALLOWLIST:
            s = re.sub(
                rf"(?<![A-Za-z0-9]){re.escape(w)}(?![A-Za-z0-9])",
                "", s, flags=re.IGNORECASE,
            )
        s = re.sub(r"(?<![A-Za-z0-9])[a-z](?![A-Za-z0-9])", "", s)  # 单位缩写（单字母）
        return s

    @classmethod
    def _has_english_leak(cls, text: str) -> bool:
        """判断译文是否残留了英文单词（忽略占位符/缩写）。"""
        if not text or not text.strip():
            return False
        stripped = cls._strip_placeholders(text)
        return bool(cls._EN_LEAK_RE.search(stripped))

    def _chat(self, client: OpenAI, model: str, messages: list[dict]) -> str:
        """发起一次对话，返回内容文本。"""
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=0.2,
            max_tokens=2048,
        )
        return resp.choices[0].message.content or ""

    @staticmethod
    def _fix_prompt(
        modid: str,
        entries: dict[str, str],
        instruction: str,
    ) -> str:
        lines = "\n".join(f"{k}={v}" for k, v in entries.items())
        return (
            f"模组 ID: {modid}（Minecraft 《我的世界》1.20.1 整合包模组）\n"
            f"{instruction}\n"
            f"每行格式：<key>:<译文>。\n"
            f"```\n{lines}\n```"
        )

    def _call_llm(self, modid: str, batch: dict[str, str]) -> dict[str, str]:
        """调用 LLM 翻译一个批次，返回 {key: translation}。

        主供应商失败时，若有 DeepSeek fallback 则自动切换重试一次。
        翻译结果还会做两道修复：
          1. 漏译条目补翻（模型偶尔会少返回几个 key）
          2. 残留英文单词的译文纠正为纯中文
        """
        lines = "\n".join(f"{k}={v}" for k, v in batch.items())
        user_msg = (
            f"模组 ID: {modid}（这是 Minecraft 《我的世界》1.20.1 整合包中的一个模组，"
            f"请用 Minecraft 官方中文术语风格翻译）\n"
            f"请翻译以下条目，同一术语保持全模组统一，每行返回格式：<key>:<translation>\n"
            f"```\n{lines}\n```"
        )
        messages = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user_msg},
        ]

        candidates = [self._primary]
        if self._fallback and self._fallback != self._primary:
            candidates.append(self._fallback)

        for idx, (client, model) in enumerate(candidates):
            if client is None:
                continue
            label = "primary" if idx == 0 else "fallback"
            try:
                text = self._chat(client, model, messages)
                parsed = self._parse_response(text, batch)

                # 修复 1：漏译条目补翻
                missing = {k: v for k, v in batch.items() if k not in parsed}
                if missing:
                    try:
                        fix = self._chat(client, model, messages + [
                            {"role": "assistant", "content": text},
                            {"role": "user", "content": self._fix_prompt(
                                modid, missing,
                                "上一轮你漏译了以下条目，请补齐并全部返回，"
                                "注意值里的 <modid:item> 物品标签原样保留。",
                            )},
                        ])
                        parsed.update(self._parse_response(fix, missing))
                    except Exception as e:
                        print(f"[WARN] 补翻漏译失败（{label}，{modid}，{len(missing)} 条）: {e}")

                # 修复 2：残留英文纠正
                leaky = {k: v for k, v in parsed.items() if self._has_english_leak(v)}
                if leaky:
                    try:
                        fix = self._chat(client, model, messages + [
                            {"role": "assistant", "content": text},
                            {"role": "user", "content": self._fix_prompt(
                                modid, leaky,
                                "以下条目的译文里残留了英文单词，请改为纯简体中文，"
                                "通用缩写（FE/RF/GUI 等）与 <modid:item> 标签可保留。",
                            )},
                        ])
                        parsed.update(self._parse_response(fix, leaky))
                    except Exception as e:
                        print(f"[WARN] 英文残留纠正失败（{label}，{modid}，{len(leaky)} 条）: {e}")

                return parsed
            except Exception as e:
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
