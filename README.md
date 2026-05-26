# paperflow

paperflow 是一套从 Zotero 收集论文，利用大模型自动生成速读卡片，推送到 Notion 看板，并在此基础上通过人工确认筛选 Must Read 论文，继而自动化批量翻译 PDF、AI 精读并导出到 Obsidian 的完整文献工作流工具。

## 项目用途

实现 Zotero → AI 速读卡片 → Notion 看板筛选 → 人工确认 Must Read → 批量 PDF 翻译 → AI 精读 → Obsidian 文献笔记 → 可选挂回 Zotero 附件 的完整文献工作流。

## 整体流程图

1. Save to Zotero 收集论文元数据和 PDF 附件。
2. paperflow 从指定 Zotero Collection 拉取信息。
3. 调用 AI（默认 DeepSeek）生成速读卡片。
4. 将速读卡片同步到 Notion database 看板。
5. 人工在 Notion 中将需要深入阅读的论文的 Human Decision 修改为 Must Read。
6. 对于 Must Read 的论文，自动创建 PDF 翻译队列。
7. 调用 pdf2zh CLI 批量翻译，生成单语和双语 PDF。
8. 翻译完成后，更新 Notion 状态，并将翻译文件本地保存（可选挂回 Zotero）。
9. AI 对 Must Read 论文进行深入精读解析。
10. 最终导出带有原文、译文链接及深入解析的 Obsidian Markdown 笔记。

## AI 筛选判断机制

速读卡片用于安排阅读优先级，不替代人工判断。系统使用五项 `1-5` 分指标：

| 指标 | 含义 | 高分要求 |
| --- | --- | --- |
| `topic_relevance_score` | 与当前研究问题直接相关程度 | 任务或核心问题高度一致 |
| `method_relevance_score` | 方法能否复用或作为关键比较 | 可直接采用或重要对照 |
| `data_relevance_score` | 数据、场景、评估是否可参考 | 同类数据或实验可对齐 |
| `novelty_score` | 相对常规方法的新增贡献 | 有明确且可核查的新方法/发现 |
| `reproducibility_score` | 复现与复核可行性 | 实验细节充分，最好有代码/数据 |

`prompts/speed_card.md` 明确要求：子评分只能为整数 `1-5`，任何 `4/5`
分必须给出 `key_evidence`，仅摘要或证据不足时不得输出 `High`
置信度。程序端还会再次执行保护：

- 根据 `screening.score_weights` 重算 `Priority Score`，不信任模型自报总分。
- 根据 `suggestion_thresholds` 重算 `AI Suggestion`，值只可能为
  `Must Read / Scan / Park / Exclude`。
- 无关键证据时将 `Confidence` 降为 `Low`；`Low` 置信度不能自动给出
  `Must Read` 建议。
- prompt、模型、主题、论文元数据、抽取设置、评分权重或阈值变更后，
  已缓存 speed card 会自动失效并重新生成。

建议用法：先按 `Priority Score` 排序，再优先人工核查
`Confidence=Low` 或 `Risk / Need Check` 明显的论文；只把人工确认后的
条目设为 `Human Decision=Must Read`。

## 自动精读 Top N

可在 `config.yaml` 中启用按加权总分自动选择精读对象：

```yaml
deep_read_selection:
  enabled: true
  top_n: 10
  include_human_must_read: true

screening:
  max_concurrent_papers: 3
```

`paperflow deep-read` 与 `paperflow export-obsidian` 会处理
`Priority Score` 最高的前 N 篇，并合并人工标记的 `Human Decision=Must Read`
条目后去重。自动选择不会改写人工判断，也不会自动进入 PDF 翻译队列；
翻译始终由人工 `Must Read` 控制。

生产运行可将 `speed_card`/`obsidian_note` 路由到 `balanced` 模型，将
`deep_read` 路由到 `strong` 模型。例如 BLSC 兼容端点下使用
`DeepSeek-V4-Flash` 做批量筛选和整理，使用 `DeepSeek-V4-Pro` 做 Top N
精读。

大 collection 可用 `screening.max_concurrent_papers` 并行处理 PDF 抽取与
speed card 请求；Notion 仍按顺序同步，减少并发写入冲突。建议从 `2-3`
开始，并按模型端点限流情况调整。
对 `strong` 模型执行长文精读时，若端点在默认时限内不能返回结果，可将
`llm.request_timeout_seconds` 提高到 `300-600` 秒。

## Conda 安装步骤

1. 创建环境：
```bash
conda create -n paperflow python=3.11 -y
conda activate paperflow
```

2. 安装项目：
```bash
pip install -r requirements.txt
pip install -e .
```

3. 配置环境变量：
```bash
cp .env.example .env
```

4. 配置 config：
```bash
cp config.example.yaml config.yaml
```

5. 检查环境：
```bash
paperflow doctor
```

## 配置指南

