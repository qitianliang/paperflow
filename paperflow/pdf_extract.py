import os
import pypdf
from paperflow.config import get_config
from paperflow.logging_utils import get_logger

logger = get_logger(__name__)


def _sanitize_text(text: str) -> str:
    """Replace lone surrogate characters (U+D800..U+DFFF) with U+FFFD.

    pypdf sometimes produces broken surrogate pairs from malformed PDF fonts,
    causing encode('utf-8') to fail later in json/cache serialization.
    """
    return "".join(
        c if ord(c) < 0xD800 or ord(c) > 0xDFFF else "\ufffd"
        for c in text
    )


def _extract_via_pymupdf(pdf_path: str, max_pages: int, max_chars: int) -> str:
    """Primary extractor using PyMuPDF — higher quality, handles surrogate-free.

    Args:
        max_pages: Max pages to extract. <= 0 means all pages.
        max_chars: Max chars to return. > 0 means limit, <= 0 means no limit.
    """
    import fitz  # PyMuPDF
    text = ""
    doc = fitz.open(pdf_path)
    num_pages = doc.page_count if max_pages <= 0 else min(max_pages, doc.page_count)
    for i in range(num_pages):
        page_text = doc[i].get_text("text")
        if page_text:
            text += page_text + "\n"
        if max_chars > 0 and len(text) > max_chars + 5000:
            break
    doc.close()
    sanitized = _sanitize_text(text)
    return sanitized[:max_chars] if max_chars > 0 else sanitized


def _extract_via_pypdf(pdf_path: str, max_pages: int, max_chars: int) -> str:
    """Fallback extractor using pypdf.

    Args:
        max_pages: Max pages to extract. <= 0 means all pages.
        max_chars: Max chars to return. > 0 means limit, <= 0 means no limit.
    """
    text = ""
    with open(pdf_path, "rb") as f:
        reader = pypdf.PdfReader(f)
        num_pages = len(reader.pages) if max_pages <= 0 else min(max_pages, len(reader.pages))
        for i in range(num_pages):
            page_text = reader.pages[i].extract_text()
            if page_text:
                text += page_text + "\n"
            if max_chars > 0 and len(text) > max_chars + 5000:
                break
    sanitized = _sanitize_text(text)
    return sanitized[:max_chars] if max_chars > 0 else sanitized


class PDFExtractor:
    def __init__(self):
        self.config = get_config()
        self.max_chars = self.config.pdf.max_chars_for_deep_read

    def extract_text(self, pdf_path: str, max_pages: int = 20, max_chars: int = 0) -> str:
        """Extracts text from the original PDF using PyMuPDF (or pypdf as fallback).

        Args:
            max_pages: Max pages to extract. <= 0 means all pages.
            max_chars: Max chars to return. > 0 means limit to N chars,
                       0 means use config default (max_chars_for_deep_read),
                       < 0 means no limit (return full extracted text).
        """
        if not pdf_path or not os.path.exists(pdf_path):
            logger.warning(f"PDF path does not exist for extraction: {pdf_path}")
            return ""

        basename = os.path.basename(pdf_path)
        # max_chars > 0: explicit limit; 0: use default; < 0: no limit
        char_limit = max_chars if max_chars != 0 else self.max_chars

        # Try PyMuPDF first — better extraction quality and Unicode handling
        try:
            text = _extract_via_pymupdf(pdf_path, max_pages, char_limit)
            logger.info(f"Extracted {len(text)} chars from {basename} (pymupdf)")
            return text
        except Exception as e1:
            logger.warning(f"PyMuPDF extraction failed for {basename}: {e1}, falling back to pypdf")
            try:
                text = _extract_via_pypdf(pdf_path, max_pages, char_limit)
                logger.info(f"Extracted {len(text)} chars from {basename} (pypdf)")
                return text
            except Exception as e2:
                logger.error(f"All extractors failed for {basename}: {e2}")
                return ""
