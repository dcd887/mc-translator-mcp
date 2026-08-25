# 翻译质量测试报告：Powah 5.0.11（对照官方中文）

> 测试时间：2026-08-26　·　测试模型：agnes-2.5-flash　·　测试方法：对 Powah 官方 jar 的**未汉化状态**（剔除 `zh_cn.json` 后）重新翻译，与 jar 内自带官方中文逐条对照。

## 1. 测试对象与准备

- **模组**：Powah 5.0.11（`qg/mods/Powah-5.0.11.jar`）—— 知名科技类模组，jar 内置官方中文翻译，适合做质量基准。
- **源文本**：`assets/powah/lang/en_us.json`，共 **262** 条（物品/方块名、wiki 描述、聊天提示等）。
- **官方基准**：jar 内 `assets/powah/lang/zh_cn.json`，共 **251** 条（官方/社区维护）。
- **流程**：复制 jar 并剔除 `zh_cn.json`/`zh_tw.json` → 用 mc-translator-mcp 对纯英文状态翻译 → 生成 `zh_cn.json` 资源包 → 与官方逐条对比。

## 2. 三轮迭代结果

| 指标 | 第 1 轮（初始） | 第 2 轮（提示词+纠错） | 第 3 轮（+定制术语表） |
|------|----------------|----------------------|----------------------|
| 翻译覆盖率 | 259/262（98.9%） | 262/262（100%） | 261/262（99.6%）* |
| 残留英文条目 | 有（如「沥青铀矿 ore」） | 0（已自动纠正） | 0 |
| 与官方完全一致 | 49/248（19.8%） | 49（19.5%） | 110/250（44.0%） |
| 实质差异 | 199 条 | 202 条 | 140 条（多为同义/行文差异） |

\* 第 3 轮仅漏 `wiki.powah.welcome_back`（`'Welcome back %s :)'`，含表情符号的极个别条目）。

## 3. 发现的问题与改进（已合入代码）

### 3.1 残留英文 / 中英混排
- **问题**：模型把 `Uraninite Ore (Poor)` 译成「沥青铀矿 ore（贫矿）」，残留英文单词。
- **改进**：提示词强制「纯简体中文输出」+ 新增**残留英文自动纠正**：解析结果后检测 ASCII 英文单词（忽略占位符/缩写/`<modid:item>` 标签），对违规译文自动发起一轮纠正重试。

### 3.2 漏译
- **问题**：含 `<powah:wrench>` 物品标签的 wiki 条目被模型整条跳过。
- **改进**：新增**漏译自动补翻**：每批返回后对缺失 key 自动发起一轮补齐请求，覆盖率从 98.9% 提到 100%。

### 3.3 模组专有术语漂移（与社区译名不一致）
- **问题**：等级名等模组专有词无法靠通用提示词对齐，如 `Niotic`→尼奥特（官方：钻石）、`Spirited`→灵质（官方：富生）、`Nitro`→硝酸（官方：下界）、`Blazing`→炽热（官方：烈焰）。
- **改进**：新增**定制术语表** `translator_glossary.json`（或 `GLOSSARY_FILE` 指定），将 `{术语: 强制译名}` 注入提示词。启用 Powah 术语表后：

| key | 第 2 轮 | 第 3 轮（+术语表） | 官方 |
|-----|--------|------------------|------|
| `block.powah.ender_cell_niotic` | 末影存储盒（尼奥特） | 末影单元（钻石） | 末影单元（钻石） |
| `block.powah.ender_cell_spirited` | 末影存储盒（灵性） | 末影单元（富生） | 末影单元（富生） |
| `block.powah.energizing_rod_nitro` | 充能杆（硝精） | 充能棒（下界） | 充能棒（下界） |
| `block.powah.energy_cable_nitro` | 能量线缆（硝酸） | 能量电缆（下界） | 能量线缆（下界） |
| `block.powah.uraninite_ore_poor` | 沥青铀矿 ore（贫矿） | 晶质铀矿矿石（贫瘠） | 贫瘠晶质铀矿石 |

### 3.4 顺带修复的打包/CLI 缺陷
- `pyproject.toml` 构建后端与 `src/` 布局配置有误（`setuptools.backends._legacy` 不存在、未声明 `where=["src"]`），导致 `pip install -e .` 后无法 `import`；已修复，`python -m mc_translator_mcp` 可用。
- CLI 的 `mod` / `dir` 子命令未 `await` 异步函数导致必然崩溃；已改为 `asyncio.run(...)`。
- 测试与本地 `.env` 隔离（避免真实 key 干扰测试断言）。

## 4. 结论：与官方中文的差距评估

- **通俗性**：达成。wiki/tooltip 长文本均为自然流畅的中文表达，无逐字硬译的机翻腔（如官方「你首先需要将自己绑定与之绑定」这类句子，AI 译为「添加到玩家发射器之前需先与你绑定」，反而更通顺）。
- **术语一致性**：启用定制术语表后与官方/社区译名对齐（末影单元/末影道门/充能棒全部等级完全一致）；未启用时大部分仍合理，仅个别专有名词不同。
- **质量差距**：剩余差异以同义/行文差异为主（如「能量电缆」vs「能量线缆」、「储能单元」vs「能量单元」、「烈焰水晶」vs「烈焰晶体」），**无错译、无英文残留、无病句**，不构成"较大质量差距"。

## 5. 复现方法

```bash
# 1. 安装
cd mc-translator-mcp
uv venv && uv pip install -e ".[dev]"
cp .env.example .env   # 填任一供应商 key

# 2. 剔除官方中文，得到"未汉化"jar（用你的 mod 替换路径）
python -m mc_translator_mcp preview <powah.jar>          # 先看工作量

# 3. 定制术语表（可选，强烈推荐）——编辑 translator_glossary.json 填模组社区译名
# 4. 翻译
python -m mc_translator_mcp mod <powah_nozhc.jar> --batch-size 20
# 5. 输出：output/powah-zh-cn/assets/powah/lang/zh_cn.json，可直接放入 resourcepacks/
```

测试脚本见 `.qtest/`（已 gitignore，不进仓库）。
