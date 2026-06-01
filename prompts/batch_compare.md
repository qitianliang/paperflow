你是我的文献综述助理。现在我已经对一批同主题论文生成了速读卡片。请你对这一批论文做横向比较，帮助我决定优先精读顺序。

我的研究主题是：

{{research_topic}}

请基于以下每篇论文的速读卡片，输出 batch-level analysis。

请严格输出 JSON。

你需要完成：

1. 找出最值得优先精读的论文。
2. 找出只是需要快速浏览的论文。
3. 找出可以暂存的论文。
4. 找出可以排除的论文。
5. 按方法路线对论文分组。
6. 按数据集或任务场景分组。
7. 标出可能重复、增量较小或边缘相关的论文。
8. 标出 AI 置信度低、需要人工复核的论文。
9. 给出我应该优先看的 5-10 篇论文及理由。

**批判性评估要求**：

在比较过程中，请特别关注以下"灌水"信号，对存在这些信号的论文降低推荐优先级：

1. **夸大声明检测**：
   - 是否有多篇论文都声称"首次"解决同一问题？如果是，标记为"重复声明"
   - 是否有论文的 novelty_score 高但 key_evidence 中缺乏技术细节？
   - 是否有论文的 claimed_contributions 与 actual_evidence 严重不匹配？

2. **方法同质化检测**：
   - 是否有多篇论文使用几乎相同的方法框架，只是换了数据集或应用场景？
   - 如果是，只推荐其中最有价值的一篇，其他标记为"方法同质化"

3. **实验不足检测**：
   - 是否有论文声称解决了重要问题但实验规模很小？
   - 是否有论文只在标准数据集上做了简单对比，缺乏深入分析？

4. **增量贡献评估**：
   - 对于同一方法路线的多篇论文，评估每篇的增量贡献
   - 如果某篇论文只是前作的简单扩展，标记为"增量有限"

5. **去重建议**：
   - 对于方法高度相似的论文组，建议只精读 1-2 篇最具代表性的
   - 其他论文可以标记为"Park"或"Exclude"

输出 JSON Schema：

{
  "batch_summary": "",
  "recommended_must_read": [
    {
      "title": "",
      "zotero_key": "",
      "reason": "",
      "risk": "",
      "confidence": "High / Medium / Low"
    }
  ],
  "recommended_scan": [
    {
      "title": "",
      "zotero_key": "",
      "reason": ""
    }
  ],
  "recommended_park": [
    {
      "title": "",
      "zotero_key": "",
      "reason": ""
    }
  ],
  "recommended_exclude": [
    {
      "title": "",
      "zotero_key": "",
      "reason": ""
    }
  ],
  "method_groups": [
    {
      "group_name": "",
      "papers": ["zotero_key"],
      "description": ""
    }
  ],
  "dataset_or_task_groups": [
    {
      "group_name": "",
      "papers": ["zotero_key"],
      "description": ""
    }
  ],
  "low_confidence_need_human_check": [
    {
      "title": "",
      "zotero_key": "",
      "reason": ""
    }
  ],
  "suggested_reading_order": [
    {
      "rank": 1,
      "title": "",
      "zotero_key": "",
      "why_first": ""
    }
  ]
}

输入的速读卡片如下：

{{speed_cards_json}}
