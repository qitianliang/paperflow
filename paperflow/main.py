import os
import click
import hashlib
from paperflow.config import Config, get_config
from paperflow.logging_utils import setup_logging, get_logger
from paperflow.zotero_client import ZoteroClient
from paperflow.notion_client import NotionClientWrapper
from paperflow.cache import cache
import json

setup_logging()
logger = get_logger("paperflow.main")

@click.group()
def cli():
    """paperflow: A comprehensive literature workflow tool."""
    pass

def _collection_id(config: Config, provided: str = "") -> str:
    return provided or config.project.collection_id or config.zotero.collection_id

def _notion_sync_digest(config: Config, metadata, speed_card) -> str:
    payload = {
        "topic": config.project.topic_label,
        "metadata": metadata.model_dump(),
        "speed_card": speed_card.model_dump() if speed_card else None,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

def _obsidian_sync_digest(config: Config, metadata, speed_card, deep_read, mono_pdf: str, dual_pdf: str, formatter_context=None) -> str:
    payload = {
        "topic": config.project.topic_label,
        "metadata": metadata.model_dump(),
        "speed_card": speed_card.model_dump(),
        "deep_read": deep_read,
        "mono_pdf": mono_pdf,
        "dual_pdf": dual_pdf,
        "formatter": formatter_context or {},
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()

@cli.command()
def doctor():
    """Check environment and configurations."""
    logger.info("Running paperflow doctor...")
    try:
        config = get_config()
        logger.info("[OK] Configuration loaded")

        if config.zotero.api_key and config.zotero.user_id:
            logger.info("[OK] Zotero credentials found")
        else:
            logger.warning("[WARNING] Zotero credentials missing")

        if config.notion.token and config.notion.database_id:
            logger.info("[OK] Notion credentials found")
        else:
            logger.warning("[WARNING] Notion credentials missing")

        import subprocess
        import sys
        # Auto-detect pdf2zh: check env var, then conda Scripts, then PATH
        pdf2zh_exe = "pdf2zh"
        env_exe = os.environ.get("PDF2ZH_EXECUTABLE", "")
        conda_exe = os.path.join(os.path.dirname(sys.executable), "Scripts", "pdf2zh.exe")
        if env_exe and os.path.exists(env_exe):
            pdf2zh_exe = env_exe
        elif os.path.exists(conda_exe):
            pdf2zh_exe = conda_exe
        elif env_exe:
            pdf2zh_exe = env_exe  # as fallback (might be in PATH)
        try:
            result = subprocess.run([pdf2zh_exe, "--version"], capture_output=True, text=True, timeout=10)
            version = result.stdout.strip() or result.stderr.strip()
            logger.info(f"[OK] pdf2zh CLI is available ({version})")
        except FileNotFoundError:
            logger.warning("[WARNING] pdf2zh CLI not found in PATH")
        except Exception as e:
            logger.warning(f"[WARNING] pdf2zh CLI check failed: {e}")

    except Exception as e:
        logger.error(f"[ERROR] {e}")

@cli.command()
@click.option('--collection', required=False, help='Zotero Collection ID (defaults to config)')
def fetch_zotero(collection):
    """Fetch metadata from Zotero and cache locally."""
    config = get_config()
    collection_id = _collection_id(config, collection)
    if not collection_id:
        logger.error("No collection ID provided and default_collection_id is empty in config.")
        return

    client = ZoteroClient()
    items = client.get_collection_items(collection_id)

    count = 0
    for item in items:
        metadata = client.parse_item(item, items)
        if metadata:
            cache.save_json(metadata.zotero_key, "metadata.json", metadata.model_dump())
            count += 1

    logger.info(f"Fetched and cached {count} items from collection {collection_id}")

@cli.command()
@click.option('--collection', required=False, help='Zotero Collection ID (defaults to config)')
@click.option('--dry-run', is_flag=True, help='Do not actually sync to Notion')
def sync_notion(collection, dry_run):
    """Sync cached metadata to Notion database."""
    config = get_config()
    collection_id = _collection_id(config, collection)
    if not collection_id:
        logger.error("No collection ID provided and default_collection_id is empty in config.")
        return

    client = ZoteroClient()
    items = client.get_collection_items(collection_id)
    notion = NotionClientWrapper()
    if not dry_run:
        notion.prime_page_index()
    from paperflow.screening import Screener
    screener = Screener()

    count = 0
    for item in items:
        metadata = client.parse_item(item, items)
        if metadata:
            speed_card_data = cache.load_json(metadata.zotero_key, "speed_card.json")
            speed_card = None
            if speed_card_data:
                assessment = cache.load_json(metadata.zotero_key, "speed_card_input.json") or {}
                if assessment.get("signature") == screener.assessment_signature(metadata):
                    speed_card = screener.card_from_data(
                        speed_card_data,
                        has_full_text=assessment.get("has_full_text", False),
                    )
                else:
                    logger.info(
                        f"Stale speed card for {metadata.zotero_key}; "
                        "syncing metadata only until run-screening regenerates it"
                    )
            if dry_run:
                logger.info(f"[Dry Run] Would sync: {metadata.title}")
            else:
                digest = _notion_sync_digest(config, metadata, speed_card)
                previous_sync = cache.load_json(metadata.zotero_key, "notion_sync.json") or {}
                if previous_sync.get("digest") == digest:
                    logger.info(f"Notion unchanged for {metadata.zotero_key}, skipping sync")
                else:
                    notion.upsert_paper(metadata, speed_card=speed_card)
                    cache.save_json(metadata.zotero_key, "notion_sync.json", {"digest": digest})
            count += 1
    logger.info(f"Processed {count} items for Notion sync.")

@cli.command()
@click.option('--collection', required=False, help='Zotero Collection ID (defaults to config)')
def run_screening(collection):
    """Run the complete screening flow: fetch -> AI screen -> sync."""
    config = get_config()
    collection_id = _collection_id(config, collection)
    if not collection_id:
        logger.error("No collection ID provided and default_collection_id is empty in config.")
        return

    logger.info(f"Starting screening flow for collection {collection_id}")
    client = ZoteroClient()
    items = client.get_collection_items(collection_id)
    notion = NotionClientWrapper()
    notion.prime_page_index()

    from paperflow.screening import Screener
    from paperflow.pdf_locator import PDFLocator
    from paperflow.pdf_extract import PDFExtractor
    screener = Screener()
    locator = PDFLocator()
    extractor = PDFExtractor()

    count = 0
    failures = []
    for item in items:
        metadata = client.parse_item(item, items)
        if metadata:
            speed_card = None
            assessment_signature = screener.assessment_signature(metadata)
            speed_card_data = cache.load_json(metadata.zotero_key, "speed_card.json")
            assessment = cache.load_json(metadata.zotero_key, "speed_card_input.json") or {}
            cache_valid = (
                speed_card_data
                and assessment.get("signature") == assessment_signature
                and not config.cache.force_refresh
            )
            if cache_valid:
                speed_card = screener.card_from_data(
                    speed_card_data,
                    has_full_text=assessment.get("has_full_text", False),
                )
                cache.save_json(metadata.zotero_key, "speed_card.json", speed_card.model_dump())
                logger.info(f"Loaded existing speed card for {metadata.zotero_key}")
            else:
                if speed_card_data:
                    logger.info(f"Assessment inputs changed for {metadata.zotero_key}, regenerating speed card")
                # Extract PDF text for speed card context
                pdf_path = locator.locate_and_stage_pdf(metadata.zotero_key, metadata.pdf_attachment_key)
                paper_text = extractor.extract_text(
                    pdf_path,
                    max_pages=config.pdf.max_pages_for_speed_card,
                    max_chars=config.pdf.max_chars_for_speed_card,
                ) if pdf_path else ""
                if paper_text:
                    logger.info(f"Extracted {len(paper_text)} chars from PDF for {metadata.zotero_key}")
                else:
                    logger.info(f"No PDF text for {metadata.zotero_key}, using abstract-only speed card")
                speed_card = screener.generate_speed_card(metadata, paper_text=paper_text)
                if speed_card:
                    cache.save_json(metadata.zotero_key, "speed_card.json", speed_card.model_dump())
                    cache.save_json(metadata.zotero_key, "speed_card_input.json", {
                        "signature": assessment_signature,
                        "has_full_text": bool(paper_text),
                    })
                else:
                    failures.append(metadata.zotero_key)

            cache.save_json(metadata.zotero_key, "metadata.json", metadata.model_dump())
            digest = _notion_sync_digest(config, metadata, speed_card)
            previous_sync = cache.load_json(metadata.zotero_key, "notion_sync.json") or {}
            if previous_sync.get("digest") == digest:
                logger.info(f"Notion unchanged for {metadata.zotero_key}, skipping sync")
            else:
                notion.upsert_paper(metadata, speed_card=speed_card)
                cache.save_json(metadata.zotero_key, "notion_sync.json", {"digest": digest})
            count += 1

    logger.info(f"Screening flow completed. Processed {count} items.")
    if failures:
        raise click.ClickException(
            f"Speed card generation failed for {len(failures)} papers: {', '.join(failures)}"
        )

@cli.command()
@click.option('--collection', required=False, help='Zotero Collection ID (defaults to config)')
def batch_compare(collection):
    """Run batch comparison across papers in a collection."""
    config = get_config()
    collection_id = _collection_id(config, collection)
    if not collection_id:
        logger.error("No collection ID provided and default_collection_id is empty in config.")
        return

    logger.info(f"Running batch comparison for collection {collection_id}...")
    client = ZoteroClient()
    items = client.get_collection_items(collection_id)

    from paperflow.batch_compare import BatchComparer
    comparer = BatchComparer()

    zotero_keys = []
    for item in items:
        metadata = client.parse_item(item, items)
        if metadata:
            zotero_keys.append(metadata.zotero_key)

    if not zotero_keys:
        logger.warning("No papers found in collection.")
        return

    result = comparer.compare_collection(zotero_keys)
    if result:
        logger.info(f"Batch comparison completed for {len(zotero_keys)} papers.")
        # Also cache the result globally
        cache.save_json("_global", "batch_compare.json", result)
    else:
        logger.error("Batch comparison failed.")

@cli.command()
def queue_translation():
    """Queue 'Must Read' papers for translation."""
    logger.info("Building translation queue from 'Must Read' papers...")
    from paperflow.translation_queue import TranslationQueue
    qm = TranslationQueue()
    queue = qm.build_queue()
    logger.info(f"Queue built: {len(queue)} items total.")

@cli.command()
@click.option('--model-tier', default=None, type=click.Choice(['cheap', 'balanced', 'strong']), help='Translation model tier (default: from config)')
def translate_queued(model_tier):
    """Run all queued translation jobs sequentially."""
    logger.info("Processing translation queue...")
    from paperflow.translation_queue import TranslationQueue
    from paperflow.pdf2zh_runner import PDF2ZHRunner
    from paperflow.notion_client import NotionClientWrapper
    from paperflow.cache import cache as paper_cache

    qm = TranslationQueue()
    runner = PDF2ZHRunner(model_tier=model_tier)
    notion = NotionClientWrapper()
    queue = qm.load_queue()

    pending = [item for item in queue if item.get("status") == "pending"]
    if not pending:
        logger.info("No pending translations in queue.")
        return

    logger.info(f"Found {len(pending)} pending translations.")
    for item in pending:
        zotero_key = item["zotero_key"]
        staged_path = item.get("staged_path", "")
        if not staged_path or not os.path.exists(staged_path):
            logger.error(f"Staged PDF not found for {zotero_key}, skipping.")
            item["status"] = "failed"
            item["error"] = "Staged PDF not found"
            notion.update_translation_status(item["page_id"], "Failed", error_msg="Staged PDF not found")
            qm.save_queue(queue)
            continue

        item["status"] = "running"
        qm.save_queue(queue)
        notion.update_translation_status(item["page_id"], "Running")
        logger.info(f"Translating {zotero_key}...")

        success, mono_pdf, dual_pdf, error = runner.run_translation(zotero_key, staged_path)

        if success:
            item["status"] = "done"
            item["mono_pdf"] = mono_pdf
            item["dual_pdf"] = dual_pdf
            notion.update_translation_status(item["page_id"], "Done", mono_pdf=mono_pdf, dual_pdf=dual_pdf)
            logger.info(f"Successfully translated {zotero_key}")
        else:
            item["status"] = "failed"
            item["error"] = error
            notion.update_translation_status(item["page_id"], "Failed", error_msg=error)
            logger.error(f"Translation failed for {zotero_key}: {error}")

        qm.save_queue(queue)

    logger.info("Translation queue processing completed.")

@cli.command()
@click.option('--zotero-key', required=True, help='Zotero Key of the paper to translate')
@click.option('--model-tier', default=None, type=click.Choice(['cheap', 'balanced', 'strong']), help='Translation model tier (default: from config)')
def translate_one(zotero_key, model_tier):
    """Translate a single paper's PDF."""
    logger.info(f"Translating single paper {zotero_key}...")
    from paperflow.pdf2zh_runner import PDF2ZHRunner
    from paperflow.pdf_locator import PDFLocator
    from paperflow.cache import cache as paper_cache

    metadata = paper_cache.load_json(zotero_key, "metadata.json")
    if not metadata:
        logger.error(f"No metadata found for {zotero_key}. Run fetch-zotero first.")
        return

    attachment_key = metadata.get("pdf_attachment_key", "")
    locator = PDFLocator()
    staged_path = locator.locate_and_stage_pdf(zotero_key, attachment_key)

    if not staged_path:
        logger.error(f"Could not locate PDF for {zotero_key}.")
        return

    runner = PDF2ZHRunner(model_tier=model_tier)
    success, mono_pdf, dual_pdf, error = runner.run_translation(zotero_key, staged_path)

    if success:
        logger.info(f"Translation succeeded for {zotero_key}")
        logger.info(f"  Mono PDF: {mono_pdf}")
        logger.info(f"  Dual PDF: {dual_pdf}")

        # Update Notion if possible
        try:
            from paperflow.notion_client import NotionClientWrapper
            notion = NotionClientWrapper()
            page = notion.find_page_by_zotero_key(zotero_key)
            if page:
                notion.update_translation_status(page["id"], "Done", mono_pdf=mono_pdf, dual_pdf=dual_pdf)
        except Exception:
            pass
    else:
        logger.error(f"Translation failed for {zotero_key}: {error}")

@cli.command()
def sync_translation_status():
    """Sync translation status from queue back to Notion."""
    logger.info("Syncing translation status to Notion...")
    from paperflow.translation_queue import TranslationQueue
    from paperflow.notion_client import NotionClientWrapper

    qm = TranslationQueue()
    queue = qm.load_queue()
    notion = NotionClientWrapper()

    updated = 0
    for item in queue:
        page_id = item.get("page_id", "")
        if not page_id:
            continue
        status = item.get("status", "pending")
        status_map = {
            "pending": "Queued",
            "running": "Running",
            "done": "Done",
            "failed": "Failed",
        }
        notion_status = status_map.get(status, "Pending")
        mono_pdf = item.get("mono_pdf", "")
        dual_pdf = item.get("dual_pdf", "")
        error = item.get("error", "")
        notion.update_translation_status(page_id, notion_status, mono_pdf=mono_pdf, dual_pdf=dual_pdf, error_msg=error)
        updated += 1

    logger.info(f"Synced {updated} items to Notion.")

@cli.command()
def deep_read():
    """Run AI deep reading on 'Must Read' papers."""
    logger.info("Running deep reading on 'Must Read' papers (independent of translation)...")
    from paperflow.deep_read import DeepReader
    from paperflow.schemas import PaperMetadata
    from paperflow.screening import Screener
    from paperflow.pdf_locator import PDFLocator
    from paperflow.pdf_extract import PDFExtractor

    notion = NotionClientWrapper()
    # We query Notion directly for Must Read papers, ignoring translation status
    papers = notion.list_topic_pages(filter={
        "property": "Human Decision",
        "select": {"equals": "Must Read"}
    })

    logger.info(f"Found {len(papers)} 'Must Read' papers for deep read.")

    reader = DeepReader()
    locator = PDFLocator()
    extractor = PDFExtractor()
    screener = Screener()

    for paper in papers:
        props = paper.get("properties", {})
        try:
            zotero_key = props.get("Zotero Key", {}).get("rich_text", [])[0].get("text", {}).get("content", "")
        except IndexError:
            continue

        if not zotero_key:
            continue

        metadata_data = cache.load_json(zotero_key, "metadata.json")
        speed_card_data = cache.load_json(zotero_key, "speed_card.json")

        if not metadata_data or not speed_card_data:
            logger.warning(f"Missing metadata or speed card for {zotero_key}, skipping deep read.")
            continue

        metadata = PaperMetadata(**metadata_data)
        assessment = cache.load_json(zotero_key, "speed_card_input.json") or {}
        speed_card = screener.card_from_data(
            speed_card_data, has_full_text=assessment.get("has_full_text", False)
        )
        deep_signature = reader.assessment_signature(metadata, speed_card)
        deep_input = cache.load_json(zotero_key, "deep_read_input.json") or {}
        if cache.load_json(zotero_key, "deep_read.json") and deep_input.get("signature") == deep_signature:
            logger.info(f"Deep read unchanged for {zotero_key}, skipping.")
            continue

        # Locate original PDF
        try:
            notion_local_path = props.get("Local PDF Path", {}).get("rich_text", [])[0].get("text", {}).get("content", "")
        except IndexError:
            notion_local_path = ""

        original_pdf_path = locator.locate_and_stage_pdf(zotero_key, metadata.pdf_attachment_key, notion_local_path)

        paper_text = ""
        if original_pdf_path:
            paper_text = extractor.extract_text(original_pdf_path)

        deep_read_result = reader.generate_deep_read(metadata, speed_card, paper_text)
        if deep_read_result:
            cache.save_json(zotero_key, "deep_read.json", deep_read_result)
            cache.save_json(zotero_key, "deep_read_input.json", {"signature": deep_signature})
            # Optionally update status in Notion
            notion._update_page(paper["id"], {"Status": {"select": {"name": "Deep Reading Done"}}})
            logger.info(f"Successfully deep read {zotero_key}")

@cli.command()
def export_obsidian():
    """Export 'Must Read' papers to Obsidian notes."""
    logger.info("Exporting 'Must Read' papers to Obsidian...")
    from paperflow.obsidian_export import ObsidianExporter
    from paperflow.schemas import PaperMetadata
    from paperflow.screening import Screener

    config = get_config()
    notion = NotionClientWrapper()
    papers = notion.list_topic_pages(filter={
        "property": "Human Decision",
        "select": {"equals": "Must Read"}
    })

    exporter = ObsidianExporter()
    screener = Screener()

    for paper in papers:
        props = paper.get("properties", {})
        try:
            zotero_key = props.get("Zotero Key", {}).get("rich_text", [])[0].get("text", {}).get("content", "")
        except IndexError:
            continue

        if not zotero_key:
            continue

        metadata_data = cache.load_json(zotero_key, "metadata.json")
        speed_card_data = cache.load_json(zotero_key, "speed_card.json")
        deep_read_data = cache.load_json(zotero_key, "deep_read.json")

        if not all([metadata_data, speed_card_data, deep_read_data]):
            logger.warning(f"Incomplete AI data for {zotero_key} (needs speed card & deep read), skipping export.")
            continue

        metadata = PaperMetadata(**metadata_data)
        assessment = cache.load_json(zotero_key, "speed_card_input.json") or {}
        speed_card = screener.card_from_data(
            speed_card_data, has_full_text=assessment.get("has_full_text", False)
        )

        # Pull translated paths from Notion if they exist (they might not, which is fine)
        try:
            mono_pdf = props.get("Translated Mono PDF", {}).get("rich_text", [])[0].get("text", {}).get("content", "")
        except IndexError:
            mono_pdf = ""
        try:
            dual_pdf = props.get("Translated Dual PDF", {}).get("rich_text", [])[0].get("text", {}).get("content", "")
        except IndexError:
            dual_pdf = ""

        digest = _obsidian_sync_digest(
            config, metadata, speed_card, deep_read_data, mono_pdf, dual_pdf,
            formatter_context=exporter.cache_context(),
        )
        previous = cache.load_json(zotero_key, "obsidian_sync.json") or {}
        if previous.get("digest") == digest:
            logger.info(f"Obsidian note unchanged for {zotero_key}, skipping export")
            continue
        success = exporter.export_note(
            metadata=metadata, speed_card=speed_card, deep_read=deep_read_data,
            mono_pdf=mono_pdf, dual_pdf=dual_pdf
        )

        if success:
             cache.save_json(zotero_key, "obsidian_sync.json", {"digest": digest})
             notion._update_page(paper["id"], {"Status": {"select": {"name": "Exported to Obsidian"}}})

@cli.command()
def attach_translations_to_zotero():
    """Optionally attach translated PDFs back to Zotero."""
    logger.info("Attaching translations back to Zotero...")
    from paperflow.translation_queue import TranslationQueue
    from paperflow.zotero_attach import ZoteroAttacher

    q_manager = TranslationQueue()
    queue = q_manager.load_queue()
    attacher = ZoteroAttacher()

    done_items = [item for item in queue if item.get("status") == "done"]

    for item in done_items:
        zotero_key = item["zotero_key"]
        mono = item.get("mono_pdf", "")
        dual = item.get("dual_pdf", "")
        attacher.attach_pdfs(zotero_key, mono, dual)

@cli.command()
@click.option('--translate', is_flag=True, help='Enable PDF translation step (disabled by default)')
@click.option('--model-tier', default=None, type=click.Choice(['cheap', 'balanced', 'strong']), help='Translation model tier (default: from config)')
def run_must_read(translate, model_tier):
    """Run the Must Read flow (queue -> [translate] -> deep read -> export).

    Translation is disabled by default. Use --translate to enable it.
    """
    logger.info("Running 'Must Read' flow...")
    from paperflow.translation_queue import TranslationQueue
    from paperflow.notion_client import NotionClientWrapper
    notion = NotionClientWrapper()

    # Step 1: Queue translation (always build queue, only run if --translate)
    logger.info("--- Step 1: Queue Translation ---")
    qm = TranslationQueue()
    qm.build_queue()

    if translate:
        logger.info("--- Step 2: Translate Queued (--translate enabled) ---")
        from paperflow.pdf2zh_runner import PDF2ZHRunner
        runner = PDF2ZHRunner(model_tier=model_tier)
        queue = qm.load_queue()
        pending = [item for item in queue if item.get("status") == "pending"]
        for item in pending:
            zotero_key = item["zotero_key"]
            staged_path = item.get("staged_path", "")
            if not staged_path or not os.path.exists(staged_path):
                logger.error(f"Staged PDF not found for {zotero_key}, skipping.")
                item["status"] = "failed"
                item["error"] = "Staged PDF not found"
                notion.update_translation_status(item["page_id"], "Failed", error_msg="Staged PDF not found")
                qm.save_queue(queue)
                continue
            item["status"] = "running"
            qm.save_queue(queue)
            notion.update_translation_status(item["page_id"], "Running")
            success, mono_pdf, dual_pdf, error = runner.run_translation(zotero_key, staged_path)
            if success:
                item["status"] = "done"
                item["mono_pdf"] = mono_pdf
                item["dual_pdf"] = dual_pdf
                notion.update_translation_status(item["page_id"], "Done", mono_pdf=mono_pdf, dual_pdf=dual_pdf)
            else:
                item["status"] = "failed"
                item["error"] = error
                notion.update_translation_status(item["page_id"], "Failed", error_msg=error)
            qm.save_queue(queue)
    else:
        logger.info("--- Step 2: Translate (skipped, use --translate to enable) ---")

    # Step 3: Deep Read
    logger.info("--- Step 3: Deep Read ---")
    from paperflow.deep_read import DeepReader
    from paperflow.schemas import PaperMetadata
    from paperflow.screening import Screener
    from paperflow.pdf_locator import PDFLocator
    from paperflow.pdf_extract import PDFExtractor
    reader = DeepReader()
    locator = PDFLocator()
    extractor = PDFExtractor()
    screener = Screener()
    try:
        must_read_papers = notion.list_topic_pages(filter={
            "property": "Human Decision", "select": {"equals": "Must Read"}
        })
    except Exception as e:
        logger.error(f"Failed to query Notion for Must Read papers (after retries): {e}")
        must_read_papers = []
    for paper in must_read_papers:
        props = paper.get("properties", {})
        try:
            zotero_key = props.get("Zotero Key", {}).get("rich_text", [])[0].get("text", {}).get("content", "")
        except IndexError:
            continue
        if not zotero_key:
            continue
        metadata_data = cache.load_json(zotero_key, "metadata.json")
        speed_card_data = cache.load_json(zotero_key, "speed_card.json")
        if not metadata_data or not speed_card_data:
            logger.warning(f"Missing metadata or speed card for {zotero_key}, skipping deep read.")
            continue
        metadata = PaperMetadata(**metadata_data)
        assessment = cache.load_json(zotero_key, "speed_card_input.json") or {}
        speed_card = screener.card_from_data(
            speed_card_data, has_full_text=assessment.get("has_full_text", False)
        )
        deep_signature = reader.assessment_signature(metadata, speed_card)
        deep_input = cache.load_json(zotero_key, "deep_read_input.json") or {}
        if cache.load_json(zotero_key, "deep_read.json") and deep_input.get("signature") == deep_signature:
            logger.info(f"Deep read unchanged for {zotero_key}, skipping.")
            continue
        try:
            notion_local_path = props.get("Local PDF Path", {}).get("rich_text", [])[0].get("text", {}).get("content", "")
        except IndexError:
            notion_local_path = ""
        original_pdf_path = locator.locate_and_stage_pdf(zotero_key, metadata.pdf_attachment_key, notion_local_path)
        paper_text = extractor.extract_text(original_pdf_path) if original_pdf_path else ""
        deep_read_result = reader.generate_deep_read(metadata, speed_card, paper_text)
        if deep_read_result:
            cache.save_json(zotero_key, "deep_read.json", deep_read_result)
            cache.save_json(zotero_key, "deep_read_input.json", {"signature": deep_signature})
            notion._update_page(paper["id"], {"Status": {"select": {"name": "Deep Reading Done"}}})
            logger.info(f"Successfully deep read {zotero_key}")

    # Step 4: Export to Obsidian
    logger.info("--- Step 4: Export to Obsidian ---")
    from paperflow.obsidian_export import ObsidianExporter
    exporter = ObsidianExporter()
    for paper in must_read_papers:
        props = paper.get("properties", {})
        try:
            zotero_key = props.get("Zotero Key", {}).get("rich_text", [])[0].get("text", {}).get("content", "")
        except IndexError:
            continue
        if not zotero_key:
            continue
        metadata_data = cache.load_json(zotero_key, "metadata.json")
        speed_card_data = cache.load_json(zotero_key, "speed_card.json")
        deep_read_data = cache.load_json(zotero_key, "deep_read.json")
        if not all([metadata_data, speed_card_data, deep_read_data]):
            continue
        metadata = PaperMetadata(**metadata_data)
        assessment = cache.load_json(zotero_key, "speed_card_input.json") or {}
        speed_card = screener.card_from_data(
            speed_card_data, has_full_text=assessment.get("has_full_text", False)
        )
        try:
            mono_pdf = props.get("Translated Mono PDF", {}).get("rich_text", [])[0].get("text", {}).get("content", "")
        except IndexError:
            mono_pdf = ""
        try:
            dual_pdf = props.get("Translated Dual PDF", {}).get("rich_text", [])[0].get("text", {}).get("content", "")
        except IndexError:
            dual_pdf = ""
        digest = _obsidian_sync_digest(
            get_config(), metadata, speed_card, deep_read_data, mono_pdf, dual_pdf,
            formatter_context=exporter.cache_context(),
        )
        previous = cache.load_json(zotero_key, "obsidian_sync.json") or {}
        if previous.get("digest") == digest:
            logger.info(f"Obsidian note unchanged for {zotero_key}, skipping export")
            continue
        success = exporter.export_note(metadata=metadata, speed_card=speed_card, deep_read=deep_read_data, mono_pdf=mono_pdf, dual_pdf=dual_pdf)
        if success:
            cache.save_json(zotero_key, "obsidian_sync.json", {"digest": digest})
            notion._update_page(paper["id"], {"Status": {"select": {"name": "Exported to Obsidian"}}})

    logger.info("'Must Read' flow completed.")

@cli.command()
def list_cache():
    """List cached papers."""
    cache_dir = cache.base_dir
    if not os.path.exists(cache_dir):
        logger.info("Cache directory is empty or does not exist.")
        return
    keys = [d for d in os.listdir(cache_dir) if os.path.isdir(os.path.join(cache_dir, d))]
    if not keys:
        logger.info("No cached papers found.")
        return
    logger.info(f"Found {len(keys)} cached papers:")
    for key in sorted(keys)[:20]:
        meta = cache.load_json(key, "metadata.json")
        title = meta.get("title", "?") if meta else "?"
        statuses = []
        for f in ["speed_card.json", "deep_read.json", "batch_compare.json"]:
            if cache.load_json(key, f):
                statuses.append(f.replace(".json", ""))
        logger.info(f"  {key}: {title[:60]} [{', '.join(statuses) if statuses else 'metadata only'}]")
    if len(keys) > 20:
        logger.info(f"  ... and {len(keys) - 20} more.")

@cli.command()
@click.option('--zotero-key', required=True, help='Zotero Key')
def clear_cache(zotero_key):
    """Clear cache for a specific paper."""
    import shutil
    key_dir = os.path.join(cache.base_dir, zotero_key)
    if os.path.exists(key_dir):
        shutil.rmtree(key_dir)
        logger.info(f"Cleared cache for {zotero_key}")
    else:
        logger.warning(f"No cache found for {zotero_key}")

@cli.command()
def retry_failed():
    """Retry failed translation tasks."""
    logger.info("Retrying failed translation tasks...")
    from paperflow.translation_queue import TranslationQueue
    from paperflow.pdf2zh_runner import PDF2ZHRunner
    from paperflow.notion_client import NotionClientWrapper
    from paperflow.cache import cache as paper_cache

    qm = TranslationQueue()
    runner = PDF2ZHRunner()
    notion = NotionClientWrapper()
    queue = qm.load_queue()

    failed = [item for item in queue if item.get("status") == "failed" and item.get("retry_count", 0) < 3]
    if not failed:
        logger.info("No failed items to retry.")
        return

    logger.info(f"Retrying {len(failed)} failed items...")
    for item in failed:
        item["retry_count"] = item.get("retry_count", 0) + 1
        zotero_key = item["zotero_key"]
        staged_path = item.get("staged_path", "")
        if not staged_path or not os.path.exists(staged_path):
            logger.error(f"Staged PDF not found for {zotero_key}, cannot retry.")
            continue

        logger.info(f"Retry {item['retry_count']} for {zotero_key}...")
        item["status"] = "running"
        qm.save_queue(queue)
        success, mono_pdf, dual_pdf, error = runner.run_translation(zotero_key, staged_path)
        if success:
            item["status"] = "done"
            item["mono_pdf"] = mono_pdf
            item["dual_pdf"] = dual_pdf
            item["error"] = ""
            notion.update_translation_status(item["page_id"], "Done", mono_pdf=mono_pdf, dual_pdf=dual_pdf)
            logger.info(f"Retry succeeded for {zotero_key}")
        else:
            item["status"] = "failed"
            item["error"] = error
            notion.update_translation_status(item["page_id"], "Failed", error_msg=error)
            logger.error(f"Retry failed for {zotero_key}: {error}")
        qm.save_queue(queue)

@cli.command()
def show_config():
    """Show current configuration."""
    config = get_config()
    logger.info(json.dumps(config.raw_config, indent=2, ensure_ascii=False))

if __name__ == '__main__':
    cli()
