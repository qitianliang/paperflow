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
    """Primary extractor using PyMuPDF — higher quality, handles surrogate-free."""
    import fitz  # PyMuPDF
    text = ""
    doc = fitz.open(pdf_path)
    num_pages = min(max_pages, doc.page_count)
    for i in range(num_pages):
        page_text = doc[i].get_text("text")
        if page_text:
            text += page_text + "\n"
        if len(text) > max_chars + 5000:
            break
    doc.close()
    return _sanitize_text(text)[:max_chars]


def _extract_via_pypdf(pdf_path: str, max_pages: int, max_chars: int) -> str:
    """Fallback extractor using pypdf."""
    text = ""
    with open(pdf_path, "rb") as f:
        reader = pypdf.PdfReader(f)
        num_pages = min(max_pages, len(reader.pages))
        for i in range(num_pages):
            page_text = reader.pages[i].extract_text()
            if page_text:
                text += page_text + "\n"
            if len(text) > max_chars + 5000:
                break
    return _sanitize_text(text)[:max_chars]


class PDFExtractor:
    def __init__(self):
        self.config = get_config()
        self.max_chars = self.config.pdf.max_chars_for_deep_read

    def extract_text(self, pdf_path: str, max_pages: int = 20, max_chars: int = 0) -> str:
        """Extracts text from the original PDF using PyMuPDF (or pypdf as fallback)."""
        if not pdf_path or not os.path.exists(pdf_path):
            logger.warning(f"PDF path does not exist for extraction: {pdf_path}")
            return ""

        basename = os.path.basename(pdf_path)
        char_limit = max_chars or self.max_chars

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
