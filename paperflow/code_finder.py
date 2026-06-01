"""
Code URL 查找模块：通过 Papers with Code API + GitHub Search API
查找论文对应的公开代码仓库。

核心策略：
1. Papers with Code API — 人工维护的论文-代码映射，最准确
2. GitHub Search API — 按标题搜索仓库，作为 fallback
3. 标题相似度验证 — 防止误匹配

注意：GitHub Search API 未认证时 rate limit 为 10次/分钟
"""

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List

import requests

from paperflow.logging_utils import get_logger

logger = get_logger(__name__)

SESSION = requests.Session()
SESSION.trust_env = False
SESSION.proxies = {"http": None, "https": None}


@dataclass
class CodeFindResult:
    """代码查找结果"""
    url: str = ""
    source: str = ""  # paperswithcode / github_search / unknown
    confidence: float = 0.0  # 0.0-1.0
    title_match_score: float = 0.0
    repo_name: str = ""
    stars: int = 0
    description: str = ""
    notes: str = ""
    queried_at: Optional[datetime] = None


class CodeFinderCache:
    """代码查找结果缓存"""

    def __init__(self, cache_file: str = "data/code_finder_cache.json"):
        self.cache_file = Path(cache_file)
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict = {}
        self._load()

    def _load(self):
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load code finder cache: {e}")
                self._data = {}

    def _save(self):
        try:
            with open(self.cache_file, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save code finder cache: {e}")

    def get(self, title: str) -> Optional[CodeFindResult]:
        key = title.lower().strip()
        if key in self._data:
            data = self._data[key]
            queried_at = datetime.fromisoformat(data.get("queried_at", "2000-01-01"))
            if datetime.now() - queried_at < timedelta(days=30):
                return CodeFindResult(**{k: v for k, v in data.items() if k != "queried_at"})
        return None

    def set(self, title: str, result: CodeFindResult):
        key = title.lower().strip()
        self._data[key] = {
            "url": result.url,
            "source": result.source,
            "confidence": result.confidence,
            "title_match_score": result.title_match_score,
            "repo_name": result.repo_name,
            "stars": result.stars,
            "description": result.description,
            "notes": result.notes,
            "queried_at": datetime.now().isoformat(),
        }
        self._save()


class CodeFinder:
    """论文代码链接查找器"""

    # Papers with Code API 端点
    PWC_API_BASE = "https://paperswithcode.com/api/v1"
    # GitHub Search API 端点
    GITHUB_API_BASE = "https://api.github.com"

    def __init__(self):
        self.cache = CodeFinderCache()
        self.session = SESSION
        # GitHub 请求间隔（未认证 10次/分钟）
        self._github_last_request = 0.0
        self._github_min_interval = 7.0  # 秒

    # ─── 公共入口 ───

    def find_code_url(self, title: str, authors: str = "") -> CodeFindResult:
        """
        按论文标题查找代码仓库链接。

        策略：
        1. 先查缓存
        2. Papers with Code API（最准确）
        3. GitHub Search API（fallback）
        4. 返回带置信度的结果
        """
        # 1. 检查缓存
        cached = self.cache.get(title)
        if cached and cached.confidence >= 0.7:
            logger.info(f"Code URL cache hit for '{title[:50]}...' → {cached.url}")
            return cached

        logger.info(f"Searching code URL for: '{title[:60]}...'")

        # 2. Papers with Code
        pwc_result = self._search_paperswithcode(title)
        if pwc_result and pwc_result.confidence >= 0.8:
            logger.info(f"Found via Papers with Code: {pwc_result.url} (conf={pwc_result.confidence})")
            self.cache.set(title, pwc_result)
            return pwc_result

        # 3. GitHub Search fallback
        logger.info(f"Papers with Code not found / low confidence, trying GitHub Search...")
        gh_result = self._search_github(title, authors)
        if gh_result and gh_result.confidence >= 0.5:
            logger.info(f"Found via GitHub Search: {gh_result.url} (conf={gh_result.confidence})")
            self.cache.set(title, gh_result)
            return gh_result

        # 4. 都没找到
        logger.warning(f"No code URL found for '{title[:60]}...'")
        empty = CodeFindResult(
            url="",
            source="unknown",
            confidence=0.0,
            notes="No code repository found via Papers with Code or GitHub Search",
            queried_at=datetime.now(),
        )
        self.cache.set(title, empty)
        return empty

    # ─── Papers with Code API ───

    def _search_paperswithcode(self, title: str) -> Optional[CodeFindResult]:
        """通过 Papers with Code API 搜索论文及其代码仓库"""
        try:
            # 步骤1：搜索论文
            search_url = f"{self.PWC_API_BASE}/papers/"
            params = {"q": title, "items_per_page": 10}
            resp = self.session.get(search_url, params=params, timeout=20)
            if resp.status_code != 200:
                logger.debug(f"PWC search HTTP {resp.status_code}")
                return None

            data = resp.json()
            results = data.get("results", [])
            if not results:
                logger.debug("PWC: no papers found")
                return None

            # 找到最佳匹配的论文
            best_paper = None
            best_score = 0.0
            for paper in results:
                pwc_title = paper.get("title", "")
                score = self._title_similarity(pwc_title, title)
                if score > best_score:
                    best_score = score
                    best_paper = paper

            if not best_paper or best_score < 0.6:
                logger.debug(f"PWC: best match score {best_score:.2f} too low")
                return None

            paper_id = best_paper.get("id")
            paper_title = best_paper.get("title", "")
            logger.info(f"PWC matched paper: '{paper_title}' (score={best_score:.2f})")

            # 步骤2：获取该论文的代码仓库
            repos_url = f"{self.PWC_API_BASE}/papers/{paper_id}/repositories/"
            repos_resp = self.session.get(repos_url, timeout=20)
            if repos_resp.status_code != 200:
                logger.debug(f"PWC repos HTTP {repos_resp.status_code}")
                return None

            repos_data = repos_resp.json()
            repos = repos_data.get("results", [])
            if not repos:
                logger.debug("PWC: no repositories for this paper")
                return None

            # 选择最佳仓库（通常第一个就是官方推荐）
            best_repo = repos[0]
            repo_url = best_repo.get("url", "")
            if not repo_url:
                return None

            # 计算置信度
            confidence = min(0.95, 0.7 + best_score * 0.25)

            return CodeFindResult(
                url=repo_url,
                source="paperswithcode",
                confidence=confidence,
                title_match_score=best_score,
                repo_name=best_repo.get("name", ""),
                stars=best_repo.get("stars", 0),
                description=best_repo.get("description", ""),
                notes=f"Matched via Papers with Code: '{paper_title}'",
                queried_at=datetime.now(),
            )

        except Exception as e:
            logger.debug(f"PWC search failed: {e}")
            return None

    # ─── GitHub Search API ───

    def _search_github(self, title: str, authors: str = "") -> Optional[CodeFindResult]:
        """通过 GitHub Search API 搜索仓库"""
        # 限速
        elapsed = time.time() - self._github_last_request
        if elapsed < self._github_min_interval:
            time.sleep(self._github_min_interval - elapsed)
        self._github_last_request = time.time()

        # 构建多个查询策略
        queries = self._build_github_queries(title, authors)

        for query in queries:
            try:
                search_url = f"{self.GITHUB_API_BASE}/search/repositories"
                params = {
                    "q": query,
                    "sort": "stars",
                    "order": "desc",
                    "per_page": 10,
                }
                resp = self.session.get(search_url, params=params, timeout=20)

                if resp.status_code == 403:
                    # Rate limit
                    logger.warning("GitHub Search API rate limited (403)")
                    break
                if resp.status_code != 200:
                    logger.debug(f"GitHub search HTTP {resp.status_code}")
                    continue

                data = resp.json()
                items = data.get("items", [])
                if not items:
                    continue

                # 验证每个结果，找最佳匹配
                best_match = None
                best_score = 0.0
                best_exact_bonus = 0.0
                for item in items:
                    repo_name = item.get("name", "")
                    full_name = item.get("full_name", "")
                    description = item.get("description") or ""
                    readme_url = item.get("url", "") + "/readme"

                    # 综合验证：名称匹配 + README 验证
                    name_score = self._title_similarity(repo_name, title)
                    desc_score = self._title_similarity(description, title) if description else 0

                    # 精确名称匹配加分：repo_name 被论文标题核心词包含
                    exact_match_bonus = 0.0
                    repo_clean = self._normalize_text(repo_name)
                    title_clean = self._normalize_text(title)
                    if repo_clean and len(repo_clean.split()) <= 3:
                        if repo_clean in title_clean:
                            exact_match_bonus = 0.15
                        elif any(repo_clean == word for word in title_clean.split()):
                            exact_match_bonus = 0.10

                    # 尝试获取 README 验证
                    readme_score = 0.0
                    try:
                        readme_resp = self.session.get(
                            readme_url,
                            timeout=10,
                            headers={"Accept": "application/vnd.github.v3.raw"},
                        )
                        if readme_resp.status_code == 200:
                            readme = readme_resp.text[:5000]  # 只读前5000字符
                            readme_score = self._title_similarity(readme, title)
                    except Exception:
                        pass

                    # 综合分数：名称权重 0.4，README 权重 0.5，描述权重 0.1，精确匹配加分
                    combined_score = min(1.0, name_score * 0.4 + readme_score * 0.5 + desc_score * 0.1 + exact_match_bonus)

                    if combined_score > best_score and combined_score >= 0.3:
                        best_score = combined_score
                        best_match = item
                        best_exact_bonus = exact_match_bonus

                if best_match and best_score >= 0.4:
                    # 精确匹配时提升 base confidence
                    if best_exact_bonus >= 0.15:
                        confidence = min(0.85, 0.55 + best_score * 0.30)
                    elif best_exact_bonus >= 0.10:
                        confidence = min(0.80, 0.50 + best_score * 0.30)
                    else:
                        confidence = min(0.75, 0.40 + best_score * 0.35)
                    return CodeFindResult(
                        url=best_match.get("html_url", ""),
                        source="github_search",
                        confidence=confidence,
                        title_match_score=best_score,
                        repo_name=best_match.get("name", ""),
                        stars=best_match.get("stargazers_count", 0),
                        description=best_match.get("description") or "",
                        notes=f"GitHub search match: '{best_match.get('full_name')}' (score={best_score:.2f})",
                        queried_at=datetime.now(),
                    )

            except Exception as e:
                logger.debug(f"GitHub search failed for query '{query}': {e}")
                continue

        return None

    def _build_github_queries(self, title: str, authors: str = "") -> List[str]:
        """构建 GitHub 搜索查询（多个策略，按精确度排序）"""
        queries = []

        # 策略1：论文标题（去掉冒号等标点）精确匹配
        clean_title = re.sub(r'[:;!?.,]', ' ', title).strip()
        queries.append(f'"{clean_title}" in:name,description,readme')

        # 策略2：标题关键词 + paper
        keywords = self._extract_keywords(title)
        if len(keywords) >= 2:
            queries.append(f'{" ".join(keywords[:4])} paper code in:readme')

        # 策略3：标题前几个词
        first_words = " ".join(title.split()[:4])
        queries.append(f'"{first_words}" in:readme')

        # 策略4：如果提供了作者，加入作者名
        if authors:
            first_author = authors.split(",")[0].strip().split()[-1]  # 取 last name
            if first_author:
                queries.append(f'{" ".join(keywords[:3])} {first_author} in:readme')

        # 去重
        seen = set()
        unique = []
        for q in queries:
            if q not in seen:
                seen.add(q)
                unique.append(q)
        return unique

    # ─── 工具方法 ───

    @staticmethod
    def _title_similarity(a: str, b: str) -> float:
        """计算两个标题的相似度 (0.0-1.0)"""
        if not a or not b:
            return 0.0

        a_clean = CodeFinder._normalize_text(a)
        b_clean = CodeFinder._normalize_text(b)

        if a_clean == b_clean:
            return 1.0

        # 包含关系
        if a_clean in b_clean or b_clean in a_clean:
            return 0.85

        # Jaccard 相似度（词级别）
        a_words = set(a_clean.split())
        b_words = set(b_clean.split())
        if not a_words or not b_words:
            return 0.0

        intersection = a_words & b_words
        union = a_words | b_words
        jaccard = len(intersection) / len(union)

        # 如果短的一方大部分词都被匹配，加分
        short_len = min(len(a_words), len(b_words))
        coverage = len(intersection) / short_len if short_len > 0 else 0

        # 综合分数
        return jaccard * 0.6 + coverage * 0.4

    @staticmethod
    def _normalize_text(text: str) -> str:
        """标准化文本用于比较"""
        text = text.lower().strip()
        # 移除标点
        text = re.sub(r'[^\w\s]', ' ', text)
        # 移除多余空格
        text = re.sub(r'\s+', ' ', text)
        # 移除停用词
        stopwords = {"the", "a", "an", "in", "on", "at", "for", "with", "of", "to", "and", "or"}
        words = [w for w in text.split() if w not in stopwords and len(w) > 1]
        return " ".join(words)

    @staticmethod
    def _extract_keywords(title: str) -> List[str]:
        """从标题中提取关键词"""
        clean = CodeFinder._normalize_text(title)
        words = clean.split()
        # 过滤太短的词
        return [w for w in words if len(w) > 2]


# ─── 快捷函数 ───

def find_code_url(title: str, authors: str = "") -> str:
    """快捷函数：直接返回 URL 字符串（空字符串表示未找到）"""
    finder = CodeFinder()
    result = finder.find_code_url(title, authors)
    return result.url


def find_code_url_with_meta(title: str, authors: str = "") -> dict:
    """返回完整元数据（用于调试/展示）"""
    finder = CodeFinder()
    result = finder.find_code_url(title, authors)
    return {
        "url": result.url,
        "source": result.source,
        "confidence": result.confidence,
        "title_match_score": result.title_match_score,
        "repo_name": result.repo_name,
        "stars": result.stars,
        "description": result.description,
        "notes": result.notes,
    }
