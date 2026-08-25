# mc-translator-mcp
Minecraft 模组中文翻译 MCP 工具

自动从模组 jar 包提取语言文件，调用通义千问批量翻译，生成中文资源包。

## 安装

```bash
cd mc-translator-mcp
pip install -e .
```

## 配置

**不绑定任何特定 AI 供应商**。复制 `.env.example` 为 `.env`，填任一家的 key 即可（优先级 `custom > agnes > dashscope`，可选 DeepSeek 兜底）：

```bash
cp .env.example .env
```

任选一种方式（检测到哪个 key 就用哪个）：

```bash
# 方式 A（推荐，最通用）：任意 OpenAI 兼容供应商 —— 自已填 key / 网关 / 模型名
TRANSLATOR_API_KEY=sk-xxx
TRANSLATOR_BASE_URL=https://your-gateway.example.com/v1
TRANSLATOR_MODEL=your-model

# 方式 B：agnes ai
# AGNES_API_KEY=sk-xxx
# AGNES_BASE_URL=https://apihub.agnes-ai.com/v1
# AGNES_MODEL=agnes-2.5-flash

# 方式 C：通义千问（阿里云百炼）—— https://bailian.console.aliyun.com/
# DASHSCOPE_API_KEY=sk-xxx
# QWEN_MODEL=qwen-plus

# 方式 D：DeepSeek（可选，主供应商失败时 fallback）
# DEEPSEEK_API_KEY=
# DEEPSEEK_MODEL=deepseek-chat
```

> 所有配置都从环境变量 / `.env` 读取，**任何环境本地都能跑通、可移植**。填好任一家的 key 即可开始翻译；改成别的供应商只需改 `.env`，不用动代码。

## 启动方式

### 作为 MCP 服务器（推荐）
```bash
python -m mc_translator_mcp
```

在 Trae/Claude Desktop 中配置 MCP server：
```json
{
  "mcpServers": {
    "mc-translator-mcp": {
      "command": "python",
      "args": ["-m", "mc_translator_mcp"]
    }
  }
}
```

### CLI 模式
```bash
# 检查 jar 语言文件
python -m mc_translator_mcp check <jar_path>

# 零成本预览：看会翻译哪些模组、多少条文本、预估 token（不调 AI、不写文件）
python -m mc_translator_mcp preview <jar_path>

# 抽样翻译预览质量（会消耗少量 token，不写文件）
python -m mc_translator_mcp dry-run <jar_path> [--limit 20]

# 翻译单个 jar
python -m mc_translator_mcp mod <jar_path> [--batch-size 15] [--force-retranslate]

# 批量翻译目录下所有 jar
python -m mc_translator_mcp dir <directory> [--glob "*.jar"] [--batch-size 15]
```

> 💡 **先 preview 再翻译**：翻译会消耗 token。正式翻译前先跑 `preview` 看工作量和预估消耗，或 `dry-run` 抽样体验翻译质量，再决定是否执行。

## 使用示例

### 在 Trae 对话中使用 MCP 工具
直接对 Trae 说：
> "帮我翻译这个模组：/path/to/mymod.jar"

Trae 会自动调用 MCP 工具的 `translate_mod` 或 `translate_all_mods_in_directory`。

### 本地运行
```bash
# 翻译单个模组
python -m mc_translator_mcp mod "C:/Users/kjds/Desktop/mods/myzombie.jar" --batch-size 20

# 批量翻译整个 mods 目录
python -m mc_translator_mcp dir "C:/Users/kjds/Desktop/.minecraft/mods" --glob "*.jar"
```

## 输出

默认输出到 `output/` 目录，每个模组生成独立资源包：
```
output/
├── mymod-zh-cn/
│   ├── pack.mcmeta
│   └── assets/mymod/lang/zh_cn.json
├── zombie_mod-zh-cn/
│   ├── pack.mcmeta
│   └── assets/zombie_mod/lang/zh_cn.json
└── ...
```

加载方式：将 `output/<modid>-zh-cn/` 文件夹复制到 Minecraft 的 `resourcepacks/` 目录。

## 翻译质量与术语一致性

调用翻译时，会把「这是一个 Minecraft 1.20.1 整合包的模组语言文件」作为上下文注入提示词，并按以下规则约束翻译：

- **Minecraft 官方术语**：`Block`→方块、`Item`→物品、`Inventory`→背包、`Health`→生命、`Craft`→合成、`Enchant`→附魔、`Tool`→工具、`Armor`→盔甲、`Chunk`→区块
- **术语全程一致**：同一英文术语在整个模组内固定用一个中文译名（不会出现一会儿「背包」一会儿「物品栏」），整批条目一起统一后再落盘
- **知名名词保持通认**：知名模组 / 系列名、科技与化学类专业词保持社区通认译法，不随意直译，如 `Sodium`→钠、`Copper`→铜
- **格式占位符绝不改动**：`{0}`、`%s`、`$variable$`、`§`颜色码、`\n` 等原样保留
- **只回传译文**：每条按 `<key>:<中文翻译>` 返回，不做额外解释

> 你可以在 `translator.py` 的 `SYSTEM_PROMPT` 中按需补充自己的术语表或规则，改完即生效。

## 核心模块

| 模块 | 功能 |
|------|------|
| `jar_parser.py` | 解析 jar 包结构，发现语言文件 |
| `lang_parser.py` | 解析 .json / .lang / .properties 格式语言文件 |
| `translator.py` | 多供应商批量翻译（custom / agnes / 通义 / DeepSeek）+ Minecraft 术语提示 + 本地缓存 |
| `pack_builder.py` | 生成资源包或改写 jar |
| `mcp_server.py` | MCP 服务器入口，暴露 4 个工具：`translate_mod` / `translate_all_mods_in_directory` / `preview_mod`（零成本预览）/ `dry_run_mod`（抽样预览） |

## 测试

```bash
python -m pytest tests/ -v
```

## 设计要点

1. **资源包优先**：默认生成独立资源包，不破坏原 jar 文件
2. **智能缓存**：相同原文 + modid 的条目只翻译一次，避免重复消耗 token
3. **已有汉化跳过**：检测到已有 zh_cn.json 时自动跳过，避免覆盖社区翻译
4. **批量翻译**：每批最多 BATCH_SIZE 条，一次 API 调用返回全部结果
5. **格式兼容**：支持 `.json`（现代）、`.lang`（传统）与 `.properties`（部分老模组/Java 习惯）三种语言文件格式；`.properties` 源会自动转为 Minecraft 可加载的 `zh_cn.json` 输出