### .env 配置说明
- 填入对应服务的 API Key 和 Token（DeepSeek/OpenAI/Zotero/Notion）。
- `ZOTERO_USER_ID`: 你的 Zotero 用户 ID，可在 Zotero 账户设置 API Keys 页面找到。
- `ZOTERO_API_KEY`: Zotero API Key。
- `NOTION_TOKEN`: 你的 Notion 内部集成 Secret Token。
- `NOTION_DATABASE_ID`: 你的 Notion 目标 Database ID。
- `OBSIDIAN_VAULT_PATH`: Obsidian Vault 的绝对路径。
- `PDF2ZH_EXECUTABLE`: 默认 `pdf2zh`，如果需要指定路径可填入绝对路径。

### config.yaml 配置说明
你可以基于 `config.example.yaml` 复制为 `config.yaml`。其中定义了你的：
- **研究主题** (`project.topics` 与 `project.active_topic`): 每个主题绑定 Zotero
  collection、Notion `Topic` 标签与 Obsidian 子目录；当前主题直接影响 AI 判断标准。
- 模型使用、路径定义、打分权重和阈值等。

### 多研究主题

默认推荐共用一个 Notion database/data source，通过 `Topic` 字段创建过滤视图；
不同主题对同一篇论文会保留独立页面和独立 AI 判断。只有需要不同成员权限或完全
不同字段结构时，才拆分数据库。

Obsidian 与本地中间产物按主题隔离：

```text
data/cache/<topic>/
data/translation_staging/<topic>/
Literature/PDFs/Translated/<topic>/
<vault>/Literature/Notes/<topic>/
```

完整配置示例见 [MULTI_TOPIC.md](MULTI_TOPIC.md)。

### Notion 数据库配置
Notion API `2025-09-03` 起通过 data source 查询与更新表结构。配置
`NOTION_DATABASE_ID` 后，可运行以下命令补齐字段：

```bash
python scripts/align_notion.py
```

Notion 列中展示高信号筛选字段；每篇页面正文内会写入可展开的
`AI Details (paperflow)`，包含摘要、方法、主要证据、局限与需核查点。
主要字段如下：

**基础信息：**
- `Title` (title): 标题
- `Authors` (rich_text): 作者
- `Year` (number): 年份
- `Venue` (rich_text): 发表地点
- `DOI` (rich_text): DOI
- `URL` (url): 原文链接
- `Zotero Key` (rich_text): Zotero 唯一标识
- `Citation Key` (rich_text): 引用键（Better BibTeX）
- `Zotero Link` (url): Zotero 客户端跳转链接
- `Local PDF Path` (rich_text): 本地 PDF 路径
- `PDF Status` (select): Has PDF / No PDF / Unknown

**筛选字段：**
- `Topic` (multi_select): 主题
- `Method` (rich_text): 方法
- `Dataset` (rich_text): 数据集
- `Code URL` (url): 代码地址
- `AI Suggestion` (select): Must Read / Scan / Park / Exclude
- `Human Decision` (select): Unreviewed / Must Read / Scan / Park / Exclude
- `Status` (select): Collected / Speed Card Done / Need Human Review / Must Read Confirmed / Translation Queued / Translation Done / Deep Reading Done / Exported to Obsidian / Park / Exclude
- `Confidence` (select): High / Medium / Low
- `Priority Score` (number): 优先级得分
- `Relevance Score` (number): 相关性得分
- `Novelty Score` (number): 创新性得分
- `Reproducibility Score` (number): 可复现性得分
- `One-line Innovation` (rich_text): 一句话创新点
- `Research Problem` (rich_text): 研究问题
- `Summary CN` (rich_text): 中文摘要
- `Summary EN` (rich_text): 英文摘要
- `Key Evidence` (rich_text): 关键证据
- `Risk / Need Check` (rich_text): 风险/需核查点
- `AI Raw JSON` (rich_text): AI 返回的原始数据
- `Last AI Update` (date): 最后更新时间

**翻译字段：**
- `Translation Needed` (checkbox): 是否需要翻译
- `Translation Status` (select): Not Needed / Queued / Running / Done / Failed / Skipped
- `Translation Engine` (select): pdf2zh / pdf2zh_next
- `Translation Service` (rich_text): 翻译服务商
- `Translated Dual PDF` (rich_text): 双语 PDF 路径
- `Translated Mono PDF` (rich_text): 单语 PDF 路径
- `Translation Error` (rich_text): 翻译报错
- `Translation Updated At` (date): 翻译更新时间
- `Translation Retry Count` (number): 重试次数
- `Zotero Attachment Status` (select): Not Attached / Attached / Failed / Skipped
- `Zotero Attachment Error` (rich_text): 附件上传报错

**Obsidian 字段：**
- `Obsidian Note Path` (rich_text): 笔记路径
- `Exported At` (date): 导出时间

