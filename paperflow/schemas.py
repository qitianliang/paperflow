from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict, Literal
from datetime import datetime


class PaperMetadata(BaseModel):
    zotero_key: str
    title: str
    authors: str
    year: Optional[int] = None
    venue: str = ""
    doi: str = ""
    url: str = ""
    citation_key: str = ""
    zotero_link: str = ""
    abstract: str = ""
    tags: List[str] = Field(default_factory=list)
    has_pdf: bool = False
    pdf_attachment_key: str = ""
    local_pdf_path: str = ""


class SpeedCard(BaseModel):
    title: str = ""
    summary_en: List[str] = Field(default_factory=list)
    summary_zh: List[str] = Field(default_factory=list)
    one_line_innovation: str = ""
    research_problem: str = ""
    core_method: str = ""
    technical_details: List[str] = Field(default_factory=list)
    dataset: str = ""
    preprocessing: str = ""
    baselines: str = ""
    code_url: str = ""
    main_contributions: List[str] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    future_work: List[str] = Field(default_factory=list)

    topic_relevance_score: int = Field(default=1, ge=1, le=5)
    method_relevance_score: int = Field(default=1, ge=1, le=5)
    data_relevance_score: int = Field(default=1, ge=1, le=5)
    novelty_score: int = Field(default=1, ge=1, le=5)
    reproducibility_score: int = Field(default=1, ge=1, le=5)
    priority_score: float = Field(default=1.0, ge=1, le=5)

    ai_suggestion: Literal["Must Read", "Scan", "Park", "Exclude"] = "Scan"
    confidence: Literal["High", "Medium", "Low"] = "Medium"
    key_evidence: List[str] = Field(default_factory=list)
    risk_need_check: List[str] = Field(default_factory=list)
    recommended_human_action: str = ""

    # 创新性评估的额外字段
    novelty_validation: List[str] = Field(default_factory=list)  # 对创新性的具体质疑或验证说明
    claimed_contributions: List[str] = Field(default_factory=list)  # 论文中声称的贡献
    actual_evidence: List[str] = Field(default_factory=list)  # 实际找到的证据
    skepticism_flags: List[str] = Field(default_factory=list)  # 发现的灌水信号或质疑点


class DeepReadResult(BaseModel):
    """Result from deep reading a paper."""
    title: str = ""
    core_question: str = ""
    why_this_problem_matters: str = ""
    position_in_literature: str = ""
    main_claims: List[str] = Field(default_factory=list)
    method_overview: str = ""
    method_steps: List[str] = Field(default_factory=list)
    technical_details: List[Dict[str, str]] = Field(default_factory=list)
    model_or_algorithm: str = ""
    data: Dict[str, str] = Field(default_factory=dict)
    experiments: Dict[str, str] = Field(default_factory=dict)
    evidence_assessment: Dict[str, Any] = Field(default_factory=dict)
    code_and_reproducibility: Dict[str, Any] = Field(default_factory=dict)
    limitations: List[str] = Field(default_factory=list)
    future_work: List[str] = Field(default_factory=list)
    relevance_to_my_research: str = ""
    how_i_can_use_this_paper: List[str] = Field(default_factory=list)
    quotable_points_for_literature_review: List[str] = Field(default_factory=list)
    sections_i_should_read_manually: List[str] = Field(default_factory=list)
    questions_for_me: List[str] = Field(default_factory=list)
    related_papers_or_threads: List[str] = Field(default_factory=list)


class BatchCompareItem(BaseModel):
    """A single item in batch compare recommendation."""
    title: str = ""
    zotero_key: str = ""
    reason: str = ""
    risk: str = ""
    confidence: str = ""


class BatchCompareGroup(BaseModel):
    """A group in batch compare."""
    group_name: str = ""
    papers: List[str] = Field(default_factory=list)
    description: str = ""


class ReadingOrderItem(BaseModel):
    rank: int = 1
    title: str = ""
    zotero_key: str = ""
    why_first: str = ""


class BatchCompareResult(BaseModel):
    """Result from batch comparison of papers."""
    batch_summary: str = ""
    recommended_must_read: List[Dict[str, str]] = Field(default_factory=list)
    recommended_scan: List[Dict[str, str]] = Field(default_factory=list)
    recommended_park: List[Dict[str, str]] = Field(default_factory=list)
    recommended_exclude: List[Dict[str, str]] = Field(default_factory=list)
    method_groups: List[Dict[str, Any]] = Field(default_factory=list)
    dataset_or_task_groups: List[Dict[str, Any]] = Field(default_factory=list)
    low_confidence_need_human_check: List[Dict[str, str]] = Field(default_factory=list)
    suggested_reading_order: List[Dict[str, Any]] = Field(default_factory=list)


class TranslationQueueItem(BaseModel):
    """An item in the translation queue."""
    zotero_key: str
    page_id: str = ""
    citation_key: str = ""
    staged_path: str = ""
    status: str = "pending"  # pending, running, done, failed, skipped
    mono_pdf: str = ""
    dual_pdf: str = ""
    error: str = ""
    retry_count: int = 0
    started_at: str = ""
    finished_at: str = ""


class LLMCallRecord(BaseModel):
    """Record of a single LLM API call for cache/audit."""
    task: str = ""
    provider: str = ""
    model: str = ""
    prompt_preview: str = ""
    raw_response: str = ""
    parsed_json: Optional[Dict[str, Any]] = None
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    success: bool = True
    error: str = ""


class Pdf2zhCallRecord(BaseModel):
    """Record of a pdf2zh CLI invocation."""
    zotero_key: str = ""
    command: str = ""
    stdout: str = ""
    stderr: str = ""
    return_code: int = -1
    start_time: str = ""
    end_time: str = ""
