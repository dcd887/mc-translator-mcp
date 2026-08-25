# mc-translator-mcp
Minecraft 模组中文翻译 MCP 工具

自动从模组 jar 包提取语言文件，调用通义千问批量翻译，生成中文资源包。

## 安装

```bash
cd mc-translator-mcp
pip install -e .
```

## 配置

复制 `.env.example` 为 `.env` 并填入你的通义千问 API Key：

```bash
cp .env.example .env
# 编辑 .env，填入 DASHSCOPE_API_KEY
```

API Key 申请：[阿里云百炼控制台](https://bailian.console.aliyun.com/)

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

# 翻译单个 jar
python -m mc_translator_mcp mod <jar_path> [--batch-size 15] [--force-retranslate]

# 批量翻译目录下所有 jar
python -m mc_translator_mcp dir <directory> [--glob "*.jar"] [--batch-size 15]
```

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

## 核心模块

| 模块 | 功能 |
|------|------|
| `jar_parser.py` | 解析 jar 包结构，发现语言文件 |
| `lang_parser.py` | 解析 .json / .lang / .properties 格式语言文件 |
| `translator.py` | 通义千问批量翻译 + 本地缓存 |
| `pack_builder.py` | 生成资源包或改写 jar |
| `mcp_server.py` | MCP 服务器入口，暴露两个工具 |

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