#### 推荐视图（Views）配置：
1. **Screening Board**: Group by `Status`
2. **Human Review**: Filter `Topic` = current topic, Sort by `Priority Score` descending
3. **Must Read Queue**: Filter `Topic` = current topic and `Human Decision` = "Must Read"
4. **Translation Queue**: Filter `Translation Status` = "Queued"
5. **Translation Failed**: Filter `Translation Status` = "Failed"
6. **Low Confidence Check**: Filter `Confidence` = "Low"
7. **Exported to Obsidian**: Filter `Status` = "Exported to Obsidian"

### DeepSeek 与 OpenAI 配置
默认通过 DeepSeek 获取服务，如果需要可以使用 OpenAI。
注意：不要在代码中硬编码模型名称。可以在 `config.yaml` 里面的 `llm.providers` 自定义或修改模型。如果 DeepSeek 被用作兼容 OpenAI 的 Provider，请确保其 `base_url` 正确（如 `https://api.deepseek.com` 或 `https://api.deepseek.com/v1`）。

### pdf2zh CLI 配置与手动测试
pdf2zh 将通过 subprocess 调用，不会直接把 API KEY 写入文件，而是通过环境变量传递给配置。
手动测试翻译：
```bash
pdf2zh test.pdf -s openai:deepseek-chat -t 1 -o output
```
如果使用兼容 OpenAI 的端点作为模型，请确保配置 `base_url` 与模型名。可以在 `config.yaml` 中配置相关的 service/model 等参数。

## 运行工作流

1. **测试连接**：
```bash
paperflow fetch-zotero --collection COLLECTION_ID
paperflow sync-notion --collection COLLECTION_ID --dry-run
paperflow doctor
```

2. **完整速读流程**（获取文献，AI打分生成速读卡片，推送到 Notion）：
```bash
paperflow run-screening --collection COLLECTION_ID
```
之后，在 Notion 看板里，人工判定 `Human Decision` 列，选出需要精读的文章标为 **Must Read**。

3. **Must Read 后续流程**（为 Must Read 论文拉取元数据、排队翻译、精读、导出到 Obsidian）：
```bash
paperflow run-must-read
```
或者分步骤执行：
```bash
paperflow queue-translation
paperflow translate-queued   # 仅在需要翻译时执行
paperflow sync-translation-status
paperflow deep-read
paperflow export-obsidian
```

### 可选挂回 Zotero 附件
默认情况下，翻译后的 PDF 只存在于本地，并在 Obsidian 笔记中做关联。你可以在 `config.yaml` 中设置 `zotero.attachment_strategy.upload_translated_pdf_to_zotero: true` 来将其作为子附件挂载回 Zotero 原始条目中。
*提示：上传到 Zotero 可能会占用你的 Zotero 文件存储空间。*

## 设计原则声明
- **Human Decision 优先级**：为什么默认不覆盖 `Human Decision`？因为自动化工具只是辅助筛选，学术决断必须且永远属于人类研究者。
- **保护原始文件**：为什么默认不修改 Zotero 原始 PDF？防止因翻译失败或格式破坏导致原文不可用，项目采用先 Copy 至缓存区（staging），再处理并输出到指定文件夹的策略。
- **少写外部系统**：Notion 同步和 Obsidian 导出保存内容指纹，内容未变化时跳过重复写入或 LLM 格式化。
- **可恢复执行**：速读和精读按输入签名缓存；中途失败后再次执行，仅重做需要更新的步骤。

## 常见错误排查
- 找不到 PDF：请检查 `Zotero` 是否已完整同步附件到本地；如果项目跑在云端，可能需要使用云存储映射或手动设定 Local PDF Path。
- Notion 写入失败：确认 Integration 已添加到 Database；如本地代理造成 TLS
  断连，将 `notion.use_environment_proxy` 设置为 `false`。
- pdf2zh 翻译超时：如果论文特别长，在 `config.yaml` 里可以适当调长 `translation.pdf2zh.timeout_minutes`。

## 已实现的紧要优化

- 带评分锚点与证据门槛的 speed card prompt，以及程序端的总分、建议和置信度校正。
- PDF 文本长度限制与头/中/尾采样，降低输入超限及只看引言带来的偏差。
- speed card、deep read、Notion、Obsidian 的变更感知缓存与幂等同步。
- Notion data source API 适配、网络重试、可配置代理绕过。
- 单库多主题隔离及只清当前主题的测试清理脚本。

## 增值扩展路线图

优先级较高但不阻塞当前使用：

1. 引用定位：在 `key_evidence` 中记录页码/章节/原文短句，支持从 Notion
   直接返回 PDF 证据位置。
2. 批量筛选校准：抽取一批人工标注 `Must Read/Exclude` 样本，评估阈值与
   权重的 precision/recall，再为不同主题配置权重。
3. 运行账本：增加 collection-level run manifest，集中展示失败论文、
   API 成本、耗时与可重试步骤。

后续增值能力：

1. 相似论文聚类、方法/数据集图谱和 Zotero related-items 回写。
2. 引用管理联动：生成综述段落素材时携带 citation key 与证据出处。
3. 可选并发队列与速率限制器，在批量导入时平衡吞吐、API 限流和费用。
