"""
Venue/Year 增强模块：多策略验证确保准确性

核心策略（四层 fallback）：
1. OpenAlex 快速查询 → venue 是知名会议/期刊则直接使用；仓库类型则继续
2. arXiv comment 字段 → 从作者标注提取 "Accepted to NeurIPS 2025" 等
3. OpenReview API → 查 NeurIPS/ICLR/ICML 等官方审稿平台的 accepted 状态
4. Semantic Scholar publicationVenue → 补充查询（有 429 限流风险）

关键洞察：
- 学术 API 对近期论文的 venue 元数据更新滞后（OpenAlex 把 NeurIPS 2025 标为 ArXiv.org）
- DeepSeek API 不支持联网搜索（enable_search 无效），不能作为兜底
- OpenReview 是 NeurIPS/ICLR/ICML 的官方平台，能查到 accepted 论文的真实 venue
- arXiv comment 字段有时包含 venue 信息，但作者不常填写
"""

import json
import re
import time
import xml.etree.ElementTree as ET
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

from paperflow.config import get_config
from paperflow.logging_utils import get_logger

load_dotenv()
logger = get_logger(__name__)

# ─── 直接连接 Session（绕过代理）───
SESSION_DIRECT = requests.Session()
SESSION_DIRECT.trust_env = False
SESSION_DIRECT.proxies = {"http": None, "https": None}

# ─── 仓库类型 venue 标识（需触发 fallback）───
REPOSITORY_VENUES = {
    "arxiv.org", "arxiv", "arxiv (cornell university)",
    "corr", "computing research repository",
    "preprint", "repository", "working paper",
    "ssrn", "social science research network",
}


@dataclass
class VenueYearResult:
    """Venue/Year 查询结果"""
    venue: str = ""
    year: Optional[int] = None
    confidence: float = 0.0
    sources: List[str] = field(default_factory=list)
    github_url: Optional[str] = None
    arxiv_id: Optional[str] = None
    notes: str = ""
    queried_at: Optional[datetime] = None


class VenueYearCache:
    """Venue/Year 查询缓存"""

    def __init__(self, cache_file: str = "data/venue_year_cache.json"):
        self.cache_file = Path(cache_file)
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self._data: Dict[str, dict] = {}
        self._load()

    def _load(self):
        """加载缓存"""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load venue/year cache: {e}")
                self._data = {}

    def _save(self):
        """保存缓存"""
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save venue/year cache: {e}")

    def get(self, title: str) -> Optional[VenueYearResult]:
        """获取缓存结果"""
        key = title.lower().strip()
        if key in self._data:
            data = self._data[key]
            # 检查缓存是否过期（30天）
            queried_at = datetime.fromisoformat(data.get("queried_at", "2000-01-01"))
            if datetime.now() - queried_at < timedelta(days=30):
                return VenueYearResult(**data)
        return None

    def set(self, title: str, result: VenueYearResult):
        """设置缓存"""
        key = title.lower().strip()
        self._data[key] = {
            "venue": result.venue,
            "year": result.year,
            "confidence": result.confidence,
            "sources": result.sources,
            "github_url": result.github_url,
            "arxiv_id": result.arxiv_id,
            "notes": result.notes,
            "queried_at": datetime.now().isoformat(),
        }
        self._save()


