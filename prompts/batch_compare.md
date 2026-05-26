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
