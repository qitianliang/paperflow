import os
from paperflow.pdf_locator import PDFLocator
from paperflow.cache import cache

def test_pdf_locator():
    print("Testing PDF Locator...")
    locator = PDFLocator()

    # Get the first item from cache to test
    cache_dir = "data/cache"
    if not os.path.exists(cache_dir):
        print("ERROR: Cache directory not found. Did fetch-zotero work?")
        return

    zotero_keys = [d for d in os.listdir(cache_dir) if os.path.isdir(os.path.join(cache_dir, d))]

    found_pdf = False
    for zotero_key in zotero_keys:
        metadata = cache.load_json(zotero_key, "metadata.json")
        if metadata and metadata.get("has_pdf"):
            print(f"Found paper with PDF: {metadata['title']} ({zotero_key})")
            attachment_key = metadata.get("pdf_attachment_key")

            # Use locator
            staged_path = locator.locate_and_stage_pdf(zotero_key, attachment_key)
            if staged_path and os.path.exists(staged_path):
                print(f"Successfully staged PDF to: {staged_path}")
                print("PDF Locator Test: SUCCESS")
                found_pdf = True
                break

    if not found_pdf:
        print("Warning: No papers with PDFs found in cache to test.")

if __name__ == "__main__":
    test_pdf_locator()