"""Test speed card generation for a single paper."""
from paperflow.config import get_config
from paperflow.cache import cache
from paperflow.schemas import PaperMetadata

zotero_key = "27IA7C3Y"
meta_data = cache.load_json(zotero_key, "metadata.json")

if not meta_data:
    print(f"No metadata found for {zotero_key}")
    exit(1)

metadata = PaperMetadata(**meta_data)
print(f"Testing speed card generation for: {metadata.title}")

# Check if speed card already exists
existing = cache.load_json(zotero_key, "speed_card.json")
if existing:
    print(f"Speed card already exists for {zotero_key}")
    print(f'  Suggestion: {existing.get("ai_suggestion", "?")}')
    print(f'  Score: {existing.get("priority_score", "?")}')
else:
    from paperflow.screening import Screener
    screener = Screener()
    result = screener.generate_speed_card(metadata, paper_text="")
    if result:
        cache.save_json(zotero_key, "speed_card.json", result.model_dump())
        print(f"Generated speed card: {result.ai_suggestion}")
    else:
        print("Failed to generate speed card")
