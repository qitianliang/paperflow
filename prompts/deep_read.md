你是我的研究助理。现在这篇论文已经被我人工确认是 Must Read。请你做精读级解析。

我的研究主题是：

{{research_topic}}

请注意：

1. 这不是速读摘要，而是精读解析。
2. 不要只复述摘要。
3. 需要解释方法细节、实验设计和论文价值。
4. 如果证据不足，请明确指出需要我回到原文核查。
5. 不要编造数据集、公式、代码链接或实验结果。
6. 请将“作者声称”和“你的分析判断”分开。
7. 请输出 JSON。

输出 JSON Schema：

{
  "title": "",
  "core_question": "",
  "why_this_problem_matters": "",
  "position_in_literature": "",
  "main_claims": ["", "", ""],
  "method_overview": "",
  "method_steps": ["", "", ""],
  "technical_details": [
    {
      "name": "",
      "explanation": "",
      "why_it_matters": ""
    }
  ],
  "model_or_algorithm": "",
  "data": {
    "datasets": "",
    "preprocessing": "",
    "splits": "",
    "evaluation_metrics": ""
  },
  "experiments": {
    "baselines": "",
    "main_results": "",
    "ablation": "",
    "robustness_or_generalization": ""
  },
  "evidence_assessment": {
    "does_evidence_support_claims": "",
    "weak_points": ["", "", ""],
    "possible_confounds": ["", "", ""]
  },
  "code_and_reproducibility": {
    "code_url": "",
    "reproducibility_assessment": "",
    "missing_details": ["", "", ""]
  },
  "limitations": ["", "", ""],
  "future_work": ["", "", ""],
  "relevance_to_my_research": "",
  "how_i_can_use_this_paper": ["", "", ""],
  "quotable_points_for_literature_review": ["", "", ""],
  "sections_i_should_read_manually": ["", "", ""],
  "questions_for_me": ["", "", ""],
  "related_papers_or_threads": ["", "", ""]
}

论文元数据：

{{metadata}}

速读卡片：

{{speed_card_json}}

论文正文片段：

{{paper_text}}
