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
- **研究主题** (`project.research_topic`): 务必修改为你的实际研究方向，这直接影响 AI 筛选判断标准！
- 模型使用、路径定义、打分权重和阈值等。

### Notion 数据库配置
请在 Notion 中创建一个 Database，并配置如下字段：

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
2. **Human Review**: Filter `Status` = "Need Human Review", Sort by `Priority Score` descending
3. **Must Read Queue**: Filter `Human Decision` = "Must Read"
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
paperflow translate-one --zotero-key ZOTERO_KEY --dry-run
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
paperflow fetch-must-read
paperflow queue-translation
paperflow translate-queued
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

## 常见错误排查
- 找不到 PDF：请检查 `Zotero` 是否已完整同步附件到本地；如果项目跑在云端，可能需要使用云存储映射或手动设定 Local PDF Path。
- Notion 写入失败：请确保你的 Integration Token 正确，且该 Integration 已经被 Invite/Add 到你创建的那个 Database 里了。
- pdf2zh 翻译超时：如果论文特别长，在 `config.yaml` 里可以适当调长 `translation.pdf2zh.timeout_minutes`。
