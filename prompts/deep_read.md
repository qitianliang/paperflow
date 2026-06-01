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

**批判性阅读要求**：

在精读过程中，请特别关注以下内容：

1. **贡献声明验证**：
   - 作者在摘要和引言中声称的主要贡献是什么？
   - 正文中的方法、实验和结果是否真正支撑了这些贡献？
   - 是否存在"声明多、证据少"的情况？

2. **方法溯源**：
   - 该方法的核心技术来源是什么？
   - 与最相近的已有工作相比，实质性的技术增量是什么？
   - 这个增量是"组合已有组件"还是"提出新机制"？

3. **实验-声明一致性**：
   - 实验设计是否足以支撑作者的核心声明？
   - 是否有消融实验、对比实验、鲁棒性实验等充分验证？
   - 是否存在"声称解决了重要问题但实验规模很小"的情况？

4. **灌水信号检测**：
   - 是否大量使用"首次"、"开创性"但缺乏技术细节？
   - 方法部分是否只是现有技术的简单组合？
   - 相关工作部分是否刻意贬低前人工作？
   - 核心贡献是否不清晰？

5. **独立判断**：
   - 请用一句话概括该工作的"最小可行描述"（MVD）
   - 如果去掉所有修辞包装，该工作的技术实质是什么？
   - 你的独立判断是什么？

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