class AcademicAPIs:
    """学术 API 查询层 — 快速获取元数据，判断是否需要 fallback"""

    def __init__(self):
        self.session = SESSION_DIRECT
        # OpenReview 请求间隔（避免并发过多）
        self._openreview_last_request = 0.0
        self._openreview_min_interval = 2.0  # 秒

    # ─── OpenAlex ───

    def query_openalex(self, title: str) -> Optional[Dict]:
        """OpenAlex — 按标题搜索，返回 venue/year/arxiv_id/doi
        
        优先返回有可信 venue（会议/期刊）的结果，
        而不是没有 venue 或 repository 类型的结果。
        """
        url = "https://api.openalex.org/works"
        params = {"filter": f"title.search:{title}", "per_page": 5}
        try:
            resp = self.session.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                logger.debug(f"OpenAlex HTTP {resp.status_code}")
                return None

            data = resp.json()
            results = data.get("results", [])
            if not results:
                return None

            # 优先选择有可信 venue 的结果，而非 venue=None 或 repository 的
            # 策略：标题完全匹配(overlap=1.0) > 标题部分匹配但有可信 venue
            # 在标题完全匹配中，优先选有可信 venue 的；如果都 venue=None，选第一个
            best_work = None
            best_priority = -1
            
            def compute_priority(work):
                """计算结果优先级
                标题完全匹配 + 有可信 venue = 4 (最高)
                标题完全匹配 + 有任意 venue = 3
                标题完全匹配 + repository/None = 2
                标题部分匹配 + 有可信 venue = 1
                其他 = 0 (不考虑)
                """
                work_title = (work.get("title") or "").lower().strip()
                title_lower = title.lower().strip()
                
                # 计算标题重叠率
                if title_lower == work_title:
                    overlap = 1.0
                elif title_lower in work_title or work_title in title_lower:
                    overlap = 0.85
                else:
                    query_words = set(title_lower.split())
                    work_words = set(work_title.split())
                    overlap = len(query_words & work_words) / max(len(query_words), len(work_words), 1)
                
                # overlap < 0.7 → 不考虑
                if overlap < 0.7:
                    return -1
                
                primary_loc = work.get("primary_location", {}) or {}
                src_obj = primary_loc.get("source", {}) or {}
                venue = src_obj.get("display_name")
                venue_type = src_obj.get("type")
                is_repo = venue_type in ("repository", "preprint") or \
                          (venue and venue.lower() in REPOSITORY_VENUES)
                
                if overlap == 1.0:
                    if venue and not is_repo and venue_type in ("journal", "conference", "proceedings"):
                        return 4  # 完全匹配 + 可信 venue
                    elif venue and not is_repo:
                        return 3  # 完全匹配 + 任意 venue
                    else:
                        return 2  # 完全匹配 + repository/None
                elif overlap >= 0.7:
                    if venue and not is_repo and venue_type in ("journal", "conference", "proceedings"):
                        return 1  # 部分匹配 + 可信 venue
                    else:
                        return 0  # 部分匹配 + 其他
                return -1
            
            for work in results:
                priority = compute_priority(work)
                if priority > best_priority:
                    best_priority = priority
                    best_work = work

            # 如果没有找到有 venue 的结果，用第一个结果作为 fallback
            if best_work is None or best_priority < 0:
                best_work = results[0]

            work = best_work
            primary_loc = work.get("primary_location", {}) or {}
            src_obj = primary_loc.get("source", {}) or {}
            ids = work.get("ids") or {}
            doi = work.get("doi", "") or ""

            # 从 DOI 提取 arXiv ID
            arxiv_id = ids.get("arxiv")
            if not arxiv_id and "arxiv" in doi.lower():
                m = re.search(r'arxiv\.(\d+\.\d+)', doi)
                if m:
                    arxiv_id = m.group(1)

            return {
                "title": work.get("title"),
                "year": work.get("publication_year"),
                "venue": src_obj.get("display_name"),
                "venue_type": src_obj.get("type"),
                "type": work.get("type"),
                "doi": doi,
                "arxiv_id": arxiv_id,
                "is_repository": src_obj.get("type") in ("repository", "preprint") or
                                  (src_obj.get("display_name", "").lower() in REPOSITORY_VENUES),
            }
        except Exception as e:
            logger.debug(f"OpenAlex query failed: {e}")
            return None

    def query_openalex_doi(self, doi: str) -> Optional[Dict]:
        """OpenAlex — 按 DOI 精确查询"""
        url = "https://api.openalex.org/works"
        params = {"filter": f"doi:{doi}", "per_page": 1}
        try:
            resp = self.session.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                return None

            data = resp.json()
            results = data.get("results", [])
            if not results:
                return None

            work = results[0]
            primary_loc = work.get("primary_location", {}) or {}
            src_obj = primary_loc.get("source", {}) or {}
            ids = work.get("ids") or {}

            return {
                "title": work.get("title"),
                "year": work.get("publication_year"),
                "venue": src_obj.get("display_name"),
                "venue_type": src_obj.get("type"),
                "arxiv_id": ids.get("arxiv"),
                "is_repository": src_obj.get("type") in ("repository", "preprint") or
                                  (src_obj.get("display_name", "").lower() in REPOSITORY_VENUES),
            }
        except Exception as e:
            logger.debug(f"OpenAlex DOI query failed: {e}")
            return None

    # ─── arXiv ───

    def query_arxiv_by_id(self, arxiv_id: str) -> Optional[Dict]:
        """arXiv — 按 ID 查询，提取 comment 字段中的 venue 信息"""
        url = "https://export.arxiv.org/api/query"
        params = {"id_list": arxiv_id, "max_results": 1}
        try:
            resp = self.session.get(url, params=params, timeout=20)
            if resp.status_code != 200:
                return None

            root = ET.fromstring(resp.text)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            entries = root.findall("atom:entry", ns)
            if not entries:
                return None

            entry = entries[0]
            comment_elem = entry.find("atom:comment", ns)
            comment = comment_elem.text.strip() if comment_elem is not None and comment_elem.text else None

            # 从 comment 提取 venue
            venue_from_comment = None
            year_from_comment = None
            if comment:
                patterns = [
                    r'(?:accepted|published|appeared|presented|to appear)\s+(?:to|at|in)\s+([A-Za-z]+)\s+(\d{4})',
                    r'(?:accepted|published|appeared|presented|to appear)\s+(?:to|at|in)\s+the\s+([A-Za-z]+)\s+(\d{4})',
                    r'(NeurIPS|ICML|ICLR|CVPR|ICCV|ECCV|AAAI|IJCAI|ACL|EMNLP|NAACL|SIGIR|CIKM|KDD|WWW|SIGMOD|VLDB|ICDE)\s+(\d{4})',
                ]
                for pattern in patterns:
                    m = re.search(pattern, comment, re.IGNORECASE)
                    if m:
                        venue_from_comment = m.group(1)
                        year_from_comment = int(m.group(2))
                        break

            return {
                "comment": comment,
                "venue_from_comment": venue_from_comment,
                "year_from_comment": year_from_comment,
                "has_venue_in_comment": venue_from_comment is not None,
            }
        except Exception as e:
            logger.debug(f"arXiv ID query failed: {e}")
            return None

    # ─── Crossref ───

    def query_crossref(self, doi: str) -> Optional[Dict]:
        """Crossref — 按 DOI 查询，返回 container-title (venue) 和 year

        Crossref 对正式发表的论文 venue 信息通常比 OpenAlex 更准确，
         especially for recently published papers where OpenAlex lags.
        """
        if not doi:
            return None
        # 清理 DOI（去掉 https://doi.org/ 前缀）
        clean_doi = doi.replace("https://doi.org/", "").replace("http://doi.org/", "").strip()
        url = f"https://api.crossref.org/works/{clean_doi}"
        try:
            resp = self.session.get(url, timeout=15)
            if resp.status_code != 200:
                logger.debug(f"Crossref HTTP {resp.status_code}")
                return None

            data = resp.json()
            msg = data.get("message", {})

            # container-title 是会议/期刊名称
            container_titles = msg.get("container-title", [])
            venue = container_titles[0] if container_titles else ""

            # 从 published-print 或 published-online 提取年份
            year = None
            for key in ("published-print", "published-online", "created"):
                date_parts = msg.get(key, {}).get("date-parts", [[]])
                if date_parts and date_parts[0] and date_parts[0][0]:
                    year = date_parts[0][0]
                    break

            # 从 event 提取会议名（有些会议论文在 event.name 中）
            event_name = ""
            event = msg.get("event", {})
            if isinstance(event, dict):
                event_name = event.get("name", "")

            # 出版商
            publisher = msg.get("publisher", "")

            # 类型
            work_type = msg.get("type", "")

            return {
                "venue": venue or event_name,
                "year": year,
                "publisher": publisher,
                "type": work_type,
                "doi": clean_doi,
            }
        except Exception as e:
            logger.debug(f"Crossref query failed: {e}")
            return None

    # ─── Semantic Scholar ───

    def query_semantic_scholar_arxiv(self, arxiv_id: str) -> Optional[Dict]:
        """Semantic Scholar — 按 arXiv ID 精确查询
        
        支持 429 rate limit 重试（最多3次，exponential backoff 10s→20s→30s）。
        """
        import time
        url = f"https://api.semanticscholar.org/graph/v1/paper/ArXiv:{arxiv_id}"
        params = {"fields": "title,venue,year,externalIds,publicationVenue"}
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                resp = self.session.get(url, params=params, timeout=15)
                if resp.status_code == 429:
                    wait_time = min(30, 10 * (2 ** attempt))
                    logger.debug(f"Semantic Scholar arxiv rate limited, retrying in {wait_time}s (attempt {attempt+1}/{max_retries})")
                    time.sleep(wait_time)
                    continue
                if resp.status_code != 200:
                    logger.debug(f"Semantic Scholar arxiv HTTP {resp.status_code}")
                    return None

                paper = resp.json()
                pub_venue = paper.get("publicationVenue", {}) or {}

                return {
                    "title": paper.get("title"),
                    "year": paper.get("year"),
                    "venue": paper.get("venue"),
                    "publicationVenue_name": pub_venue.get("name"),
                    "publicationVenue_type": pub_venue.get("type"),
                    "is_repository": pub_venue.get("type") in ("repository", "preprint") or
                                     (paper.get("venue", "").lower() in REPOSITORY_VENUES if paper.get("venue") else False),
                }
            except Exception as e:
                logger.debug(f"Semantic Scholar arxiv query failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(10)
                    continue
                return None

    def query_semantic_scholar_title(self, title: str) -> Optional[Dict]:
        """Semantic Scholar — 按标题精确匹配
        
        使用 /paper/search/match endpoint（精确标题匹配），只返回最佳匹配。
        比 /paper/search 更精确：找不到匹配返回 404，不返回不相关结果。
        有 rate limit（100次/5分钟），支持 429 重试（最多3次）。
        """
        import time
        # 使用精确标题匹配 endpoint
        url = "https://api.semanticscholar.org/graph/v1/paper/search/match"
        params = {
            "query": title,
            "fields": "title,venue,year,externalIds,publicationVenue",
        }
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                resp = self.session.get(url, params=params, timeout=15)
                if resp.status_code == 429:
                    wait_time = min(30, 10 * (2 ** attempt))
                    logger.debug(f"Semantic Scholar title match rate limited, retrying in {wait_time}s (attempt {attempt+1}/{max_retries})")
                    time.sleep(wait_time)
                    continue
                if resp.status_code == 404:
                    logger.debug(f"Semantic Scholar title match: no match found for '{title[:50]}...'")
                    return None
                if resp.status_code != 200:
                    logger.debug(f"Semantic Scholar title match HTTP {resp.status_code}")
                    return None
                data = resp.json()
                papers = data.get("data", [])
                if not papers:
                    return None
                # /paper/search/match 只返回最佳匹配，直接使用
                paper = papers[0]
                pub_venue = paper.get("publicationVenue", {}) or {}
                venue = paper.get("venue", "") or ""
                pub_venue_name = pub_venue.get("name", "") or ""
                pub_venue_type = pub_venue.get("type", "") or ""
                external_ids = paper.get("externalIds", {}) or {}
                
                return {
                    "title": paper.get("title"),
                    "year": paper.get("year"),
                    "venue": venue,
                    "publicationVenue_name": pub_venue_name,
                    "publicationVenue_type": pub_venue_type,
                    "arxiv_id": external_ids.get("ArXiv"),
                    "doi": external_ids.get("DOI"),
                    "is_repository": pub_venue_type in ("repository", "preprint") or
                                     (venue.lower() in REPOSITORY_VENUES if venue else False),
                }
            except Exception as e:
                logger.debug(f"Semantic Scholar title match request failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(10)
                    continue
                return None

    # ─── DBLP ───

    def query_dblp(self, title: str) -> Optional[Dict]:
        """DBLP — 按标题搜索，遍历结果找标题匹配且有可信 venue 的"""
        url = "https://dblp.org/search/publ/api"
        params = {"q": title, "h": 20, "format": "json"}
        try:
            resp = self.session.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                return None

            data = resp.json()
            hits = data.get("result", {}).get("hits", {}).get("hit", [])
            if not hits:
                return None

            # 遍历所有结果，优先选择标题完全匹配且有可信 venue 的
            best_hit = None
            best_priority = -1
            
            for hit in hits:
                info = hit.get("info", {})
                db_title = info.get("title", "") or ""
                db_venue = info.get("venue", "") or ""
                db_year = info.get("year")
                db_type = info.get("type", "")
                
                # 标题匹配度
                title_lower = title.lower().strip().rstrip('.')
                db_lower = db_title.lower().strip().rstrip('.')
                if title_lower == db_lower:
                    overlap = 1.0
                elif title_lower in db_lower or db_lower in title_lower:
                    overlap = 0.8
                else:
                    q_words = set(title_lower.split())
                    d_words = set(db_lower.split())
                    overlap = len(q_words & d_words) / max(len(q_words), len(d_words), 1)
                
                if overlap < 0.7:
                    continue
                
                is_repo = db_venue.lower() in REPOSITORY_VENUES
                
                # 优先级：完全匹配+可信venue=4, 完全匹配+任意venue=3,
                # 完全匹配+repo=2, 部分匹配+可信venue=1
                if overlap == 1.0:
                    if db_venue and not is_repo and db_type in ("Conference and Workshop Papers", "Journal Articles"):
                        priority = 4
                    elif db_venue and not is_repo:
                        priority = 3
                    else:
                        priority = 2
                elif overlap >= 0.7:
                    if db_venue and not is_repo:
                        priority = 1
                    else:
                        priority = 0
                
                if priority > best_priority:
                    best_priority = priority
                    best_hit = info

            if not best_hit or best_priority < 0:
                # fallback: 用第一个结果
                best_hit = hits[0].get("info", {})

            venue = best_hit.get("venue", "")
            return {
                "title": best_hit.get("title"),
                "year": best_hit.get("year"),
                "venue": venue,
                "type": best_hit.get("type"),
                "is_repository": venue.lower() in REPOSITORY_VENUES,
            }
        except Exception as e:
            logger.debug(f"DBLP query failed: {e}")
            return None

    # ─── OpenReview ───

    def query_openreview(self, title: str) -> Optional[Dict]:
        """
        OpenReview API — 查询 NeurIPS/ICLR/ICML 等会议的 accepted 论文
        
        OpenReview 是 NeurIPS/ICLR/ICML 的官方审稿平台，
        对近期论文（<1年）能提供最准确的 venue 信息。
        
        搜索策略：
        1. 先搜索标题匹配的 notes（论文提交）
        2. 检查 invitation（会议类型）和 cdate/odate（时间）
        3. 提取 venue 信息
        """
        import time
        # 限速
        elapsed = time.time() - self._openreview_last_request
        if elapsed < self._openreview_min_interval:
            time.sleep(self._openreview_min_interval - elapsed)
        self._openreview_last_request = time.time()

        url = "https://api2.openreview.net/notes/search"
        params = {
            "query": title,
            "limit": 5,
        }
        try:
            resp = self.session.get(url, params=params, timeout=20)
            if resp.status_code != 200:
                logger.debug(f"OpenReview HTTP {resp.status_code}")
                return None

            data = resp.json()
            notes = data.get("notes", [])
            if not notes:
                logger.debug("OpenReview: no notes found")
                return None

            best_match = None
            best_score = 0.0

            for note in notes:
                content = note.get("content", {})
                # 提取标题
                note_title = ""
                title_obj = content.get("title", {})
                if isinstance(title_obj, dict):
                    note_title = title_obj.get("value", "")
                elif isinstance(title_obj, str):
                    note_title = title_obj

                # 标题匹配度
                title_lower = note_title.lower().strip()
                query_lower = title.lower().strip()
                # 简单匹配：query 标题包含在 note 标题中，或反过来
                if title_lower == query_lower:
                    score = 1.0
                elif title_lower in query_lower or query_lower in title_lower:
                    score = 0.8
                else:
                    # 计算单词重叠率
                    query_words = set(query_lower.split())
                    note_words = set(title_lower.split())
                    overlap = len(query_words & note_words) / max(len(query_words), len(note_words), 1)
                    score = overlap

                if score > best_score and score >= 0.4:
                    best_score = score
                    best_match = note

            if not best_match:
                logger.debug("OpenReview: no good title match")
                return None

            # 从 best_match 提取 venue 信息
            content = best_match.get("content", {})
            invitation = best_match.get("invitation", "")
            venue_str = ""
            venue_year = None

            # 从 content.venue 提取
            venue_obj = content.get("venue", {})
            if isinstance(venue_obj, dict):
                venue_str = venue_obj.get("value", "")
            elif isinstance(venue_obj, str):
                venue_str = venue_obj

            # 从 invitation 推断会议（如 "NeurIPS.cc/2025/Conference"）
            if not venue_str and invitation:
                # 常见 invitation 格式: Conference/YYYY/Submission
                inv_match = re.search(r'(NeurIPS|ICLR|ICML|CVPR|AAAI|IJCAI)\.cc/(\d{4})', invitation)
                if inv_match:
                    venue_str = inv_match.group(1)
                    venue_year = int(inv_match.group(2))

            # 从 venueid 推断（OpenReview v2: content.venueid = {"value": "NeurIPS.cc/2025/Conference"}）
            venueid_obj = content.get("venueid", {})
            venueid = ""
            if isinstance(venueid_obj, dict):
                venueid = venueid_obj.get("value", "")
            elif isinstance(venueid_obj, str):
                venueid = venueid_obj

            # 从 venueid 推断（最可靠） — "NeurIPS.cc/2025/Conference"
            vid_match = re.search(r'(NeurIPS|ICLR|ICML|CVPR|AAAI|IJCAI)\.cc/(\d{4})', venueid)
            if vid_match:
                inferred_venue = vid_match.group(1)
                inferred_year = int(vid_match.group(2))
                if not venue_str:
                    venue_str = inferred_venue
                if not venue_year:
                    venue_year = inferred_year

            # 从 odate（official publication date）提取年份
            odate = best_match.get("odate")
            if odate and not venue_year:
                try:
                    venue_year = int(datetime.fromtimestamp(odate / 1000).year)
                except:
                    pass

            # 从 cdate（creation date）提取年份
            cdate = best_match.get("cdate")
            if cdate and not venue_year:
                try:
                    venue_year = int(datetime.fromtimestamp(cdate / 1000).year)
                except:
                    pass

            # 判断是否是 accepted
            is_accepted = False
            if venue_str:
                # "NeurIPS 2025 poster", "ICLR 2025 oral", "Accepted" 等都算 accepted
                if any(kw in venue_str.lower() for kw in ["accepted", "poster", "oral", "spotlight", "workshop"]):
                    is_accepted = True
                # venueid 包含 Conference 也算
                if "conference" in venueid.lower():
                    is_accepted = True

            # 提取 arXiv ID（如果 content 中有）
            arxiv_id = None
            arxiv_obj = content.get("arxiv", {}) or content.get("arxiv_id", {})
            if isinstance(arxiv_obj, dict):
                arxiv_val = arxiv_obj.get("value", "")
                if arxiv_val:
                    arxiv_id = arxiv_val
            elif isinstance(arxiv_obj, str):
                arxiv_id = arxiv_obj

            # 如果 venue_str 包含年份（如 "NeurIPS 2025 Conference"）
            if venue_str and not venue_year:
                year_match = re.search(r'(\d{4})', venue_str)
                if year_match:
                    venue_year = int(year_match.group(1))

            logger.info(f"OpenReview: title_match={best_score:.2f}, venue_str={venue_str}, venueid={venueid}, year={venue_year}, accepted={is_accepted}")

            return {
                "title_match_score": best_score,
                "venue": venue_str,
                "venueid": venueid,
                "year": venue_year,
                "invitation": invitation,
                "is_accepted": is_accepted,
                "arxiv_id": arxiv_id,
                "note_id": best_match.get("id"),
            }
        except Exception as e:
            logger.debug(f"OpenReview query failed: {e}")
            return None


class VenueYearEnhancer:
    """Venue/Year 增强器 — 四层 fallback 策略"""

    # 知名会议映射表（用于标准化）
    VENUE_ALIASES = {
        "neurips": ["neurips", "nips", "advances in neural information processing systems", "neural information processing systems"],
        "icml": ["icml", "international conference on machine learning"],
        "iclr": ["iclr", "international conference on learning representations"],
        "cvpr": ["cvpr", "conference on computer vision and pattern recognition", "computer vision and pattern recognition"],
        "iccv": ["iccv", "international conference on computer vision"],
        "eccv": ["eccv", "european conference on computer vision"],
        "aaai": ["aaai", "association for the advancement of artificial intelligence"],
        "ijcai": ["ijcai", "international joint conference on artificial intelligence"],
        "acl": ["acl", "association for computational linguistics"],
        "emnlp": ["emnlp", "empirical methods in natural language processing"],
        "naacl": ["naacl", "north american chapter of the association for computational linguistics"],
        "sigir": ["sigir", "special interest group on information retrieval"],
        "cikm": ["cikm", "conference on information and knowledge management"],
        "www": ["www", "the web conference", "international world wide web conference"],
        "kdd": ["kdd", "knowledge discovery and data mining"],
        "icde": ["icde", "international conference on data engineering"],
        "vldb": ["vldb", "very large data base"],
        "sigmod": ["sigmod", "special interest group on management of data"],
    }

    def __init__(self):
        self.config = get_config()
        self.cache = VenueYearCache()
        self.academic_api = AcademicAPIs()

    def _normalize_venue(self, venue: str) -> str:
        """标准化 venue 名称"""
        if not venue:
            return ""
        venue_lower = venue.lower().strip()
        for standard, aliases in self.VENUE_ALIASES.items():
            for alias in aliases:
                if alias in venue_lower:
                    return standard.upper()
        return venue

    def _is_trusted_venue(self, venue: str, venue_type: Optional[str] = None) -> bool:
        """判断 venue 是否是可信的会议/期刊名（而非仓库/预印本）"""
        if not venue:
            return False
        venue_lower = venue.lower().strip()

        # 仓库类型不可信
        if venue_lower in REPOSITORY_VENUES:
            return False
        if venue_type in ("repository", "preprint"):
            return False

        # 检查是否在知名会议别名中
        for standard, aliases in self.VENUE_ALIASES.items():
            for alias in aliases:
                if alias in venue_lower:
                    return True

        # 如果不是仓库且不在别名表中，可能是期刊 — 也算可信
        if venue_type in ("journal", "conference", "proceedings"):
            return True

        # 未知类型但不在仓库列表中 — 算中等可信
        return venue_lower not in REPOSITORY_VENUES

    def _extract_year_from_sources(self, sources: List[str]) -> Optional[int]:
        """从 sources 中提取年份"""
        for source in sources:
            # 匹配 URL 中的年份，如 /2025/ 或 /paper/2025/
            matches = re.findall(r'(\d{4})', source)
            for match in matches:
                year = int(match)
                if 2000 <= year <= 2030:
                    return year
        return None

    def _layer1_academic_api(self, title: str) -> Optional[VenueYearResult]:
        """
        第一层：学术 API 查询
        - OpenAlex 快速获取 venue/year/arxiv_id
        - 如果 venue 是知名会议 → 直接返回（高置信度）
        - 如果 venue 是仓库 → 返回 arxiv_id 但标记需要 fallback
        - 如果 venue 为 None但有 DOI → 用 DOI 做精确查询
        """
        # ─── OpenAlex 标题搜索 ───
        oa_result = self.academic_api.query_openalex(title)

        if not oa_result:
            logger.info(f"Layer1: OpenAlex 未找到 '{title[:50]}...'")
            return None

        venue = oa_result.get("venue", "")
        venue_type = oa_result.get("venue_type")
        year = oa_result.get("year")
        arxiv_id = oa_result.get("arxiv_id")
        doi = oa_result.get("doi")
        is_repo = oa_result.get("is_repository", False)

        logger.info(f"Layer1: OpenAlex → venue={venue}, type={venue_type}, year={year}, arxiv={arxiv_id}, doi={doi}, is_repo={is_repo}")

# 如果 venue 为 None 但有 DOI → 用 DOI 做精确查询
        if not venue and doi:
            doi_result = self.academic_api.query_openalex_doi(doi)
            if doi_result and doi_result.get("venue"):
                venue = doi_result.get("venue")
                venue_type = doi_result.get("venue_type")
                is_repo = doi_result.get("is_repository", False)
                logger.info(f"Layer1: OpenAlex DOI → venue={venue}, type={venue_type}, is_repo={is_repo}")

            # 如果 DOI 精确查询也返回 venue=None，从 DOI 字符串推断会议
            if not venue and doi:
                # DOI 可能包含会议缩写，如:
                # 10.1109/cvpr.2016.90 → CVPR 2016
                # 10.1145/3447548.3467268 → KDD/SIGIR 等
                # 10.5555/3455716.3455856 → NeurIPS
                doi_lower = doi.lower()
                doi_venue_map = {
                    "cvpr": "CVPR",
                    "iccv": "ICCV",
                    "eccv": "ECCV",
                    "icml": "ICML",
                    "iclr": "ICLR",
                    "nips": "NeurIPS",
                    "neurips": "NeurIPS",
                    "aaai": "AAAI",
                    "ijcai": "IJCAI",
                    "acl": "ACL",
                    "emnlp": "EMNLP",
                    "naacl": "NAACL",
                    "sigir": "SIGIR",
                    "cikm": "CIKM",
                    "kdd": "KDD",
                    "www": "WWW",
                    "sigmod": "SIGMOD",
                    "vldb": "VLDB",
                    "icde": "ICDE",
                }
                for doi_prefix, venue_name in doi_venue_map.items():
                    if doi_prefix in doi_lower:
                        inferred_venue = venue_name
                        logger.info(f"Layer1: DOI → inferred venue={inferred_venue} from doi={doi}")
                        # 从 DOI 中的年份推断（优先 2000-2030 范围）
                        year_matches = re.findall(r'(\d{4})', doi)
                        inferred_year = year
                        for y in year_matches:
                            iy = int(y)
                            if 2000 <= iy <= 2030:
                                inferred_year = iy
                                break
                        return VenueYearResult(
                            venue=inferred_venue,
                            year=inferred_year,
                            confidence=0.85,  # DOI 推断非常可靠（DOI 是唯一标识符）
                            sources=["openalex_doi_inferred"],
                            arxiv_id=arxiv_id,
                            notes=f"OpenAlex venue=None, inferred from DOI: {doi} → {inferred_venue}",
                            queried_at=datetime.now(),
                        )

        # 如果是可信会议/期刊 → 直接使用
        if self._is_trusted_venue(venue, venue_type):
            return VenueYearResult(
                venue=self._normalize_venue(venue),
                year=year,
                confidence=0.85,
                sources=["openalex"],
                arxiv_id=arxiv_id,
                notes=f"OpenAlex: venue={venue}, type={venue_type}",
                queried_at=datetime.now(),
            )

        # 如果是仓库类型 → 尝试 DBLP 补充，而不是直接标记为需要 fallback
        if is_repo:
            logger.info(f"Layer1: OpenAlex returned repository venue='{venue}', trying DBLP...")
            dblp_result = self.academic_api.query_dblp(title)
            if dblp_result and dblp_result.get("venue") and not dblp_result.get("is_repository", False):
                dblp_venue = dblp_result.get("venue")
                dblp_year = dblp_result.get("year")
                dblp_title = dblp_result.get("title", "")
                # 验证标题匹配度
                title_lower = (dblp_title or "").lower().strip().rstrip('.')
                query_lower = title.lower().strip()
                if title_lower == query_lower:
                    title_match = 1.0
                else:
                    query_words = set(query_lower.split())
                    note_words = set(title_lower.split())
                    title_match = len(query_words & note_words) / max(len(query_words), len(note_words), 1)

                if title_match >= 0.7:
                    dblp_conf = 0.85 if title_match >= 0.95 else 0.6
                    logger.info(f"Layer1: DBLP override for repository → venue={dblp_venue}, year={dblp_year}, match={title_match}, conf={dblp_conf}")
                    return VenueYearResult(
                        venue=self._normalize_venue(dblp_venue),
                        year=dblp_year or year,
                        confidence=dblp_conf,
                        sources=["openalex", "dblp"],
                        arxiv_id=arxiv_id,
                        notes=f"OpenAlex returned repository, DBLP found: venue={dblp_venue}",
                        queried_at=datetime.now(),
                    )
                else:
                    logger.info(f"Layer1: DBLP fallback rejected — title_match={title_match} too low")

            # DBLP 也失败 → 标记需要 fallback
            return VenueYearResult(
                venue="",
                year=year,
                confidence=0.3,
                sources=["openalex"],
                arxiv_id=arxiv_id,
                notes=f"OpenAlex: venue={venue} (repository), need fallback for real venue",
                queried_at=datetime.now(),
            )

        # 其他情况（未知 venue 或 venue 为 None）
        if venue:
            return VenueYearResult(
                venue=self._normalize_venue(venue),
                year=year,
                confidence=0.5,
                sources=["openalex"],
                arxiv_id=arxiv_id,
                notes=f"OpenAlex: venue={venue}, type={venue_type}, unknown category",
                queried_at=datetime.now(),
            )
        else:
            # venue 为 None → 先尝试 DBLP 补充
            dblp_result = self.academic_api.query_dblp(title)
            if dblp_result and dblp_result.get("venue") and not dblp_result.get("is_repository", False):
                dblp_venue = dblp_result.get("venue")
                dblp_year = dblp_result.get("year")
                dblp_title = dblp_result.get("title", "")
                # 验证标题匹配度 — 防止 DBLP 返回不相关论文
                title_lower = (dblp_title or "").lower().strip()
                query_lower = title.lower().strip()
                if title_lower == query_lower:
                    title_match = 1.0
                elif title_lower in query_lower or query_lower in title_lower:
                    title_match = 0.8
                else:
                    query_words = set(query_lower.split())
                    note_words = set(title_lower.split())
                    title_match = len(query_words & note_words) / max(len(query_words), len(note_words), 1)

                if title_match >= 0.7:
                    # 根据匹配度调整置信度：
                    # 完全匹配(1.0) → 0.6, 部分匹配(0.7-0.99) → 0.3
                    # 部分匹配的 DBLP 结果不可靠，让后续层覆盖
                    dblp_conf = 0.6 if title_match >= 0.95 else 0.3
                    logger.info(f"Layer1: DBLP fallback → venue={dblp_venue}, year={dblp_year}, title_match={title_match}, conf={dblp_conf}")
                    return VenueYearResult(
                        venue=self._normalize_venue(dblp_venue),
                        year=dblp_year or year,
                        confidence=dblp_conf,
                        sources=["openalex", "dblp"],
                        arxiv_id=arxiv_id,
                        notes=f"OpenAlex venue=None, DBLP: venue={dblp_venue}, title_match={title_match}",
                        queried_at=datetime.now(),
                    )
                else:
                    logger.info(f"Layer1: DBLP fallback rejected — title_match={title_match} too low (dblp_title='{dblp_title}')")
            # DBLP 也失败 → 需要后续层补充
            return VenueYearResult(
                venue="",
                year=year,
                confidence=0.2,
                sources=["openalex"],
                arxiv_id=arxiv_id,
                notes=f"OpenAlex: venue=None, need fallback. DOI={doi}",
                queried_at=datetime.now(),
            )

    def _layer2_arxiv_comment(self, arxiv_id: Optional[str]) -> Optional[VenueYearResult]:
        """
        第二层：arXiv comment 字段解析
        - 如果 arXiv 作者标注了 "Accepted to NeurIPS 2025" → 提取
        - 否则返回 None（不覆盖前层结果）
        """
        if not arxiv_id:
            return None

        arxiv_result = self.academic_api.query_arxiv_by_id(arxiv_id)

        if not arxiv_result:
            logger.info(f"Layer2: arXiv API 查询失败 (arxiv_id={arxiv_id})")
            return None

        comment = arxiv_result.get("comment")
        venue_from_comment = arxiv_result.get("venue_from_comment")
        year_from_comment = arxiv_result.get("year_from_comment")
        has_venue = arxiv_result.get("has_venue_in_comment", False)

        logger.info(f"Layer2: arXiv comment='{comment}', extracted venue={venue_from_comment}, year={year_from_comment}")

        if has_venue and venue_from_comment:
            return VenueYearResult(
                venue=self._normalize_venue(venue_from_comment),
                year=year_from_comment,
                confidence=0.9,  # 作者自己标注，非常可信
                sources=["arxiv_comment"],
                arxiv_id=arxiv_id,
                notes=f"arXiv comment: '{comment}' → {venue_from_comment} {year_from_comment}",
                queried_at=datetime.now(),
            )

        return None

    # ─── OpenReview API: query_openreview ───
    def _layer3_openreview(self, title: str, arxiv_id: Optional[str] = None) -> Optional[VenueYearResult]:
        """
        第三层：OpenReview API — 官方审稿平台查询
        
        OpenReview 是 NeurIPS/ICLR/ICML 的官方审稿平台，
        对近期论文能提供最准确的 venue 信息（包括 accepted 状态）。
        
        注意：仅覆盖 NeurIPS/ICLR/ICML 等少数顶级会议，
        不覆盖 CVPR/AAAI/KDD 等。
        """
        or_result = self.academic_api.query_openreview(title)

        if not or_result:
            logger.info(f"Layer3: OpenReview 未找到 '{title[:50]}...'")
            return None

        venue_str = or_result.get("venue", "")
        venueid = or_result.get("venueid", "")
        year = or_result.get("year")
        is_accepted = or_result.get("is_accepted", False)
        match_score = or_result.get("title_match_score", 0)
        invitation = or_result.get("invitation", "")

        logger.info(f"Layer3: OpenReview → venue={venue_str}, venueid={venueid}, year={year}, accepted={is_accepted}, match={match_score}")

        # title_match 过滤：部分匹配(score < 0.85)的 OpenReview 结果不够可靠
        # OpenReview 搜索是模糊的，短标题（如 "Attention Is All You Need"）容易误匹配
        if match_score < 0.85:
            logger.info(f"Layer3: OpenReview title_match={match_score} too low (need >= 0.85), skipping")
            return None

        # 从 venueid 提取会议名（最可靠） — "NeurIPS.cc/2025/Conference"
        normalized_venue = ""
        vid_match = re.search(r'(NeurIPS|ICLR|ICML|CVPR|AAAI|IJCAI)\.cc', venueid)
        if vid_match:
            normalized_venue = vid_match.group(1).upper()

        # 如果 venueid 没有识别出，从 venue_str 提取
        if not normalized_venue and venue_str:
            for standard, aliases in self.VENUE_ALIASES.items():
                for alias in aliases:
                    if alias in venue_str.lower():
                        normalized_venue = standard.upper()
                        break
                if normalized_venue:
                    break

        # 如果都没有，从 invitation 推断
        if not normalized_venue and invitation:
            inv_match = re.search(r'(NeurIPS|ICLR|ICML|CVPR|AAAI|IJCAI)\.cc/(\d{4})', invitation)
            if inv_match:
                normalized_venue = inv_match.group(1).upper()
                if not year:
                    year = int(inv_match.group(2))

        if not normalized_venue:
            # 没有识别出具体会议，返回 None
            return None

        # 置信度计算
        confidence = 0.7  # 基础置信度
        if is_accepted:
            confidence = 0.95  # OpenReview accepted → 非常可信
        if match_score >= 0.8:
            confidence = min(confidence + 0.05, 0.98)
        elif match_score >= 0.5:
            confidence = min(confidence - 0.05, 0.95)

        return VenueYearResult(
            venue=normalized_venue,
            year=year,
            confidence=confidence,
            sources=["openreview"],
            arxiv_id=arxiv_id or or_result.get("arxiv_id"),
            notes=f"OpenReview: venue={venue_str}, venueid={venueid}, accepted={is_accepted}, match={match_score}",
            queried_at=datetime.now(),
        )

    def _layer4_semantic_scholar(self, arxiv_id: Optional[str], title: str = "") -> Optional[VenueYearResult]:
        """
        第四层：Semantic Scholar 补充查询
        
        先按 arXiv ID 精确查询，如果失败则按标题搜索。
        注意：有 rate limit，仅在前面三层都失败时使用。
        """
        # 先尝试 arXiv ID 精确查询
        if arxiv_id:
            ss_result = self.academic_api.query_semantic_scholar_arxiv(arxiv_id)
            if ss_result:
                venue = ss_result.get("venue", "")
                pub_venue_name = ss_result.get("publicationVenue_name", "")
                pub_venue_type = ss_result.get("publicationVenue_type", "")
                year = ss_result.get("year")
                is_repo = ss_result.get("is_repository", False)
                effective_venue = pub_venue_name or venue

                logger.info(f"Layer4: Semantic Scholar (arxiv) → venue={venue}, pub_venue={pub_venue_name}, type={pub_venue_type}, year={year}")

                if is_repo or not effective_venue:
                    pass  # 继续尝试标题搜索
                elif self._is_trusted_venue(effective_venue, pub_venue_type):
                    return VenueYearResult(
                        venue=self._normalize_venue(effective_venue),
                        year=year,
                        confidence=0.6,
                        sources=["semantic_scholar"],
                        arxiv_id=arxiv_id,
                        notes=f"Semantic Scholar: venue={venue}, pub_venue={pub_venue_name}, type={pub_venue_type}",
                        queried_at=datetime.now(),
                    )

        # 标题搜索（arXiv ID 查询失败或返回无效结果时）
        if title:
            ss_result = self.academic_api.query_semantic_scholar_title(title)
            if not ss_result:
                logger.info(f"Layer4: Semantic Scholar title search failed for '{title[:50]}...'")
                return None

            venue = ss_result.get("venue", "")
            pub_venue_name = ss_result.get("publicationVenue_name", "")
            pub_venue_type = ss_result.get("publicationVenue_type", "")
            year = ss_result.get("year")
            is_repo = ss_result.get("is_repository", False)
            effective_venue = pub_venue_name or venue

            logger.info(f"Layer4: Semantic Scholar (title) → venue={venue}, pub_venue={pub_venue_name}, type={pub_venue_type}, year={year}")

            # ─── Crossref DOI 回退 ───
            # Semantic Scholar 找到论文但 venue 为空时，用 DOI 查 Crossref
            if not effective_venue or is_repo:
                doi = ss_result.get("doi", "")
                if doi:
                    logger.info(f"Layer4: Semantic Scholar venue empty, trying Crossref with DOI={doi}")
                    cr_result = self.academic_api.query_crossref(doi)
                    if cr_result and cr_result.get("venue"):
                        cr_venue = cr_result["venue"]
                        cr_year = cr_result.get("year") or year
                        logger.info(f"Layer4: Crossref → venue={cr_venue}, year={cr_year}")
                        # 标准化 venue（从会议全称提取缩写）
                        normalized = self._normalize_venue(cr_venue)
                        if normalized and normalized != cr_venue:
                            # 成功提取了标准缩写
                            return VenueYearResult(
                                venue=normalized,
                                year=cr_year,
                                confidence=0.75,
                                sources=["semantic_scholar", "crossref"],
                                arxiv_id=arxiv_id or ss_result.get("arxiv_id"),
                                notes=f"Crossref DOI: venue={cr_venue} → {normalized}, year={cr_year}",
                                queried_at=datetime.now(),
                            )
                        else:
                            # venue 是完整名称但无法标准化，仍然使用（可能需人工确认）
                            return VenueYearResult(
                                venue=cr_venue,
                                year=cr_year,
                                confidence=0.6,
                                sources=["semantic_scholar", "crossref"],
                                arxiv_id=arxiv_id or ss_result.get("arxiv_id"),
                                notes=f"Crossref DOI: venue={cr_venue} (full name, not standardized), year={cr_year}",
                                queried_at=datetime.now(),
                            )
                return None

            if self._is_trusted_venue(effective_venue, pub_venue_type):
                return VenueYearResult(
                    venue=self._normalize_venue(effective_venue),
                    year=year,
                    confidence=0.6,
                    sources=["semantic_scholar"],
                    arxiv_id=arxiv_id or ss_result.get("arxiv_id"),
                    notes=f"Semantic Scholar title search: venue={venue}, pub_venue={pub_venue_name}, type={pub_venue_type}",
                    queried_at=datetime.now(),
                )

        return None

    def enhance(self, title: str, current_venue: str = "", current_year: Optional[int] = None, url: str = "") -> Tuple[str, Optional[int], float]:
        """
        增强 venue 和 year 信息 — 四层 fallback 策略

        流程:
        1. 缓存检查 → 有高质量缓存直接返回
        2. 当前 venue 检查 → 如果已经是可信会议，跳过
        3. Layer1: OpenAlex → 如果返回可信 venue，使用；仓库类型则继续
        4. Layer2: arXiv comment → 如果有作者标注，使用
        5. Layer3: OpenReview API → 查官方审稿平台的 accepted 状态
        6. Layer4: Semantic Scholar → 补充查询（有 rate limit）

        Returns:
            (venue, year, confidence)
        """
        # ─── 0. 检查缓存 ───
        cached = self.cache.get(title)
        if cached and cached.confidence >= 0.8:
            logger.info(f"Using cached venue/year for '{title[:50]}...' (conf={cached.confidence})")
            return cached.venue, cached.year, cached.confidence

        # ─── 1. 当前 venue 检查 ───
        if current_venue and current_venue.strip() and "arxiv" not in current_venue.lower():
            normalized = self._normalize_venue(current_venue)
            if self._is_trusted_venue(current_venue):
                # 但 year 缺失时，仍然查询补充
                if current_year:
                    logger.info(f"Current venue '{current_venue}' is trusted, skipping query")
                    return normalized, current_year, 1.0

        # ─── 2. Layer1: OpenAlex ───
        layer1 = self._layer1_academic_api(title)

        if layer1 and layer1.confidence >= 0.8:
            # OpenAlex 返回了可信 venue → 直接使用
            logger.info(f"Layer1 (OpenAlex) sufficient: venue={layer1.venue}, year={layer1.year}, conf={layer1.confidence}")
            self.cache.set(title, layer1)
            return layer1.venue, layer1.year, layer1.confidence

        # ─── 3. Layer2: arXiv comment ───
        arxiv_id = layer1.arxiv_id if layer1 else None
        layer2 = self._layer2_arxiv_comment(arxiv_id)

        if layer2 and layer2.confidence >= 0.8:
            logger.info(f"Layer2 (arXiv comment) sufficient: venue={layer2.venue}, year={layer2.year}, conf={layer2.confidence}")
            final_year = layer2.year or (layer1.year if layer1 else None) or current_year
            result = VenueYearResult(
                venue=layer2.venue,
                year=final_year,
                confidence=layer2.confidence,
                sources=layer2.sources,
                arxiv_id=arxiv_id,
                notes=layer2.notes,
                queried_at=datetime.now(),
            )
            self.cache.set(title, result)
            return result.venue, result.year, result.confidence

        # ─── 4. Layer3: OpenReview ───
        logger.info(f"Falling back to Layer3 (OpenReview) for '{title[:50]}...'")
        layer3 = self._layer3_openreview(title, arxiv_id)

        if layer3 and layer3.confidence >= 0.7:
            logger.info(f"Layer3 (OpenReview) sufficient: venue={layer3.venue}, year={layer3.year}, conf={layer3.confidence}")
            final_year = layer3.year or (layer1.year if layer1 else None) or current_year
            all_sources = layer3.sources
            if layer1:
                all_sources.extend(layer1.sources)
            result = VenueYearResult(
                venue=layer3.venue,
                year=final_year,
                confidence=layer3.confidence,
                sources=all_sources,
                arxiv_id=arxiv_id or layer3.arxiv_id,
                notes=layer3.notes,
                queried_at=datetime.now(),
            )
            self.cache.set(title, result)
            return result.venue, result.year, result.confidence

        # ─── 5. Layer4: Semantic Scholar ───
        logger.info(f"Falling back to Layer4 (Semantic Scholar) for '{title[:50]}...'")
        layer4 = self._layer4_semantic_scholar(arxiv_id, title)

        if layer4 and layer4.confidence >= 0.5:
            logger.info(f"Layer4 (Semantic Scholar) sufficient: venue={layer4.venue}, year={layer4.year}, conf={layer4.confidence}")
            final_year = layer4.year or (layer1.year if layer1 else None) or current_year
            all_sources = layer4.sources
            if layer1:
                all_sources.extend(layer1.sources)
            result = VenueYearResult(
                venue=layer4.venue,
                year=final_year,
                confidence=layer4.confidence,
                sources=all_sources,
                arxiv_id=arxiv_id,
                notes=layer4.notes,
                queried_at=datetime.now(),
            )
            self.cache.set(title, result)
            return result.venue, result.year, result.confidence

        # ─── 6. 所有层都失败 → 返回最优 partial 结果 ───
        logger.warning(f"All 4 layers failed for '{title[:50]}...'")

        # 尝试合并 partial 信息
        best_venue = ""
        best_year = None
        best_conf = 0.0
        best_sources = []
        best_notes = ""

        # 从各层收集 partial 信息
        for layer, name in [(layer1, "openalex"), (layer2, "arxiv"), (layer3, "openreview"), (layer4, "semantic_scholar")]:
            if layer:
                if layer.confidence > best_conf:
                    best_conf = layer.confidence
                if layer.venue and layer.venue not in ("Unknown", ""):
                    best_venue = layer.venue
                if layer.year:
                    best_year = layer.year
                best_sources.extend(layer.sources)
                best_notes = layer.notes

        # 使用 current 信息作为兜底
        final_venue = best_venue or (self._normalize_venue(current_venue) if current_venue else "Unknown")
        final_year = best_year or current_year
        final_conf = max(best_conf, 0.1)

        if final_venue == "Unknown" and not final_year:
            logger.warning(f"No venue/year found for '{title[:50]}...'")
            return "Unknown", None, 0.0

        # 缓存低置信度结果（标记为需要人工确认）
        result = VenueYearResult(
            venue=final_venue,
            year=final_year,
            confidence=final_conf,
            sources=best_sources,
            arxiv_id=arxiv_id,
            notes=f"Low confidence (all layers partial): {best_notes}",
            queried_at=datetime.now(),
        )
        self.cache.set(title, result)
        return final_venue, final_year, final_conf


def main():
    """测试 VenueYearEnhancer — 四层 fallback"""
    enhancer = VenueYearEnhancer()

    test_cases = [
        {"title": "SPOT-trip: dual-preference driven out-of-town trip recommendation", "expected": "NeurIPS 2025"},
        {"title": "Attention Is All You Need", "expected": "NeurIPS 2017"},
        {"title": "Deep Residual Learning for Image Recognition", "expected": "CVPR 2016"},
    ]

    print("=" * 80)
    print("Venue/Year 增强器测试 — 四层 fallback")
    print("=" * 80)

    for case in test_cases:
        title = case["title"]
        expected = case["expected"]

        print(f"\n{'='*80}")
        print(f"论文: {title}")
        print(f"预期: {expected}")
        print(f"{'='*80}")

        venue, year, confidence = enhancer.enhance(title)
        match = "✅" if expected.split()[0] == venue else "❌"
        print(f"结果: venue={venue}, year={year}, confidence={confidence} {match}")

    print("\n" + "=" * 80)
    print("测试完成")
    print("=" * 80)


if __name__ == "__main__":
    main()
