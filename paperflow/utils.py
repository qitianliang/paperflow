import json
import re
from typing import Optional, Dict, Any
from paperflow.logging_utils import get_logger

logger = get_logger(__name__)

def try_repair_json(raw: str) -> Optional[Dict[str, Any]]:
    """Attempt to repair common JSON issues from LLM output."""
    if not raw or not raw.strip():
        return None

    text = raw.strip()

    # Remove markdown code fences
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting JSON object from surrounding text
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # Try extracting JSON array
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    # Try fixing trailing commas
    fixed = re.sub(r',(\s*[}\]])', r'\1', text)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # Try fixing single quotes
    try:
        fixed = text.replace("'", '"')
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    logger.warning("All JSON repair attempts failed.")
    return None

def safe_get_rich_text(props: Dict[str, Any], key: str, default: str = "") -> str:
    """Safely extract rich_text content from Notion page properties."""
    try:
        rt = props.get(key, {}).get("rich_text", [])
        if rt:
            return rt[0].get("text", {}).get("content", default)
    except (IndexError, AttributeError, TypeError):
        pass
    return default

def safe_get_select(props: Dict[str, Any], key: str, default: str = "") -> str:
    """Safely extract select name from Notion page properties."""
    try:
        sel = props.get(key, {}).get("select")
        if sel:
            return sel.get("name", default)
    except (AttributeError, TypeError):
        pass
    return default

def safe_get_title(props: Dict[str, Any], key: str = "Title", default: str = "") -> str:
    """Safely extract title content from Notion page properties."""
    try:
        ti = props.get(key, {}).get("title", [])
        if ti:
            return ti[0].get("text", {}).get("content", default)
    except (IndexError, AttributeError, TypeError):
        pass
    return default

def sanitize_filename(name: str, max_len: int = 100) -> str:
    """Sanitize a string for use as a filename."""
    safe = "".join(c for c in name if c.isalnum() or c in " -_").strip()
    safe = safe.replace(" ", "_")
    if len(safe) > max_len:
        safe = safe[:max_len]
    return safe

def sample_long_text(text: str, max_chars: int, sections: int = 3) -> str:
    """Keep evidence from beginning, middle, and end instead of silent head-only truncation."""
    if not text or len(text) <= max_chars:
        return text
    sections = max(1, sections)
    marker = "\n\n[... sampled gap ...]\n\n"
    available = max_chars - len(marker) * (sections - 1)
    chunk_size = max(1, available // sections)
    if sections == 1:
        return text[:max_chars]
    starts = [round(i * (len(text) - chunk_size) / (sections - 1)) for i in range(sections)]
    sampled = marker.join(text[start:start + chunk_size] for start in starts)
    return sampled[:max_chars]
