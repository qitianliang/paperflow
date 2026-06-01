import os
import click
import hashlib
from concurrent.futures import ThreadPoolExecutor
from paperflow.config import Config, get_config
from paperflow.logging_utils import setup_logging, get_logger
from paperflow.zotero_client import ZoteroClient
from paperflow.notion_client import NotionClientWrapper
from paperflow.schemas import PaperMetadata, SpeedCard
from paperflow.cache import cache
import json

setup_logging()
logger = get_logger("paperflow.main")


class DefaultCommandGroup(click.Group):
    """Click Group that runs `enrich-paper` as the default command
    when no known subcommand is given.

    Examples:
        paperflow --title "Think2Go..." --fake-no-code   -> enrich-paper
        paperflow doctor                                   -> doctor
        paperflow --help                                   -> group help
    """
    def __init__(self, default_cmd_name="enrich-paper", *args, **kwargs):
        self.default_cmd_name = default_cmd_name
        super().__init__(*args, **kwargs)

    def parse_args(self, ctx, args):
        # If no args or help requested, show group help as usual
        if not args or args[0] in ("--help", "-h"):
            return super().parse_args(ctx, args)
        # If first arg is a known subcommand, route normally
        if args[0] in self.commands:
            return super().parse_args(ctx, args)
        # Otherwise default to enrich-paper
        args.insert(0, self.default_cmd_name)
        return super().parse_args(ctx, args)


@click.group(cls=DefaultCommandGroup, invoke_without_command=True)
def cli():
    """paperflow: A comprehensive literature workflow tool.

    When run without a subcommand, defaults to `enrich-paper`.
    """
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
@click.option('--from-cache', is_flag=True, help='Sync from local cache instead of calling Zotero API')
def sync_notion(collection, dry_run, from_cache):
    """Sync cached metadata to Notion database."""
    config = get_config()
    notion = NotionClientWrapper()
    if not dry_run:
        notion.prime_page_index()
    from paperflow.screening import Screener
    screener = Screener()

    def _sync_one(metadata: PaperMetadata) -> bool:
        """Sync a single paper to Notion. Returns True if processed."""
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
        return True

    count = 0
    if from_cache:
        cache_dir = cache.base_dir
        if not os.path.exists(cache_dir):
            logger.info("Cache directory does not exist.")
            return
        zotero_keys = [
            d for d in os.listdir(cache_dir)
            if os.path.isdir(os.path.join(cache_dir, d)) and d != "_global"
        ]
        if not zotero_keys:
            logger.info("No cached papers found.")
            return
        logger.info(f"Syncing {len(zotero_keys)} papers from local cache...")
        for zotero_key in zotero_keys:
            metadata_data = cache.load_json(zotero_key, "metadata.json")
            if not metadata_data:
                continue
            try:
                metadata = PaperMetadata(**metadata_data)
            except Exception as e:
                logger.warning(f"Failed to parse metadata for {zotero_key}: {e}")
                continue
            if _sync_one(metadata):
                count += 1
    else:
        collection_id = _collection_id(config, collection)
        if not collection_id:
            logger.error("No collection ID provided and default_collection_id is empty in config.")
            return
        client = ZoteroClient()
        items = client.get_collection_items(collection_id)

        # 加载本地缓存的 keys
        cache_dir = cache.base_dir
        cached_keys = set()
        if os.path.exists(cache_dir):
            cached_keys = {
                d for d in os.listdir(cache_dir)
                if os.path.isdir(os.path.join(cache_dir, d)) and d != "_global"
            }

        # 处理 Zotero items（缓存优先）
        zotero_keys = set()
        for item in items:
            data = item.get('data', {})
            zotero_key = data.get('key', '')
            zotero_keys.add(zotero_key)

            metadata = None
            # 优先使用本地缓存，避免重复解析
            if zotero_key:
                metadata_data = cache.load_json(zotero_key, "metadata.json")
                if metadata_data:
                    try:
                        metadata = PaperMetadata(**metadata_data)
                        logger.debug(f"Using cached metadata for {zotero_key}")
                    except Exception:
                        pass

            # 缓存中没有，从 Zotero item 解析
            if not metadata:
                metadata = client.parse_item(item, items)
                if metadata:
                    cache.save_json(metadata.zotero_key, "metadata.json", metadata.model_dump())

            if metadata and _sync_one(metadata):
                count += 1

        # 处理只在本地缓存中的论文（如 Zotero 中已删除但想保留在 Notion 中）
        only_in_cache = cached_keys - zotero_keys
        if only_in_cache:
            logger.info(f"Syncing {len(only_in_cache)} papers that exist only in local cache...")
            for zotero_key in only_in_cache:
                metadata_data = cache.load_json(zotero_key, "metadata.json")
                if metadata_data:
                    try:
                        metadata = PaperMetadata(**metadata_data)
                        if _sync_one(metadata):
                            count += 1
                    except Exception as e:
                        logger.warning(f"Failed to sync cached-only paper {zotero_key}: {e}")
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
    def assess_paper(metadata):
        screener = Screener()
        locator = PDFLocator()
        extractor = PDFExtractor()
        try:
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
                if config.pdf.speed_card_full_text:
                    # Full-text mode: extract all pages, no char limit
                    paper_text = extractor.extract_text(
                        pdf_path,
                        max_pages=0,
                        max_chars=-1,
                    ) if pdf_path else ""
                    if paper_text:
                        logger.info(f"Full-text mode: extracted {len(paper_text)} chars from PDF for {metadata.zotero_key}")
                else:
                    paper_text = extractor.extract_text(
                        pdf_path,
                        max_pages=config.pdf.max_pages_for_speed_card,
                        max_chars=config.pdf.max_chars_for_speed_card,
                    ) if pdf_path else ""
                    if paper_text:
                        logger.info(f"Extracted {len(paper_text)} chars from PDF for {metadata.zotero_key}")
                if not paper_text:
                    logger.info(f"No PDF text for {metadata.zotero_key}, using abstract-only speed card")
                speed_card = screener.generate_speed_card(metadata, paper_text=paper_text)
                if speed_card:
                    cache.save_json(metadata.zotero_key, "speed_card.json", speed_card.model_dump())
                    cache.save_json(metadata.zotero_key, "speed_card_input.json", {
                        "signature": assessment_signature,
                        "has_full_text": bool(paper_text),
                    })
            return metadata, speed_card, speed_card is None
        except Exception as e:
            logger.exception(f"Screening failed for {metadata.zotero_key}: {e}")
            return metadata, None, True

    metadata_items = []
    for item in items:
        metadata = client.parse_item(item, items)
        if metadata is not None:
            metadata_items.append(metadata)
    max_workers = config.screening.max_concurrent_papers
    logger.info(f"Screening {len(metadata_items)} papers with {max_workers} worker(s)")
    failures = []
    count = 0
    if max_workers > 1:
        executor = ThreadPoolExecutor(max_workers=max_workers)
        outcomes = executor.map(assess_paper, metadata_items)
    else:
        executor = None
        outcomes = map(assess_paper, metadata_items)
    screened_papers: list[tuple[PaperMetadata, SpeedCard]] = []
    try:
        for metadata, speed_card, failed in outcomes:
            if failed:
                failures.append(metadata.zotero_key)
                logger.warning(f"Speed card generation failed for {metadata.zotero_key}, skipping Notion sync")
            else:
                cache.save_json(metadata.zotero_key, "metadata.json", metadata.model_dump())
                digest = _notion_sync_digest(config, metadata, speed_card)
                previous_sync = cache.load_json(metadata.zotero_key, "notion_sync.json") or {}
                if previous_sync.get("digest") == digest:
                    logger.info(f"Notion unchanged for {metadata.zotero_key}, skipping sync")
                else:
                    notion.upsert_paper(metadata, speed_card=speed_card)
                    cache.save_json(metadata.zotero_key, "notion_sync.json", {"digest": digest})
                if speed_card:
                    screened_papers.append((metadata, speed_card))
            count += 1
    finally:
        if executor:
            executor.shutdown(wait=True)

    # 数量核验：确保 Notion 中的论文数量与 Zotero 一致
    zotero_count = len(metadata_items)
    notion_count = count
    logger.info(f"Zotero collection has {zotero_count} papers, Notion synced {notion_count} papers.")
    if zotero_count != notion_count:
        logger.warning(f"Count mismatch: Zotero={zotero_count}, Notion={notion_count}")

    # 自动标记 top N 为 Must Read
    auto_mark = config.screening.auto_mark_top_n
    if auto_mark > 0 and screened_papers:
        screened_papers.sort(key=lambda pair: pair[1].priority_score, reverse=True)
        top_papers = screened_papers[:auto_mark]
        logger.info(f"Auto-marking top {len(top_papers)} paper(s) as Must Read (threshold: top {auto_mark})")
        for metadata, speed_card in top_papers:
            try:
                page = notion.find_page_by_zotero_key(metadata.zotero_key)
                if page:
                    notion._update_page(page["id"], {
                        "Human Decision": {"select": {"name": "Must Read"}},
                        "Status": {"select": {"name": "Must Read Confirmed"}},
                    })
                    logger.info(f"Auto-marked {metadata.zotero_key} as Must Read (Priority Score: {speed_card.priority_score})")
                else:
                    logger.warning(f"Could not find Notion page for {metadata.zotero_key} to auto-mark Must Read")
            except Exception as e:
                logger.error(f"Failed to auto-mark Must Read for {metadata.zotero_key}: {e}")

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
    from paperflow.translation_artifacts import TranslationArtifacts

    qm = TranslationQueue()
    runner = PDF2ZHRunner(model_tier=model_tier)
    notion = NotionClientWrapper()
    artifacts = TranslationArtifacts()
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
            source_mono_pdf, source_dual_pdf = mono_pdf, dual_pdf
            mono_pdf, dual_pdf = artifacts.publish(zotero_key, mono_pdf, dual_pdf)
            item["status"] = "done"
            item["source_mono_pdf"] = source_mono_pdf
            item["source_dual_pdf"] = source_dual_pdf
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
    from paperflow.translation_artifacts import TranslationArtifacts

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
        mono_pdf, dual_pdf = TranslationArtifacts().publish(zotero_key, mono_pdf, dual_pdf)
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
    from paperflow.translation_artifacts import TranslationArtifacts

    qm = TranslationQueue()
    queue = qm.load_queue()
    notion = NotionClientWrapper()
    artifacts = TranslationArtifacts()

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
        if status == "done":
            mono_pdf, dual_pdf = artifacts.publish(item["zotero_key"], mono_pdf, dual_pdf)
            item["mono_pdf"] = mono_pdf
            item["dual_pdf"] = dual_pdf
        notion.update_translation_status(page_id, notion_status, mono_pdf=mono_pdf, dual_pdf=dual_pdf, error_msg=error)
        updated += 1

    qm.save_queue(queue)
    logger.info(f"Synced {updated} items to Notion.")

@cli.command()
def deep_read():
    """Run AI deep reading on configured ranked and human-selected papers."""
    logger.info("Running deep reading on configured candidates (independent of translation)...")
    from paperflow.deep_read import DeepReader
    from paperflow.schemas import PaperMetadata
    from paperflow.screening import Screener
    from paperflow.pdf_locator import PDFLocator
    from paperflow.pdf_extract import PDFExtractor

    notion = NotionClientWrapper()
    papers = notion.get_deep_read_papers()
    logger.info(f"Found {len(papers)} papers for deep read.")

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
@click.option('--from-cache', is_flag=True, help='Export from local cache instead of querying Notion')
def export_obsidian(from_cache):
    """Export deep-read candidate papers to Obsidian notes."""
    logger.info("Exporting deep-read candidates to Obsidian...")
    from paperflow.obsidian_export import ObsidianExporter
    from paperflow.translation_artifacts import TranslationArtifacts
    from paperflow.schemas import PaperMetadata
    from paperflow.screening import Screener

    config = get_config()
    exporter = ObsidianExporter()
    artifacts = TranslationArtifacts()
    screener = Screener()

    # 收集需要导出的论文
    papers_to_export = []
    if from_cache:
        cache_dir = cache.base_dir
        if not os.path.exists(cache_dir):
            logger.info("Cache directory does not exist.")
            return
        for d in os.listdir(cache_dir):
            if d == "_global" or not os.path.isdir(os.path.join(cache_dir, d)):
                continue
            if all([
                cache.load_json(d, "metadata.json"),
                cache.load_json(d, "speed_card.json"),
                cache.load_json(d, "deep_read.json"),
            ]):
                papers_to_export.append(d)
        logger.info(f"Found {len(papers_to_export)} complete papers in local cache")
    else:
        notion = NotionClientWrapper()
        papers = notion.get_deep_read_papers()
        for paper in papers:
            props = paper.get('properties', {})
            try:
                zotero_key = props.get("Zotero Key", {}).get("rich_text", [])[0].get("text", {}).get("content", "")
            except IndexError:
                continue
            if zotero_key:
                papers_to_export.append((zotero_key, paper))

    for item in papers_to_export:
        if from_cache:
            zotero_key = item
            page = None
        else:
            zotero_key, page = item

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

        if from_cache:
            # 直接从 artifacts 缓存解析翻译 PDF 路径
            mono_pdf, dual_pdf = artifacts.resolve(zotero_key, "", "")
        else:
            # Pull translated paths from Notion if they exist (they might not, which is fine)
            props = page.get("properties", {})
            try:
                mono_pdf = props.get("Translated Mono PDF", {}).get("rich_text", [])[0].get("text", {}).get("content", "")
            except IndexError:
                mono_pdf = ""
            try:
                dual_pdf = props.get("Translated Dual PDF", {}).get("rich_text", [])[0].get("text", {}).get("content", "")
            except IndexError:
                dual_pdf = ""
            mono_pdf, dual_pdf = artifacts.resolve(zotero_key, mono_pdf, dual_pdf)
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
             if not from_cache and page:
                 notion._update_page(page["id"], {"Status": {"select": {"name": "Exported to Obsidian"}}})

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
    """Run selected flow (Must Read translation queue -> candidates deep read -> export).

    Translation is human Must Read only and disabled by default. Use --translate to enable it.
    """
    logger.info("Running 'Must Read' flow...")
    from paperflow.translation_queue import TranslationQueue
    from paperflow.notion_client import NotionClientWrapper
    from paperflow.translation_artifacts import TranslationArtifacts
    notion = NotionClientWrapper()
    artifacts = TranslationArtifacts()

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
                source_mono_pdf, source_dual_pdf = mono_pdf, dual_pdf
                mono_pdf, dual_pdf = artifacts.publish(zotero_key, mono_pdf, dual_pdf)
                item["status"] = "done"
                item["source_mono_pdf"] = source_mono_pdf
                item["source_dual_pdf"] = source_dual_pdf
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
        deep_read_papers = notion.get_deep_read_papers()
    except Exception as e:
        logger.error(f"Failed to query Notion for deep read candidates (after retries): {e}")
        deep_read_papers = []
    for paper in deep_read_papers:
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
    for paper in deep_read_papers:
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
        mono_pdf, dual_pdf = artifacts.resolve(zotero_key, mono_pdf, dual_pdf)
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

def _enrich_one(
    zotero_key: str,
    metadata: PaperMetadata,
    fake_no_code: bool,
    save_to_cache: bool,
    update_notion: bool,
) -> dict:
    """Enrich a single paper: venue + code URL. Returns result dict."""
    from paperflow.venue_enhancer import VenueYearEnhancer
    from paperflow.code_finder import find_code_url_with_meta

    title = metadata.title
    current_venue = metadata.venue or ""
    current_year = metadata.year
    current_url = metadata.url or ""
    authors = metadata.authors or ""

    logger.info(f"\n{'='*60}")
    logger.info(f"Enriching: {title[:70]}... [key={zotero_key}]")
    logger.info(f"{'='*60}")

    # ─── Venue ───
    enhancer = VenueYearEnhancer()
    enhanced_venue, enhanced_year, venue_confidence = enhancer.enhance(
        title=title,
        current_venue=current_venue,
        current_year=current_year,
        url=current_url,
    )
    venue_changed = (enhanced_venue != current_venue) or (enhanced_year != current_year)
    logger.info(f"[Venue] {current_venue or '(empty)'} -> {enhanced_venue or '(empty)'} (conf={venue_confidence:.2f})")

    # ─── Code URL ───
    existing_code_url = ""
    if not fake_no_code:
        sc = cache.load_json(zotero_key, "speed_card.json") or {}
        existing_code_url = sc.get("code_url", "")

    if existing_code_url and not fake_no_code:
        logger.info(f"[Code] existing={existing_code_url} (skipped)")
        code_result = {"url": existing_code_url, "source": "existing", "confidence": 1.0}
    else:
        code_meta = find_code_url_with_meta(title, authors)
        logger.info(f"[Code] {code_meta['url'] or 'NOT FOUND'} | source={code_meta['source']} | conf={code_meta['confidence']:.2f}")
        code_result = code_meta

    # ─── Save to cache ───
    if save_to_cache:
        metadata.venue = enhanced_venue
        metadata.year = enhanced_year
        cache.save_json(zotero_key, "metadata.json", metadata.model_dump())
        if code_result["url"] and code_result["source"] != "existing":
            sc = cache.load_json(zotero_key, "speed_card.json") or {}
            sc["code_url"] = code_result["url"]
            cache.save_json(zotero_key, "speed_card.json", sc)

    # ─── Update Notion ───
    if update_notion:
        try:
            notion = NotionClientWrapper()
            page = notion.find_page_by_zotero_key(zotero_key)
            if page:
                properties = {}
                if enhanced_venue:
                    properties["Venue"] = {"rich_text": [{"text": {"content": enhanced_venue}}]}
                if enhanced_year:
                    properties["Year"] = {"number": enhanced_year}
                if code_result.get("url"):
                    properties["Code URL"] = {"url": code_result["url"]}
                # Venue Confidence 字段（P2）
                conf_label = "High" if venue_confidence >= 0.8 else "Medium" if venue_confidence >= 0.5 else "Low"
                properties["Venue Confidence"] = {"select": {"name": conf_label}}
                if properties:
                    try:
                        notion._update_page(page["id"], properties)
                        logger.info(f"[Notion] synced")
                    except Exception as e:
                        err_msg = str(e)
                        # 如果 Venue Confidence 属性不存在，重试时不带它
                        if "is not a property that exists" in err_msg and "Venue Confidence" in properties:
                            logger.warning(f"[Notion] Venue Confidence property missing, retrying without it")
                            safe_props = {k: v for k, v in properties.items() if k != "Venue Confidence"}
                            if safe_props:
                                notion._update_page(page["id"], safe_props)
                                logger.info(f"[Notion] synced (without Venue Confidence)")
                        else:
                            raise
            else:
                logger.warning(f"[Notion] page not found for {zotero_key}")
        except Exception as e:
            logger.error(f"[Notion] sync failed: {e}")

    return {
        "zotero_key": zotero_key,
        "title": title,
        "venue": enhanced_venue,
        "year": enhanced_year,
        "venue_confidence": venue_confidence,
        "venue_changed": venue_changed,
        "code_url": code_result["url"],
        "code_source": code_result["source"],
        "code_confidence": code_result["confidence"],
    }


@cli.command()
@click.option('--title', help='Paper title (directly specified)')
@click.option('--zotero-key', help='Zotero key (read metadata from cache)')
@click.option('--collection', required=False, help='Zotero Collection ID — batch mode')
@click.option('--from-cache', is_flag=True, help='Batch mode: process all cached papers')
@click.option('--fake-no-code', is_flag=True, help='Ignore existing code URL, force search')
@click.option('--update-notion', is_flag=True, help='Sync result back to Notion')
@click.option('--save-to-cache', is_flag=True, help='Save enriched result to local cache')
def enrich_paper(title, zotero_key, collection, from_cache, fake_no_code, update_notion, save_to_cache):
    """Manually trigger paper metadata enrichment: venue + code URL.

    Single paper:
      paperflow enrich-paper --title "Think2Go: generative next POI recommendation..."
      paperflow enrich-paper --zotero-key ABC123 --fake-no-code --save-to-cache

    Batch mode:
      paperflow enrich-paper --collection COLLECTION_ID --save-to-cache
      paperflow enrich-paper --from-cache --save-to-cache --update-notion
    """
    # ─── 单篇模式 ───
    if title or zotero_key:
        if zotero_key:
            metadata_data = cache.load_json(zotero_key, "metadata.json")
            if not metadata_data:
                logger.error(f"No cached metadata for zotero_key={zotero_key}")
                return
            metadata = PaperMetadata(**metadata_data)
        elif title:
            # 构造一个临时的 metadata 对象
            metadata = PaperMetadata(
                zotero_key="_manual",
                title=title,
                authors="",
            )
        else:
            logger.error("Either --title or --zotero-key must be provided.")
            return

        result = _enrich_one(
            zotero_key=metadata.zotero_key,
            metadata=metadata,
            fake_no_code=fake_no_code,
            save_to_cache=save_to_cache,
            update_notion=update_notion,
        )
        logger.info(f"\n{'='*60}")
        logger.info("SINGLE PAPER SUMMARY")
        logger.info(f"{'='*60}")
        logger.info(f"Venue:    {result['venue'] or 'Unknown'} (conf={result['venue_confidence']:.2f})")
        logger.info(f"Year:     {result['year'] or 'Unknown'}")
        logger.info(f"Code URL: {result['code_url'] or 'Not found'}")
        return

    # ─── 批量模式 ───
    papers_to_enrich: list[tuple[str, PaperMetadata]] = []

    if collection:
        config = get_config()
        collection_id = collection or config.project.collection_id or config.zotero.collection_id
        if not collection_id:
            logger.error("No collection ID provided.")
            return
        client = ZoteroClient()
        items = client.get_collection_items(collection_id)
        for item in items:
            metadata = client.parse_item(item, items)
            if metadata:
                papers_to_enrich.append((metadata.zotero_key, metadata))
        logger.info(f"Batch mode: loaded {len(papers_to_enrich)} papers from Zotero collection {collection_id}")

    elif from_cache:
        cache_dir = cache.base_dir
        if not os.path.exists(cache_dir):
            logger.error("Cache directory does not exist.")
            return
        keys = [d for d in os.listdir(cache_dir)
                if os.path.isdir(os.path.join(cache_dir, d)) and d != "_global"]
        for zotero_key in keys:
            metadata_data = cache.load_json(zotero_key, "metadata.json")
            if metadata_data:
                try:
                    metadata = PaperMetadata(**metadata_data)
                    papers_to_enrich.append((zotero_key, metadata))
                except Exception as e:
                    logger.warning(f"Failed to parse metadata for {zotero_key}: {e}")
        logger.info(f"Batch mode: loaded {len(papers_to_enrich)} papers from local cache")

    else:
        logger.error("Please specify --title, --zotero-key, --collection, or --from-cache")
        return

    if not papers_to_enrich:
        logger.info("No papers to enrich.")
        return

    # 批量处理（串行，避免 API 限速）
    results = []
    for zk, meta in papers_to_enrich:
        try:
            result = _enrich_one(
                zotero_key=zk,
                metadata=meta,
                fake_no_code=fake_no_code,
                save_to_cache=save_to_cache,
                update_notion=update_notion,
            )
            results.append(result)
        except Exception as e:
            logger.error(f"Enrichment failed for {zk}: {e}")
            results.append({
                "zotero_key": zk,
                "title": meta.title,
                "venue": meta.venue,
                "year": meta.year,
                "venue_confidence": 0.0,
                "venue_changed": False,
                "code_url": "",
                "code_source": "error",
                "code_confidence": 0.0,
            })

    # 汇总统计
    venue_updated = sum(1 for r in results if r["venue_changed"])
    code_found = sum(1 for r in results if r["code_url"] and r["code_source"] != "existing")
    code_existing = sum(1 for r in results if r["code_source"] == "existing")
    errors = sum(1 for r in results if r["code_source"] == "error")
    low_conf_venue = sum(1 for r in results if r["venue_confidence"] < 0.5)

    logger.info(f"\n{'='*60}")
    logger.info("BATCH ENRICHMENT SUMMARY")
    logger.info(f"{'='*60}")
    logger.info(f"Total papers:      {len(results)}")
    logger.info(f"Venue updated:     {venue_updated}")
    logger.info(f"Code URL found:    {code_found}")
    logger.info(f"Code URL existing: {code_existing}")
    logger.info(f"Low venue conf:    {low_conf_venue}")
    if errors:
        logger.info(f"Errors:            {errors}")
    logger.info(f"{'='*60}\n")


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
    from paperflow.translation_artifacts import TranslationArtifacts

    qm = TranslationQueue()
    runner = PDF2ZHRunner()
    notion = NotionClientWrapper()
    artifacts = TranslationArtifacts()
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
            source_mono_pdf, source_dual_pdf = mono_pdf, dual_pdf
            mono_pdf, dual_pdf = artifacts.publish(zotero_key, mono_pdf, dual_pdf)
            item["status"] = "done"
            item["source_mono_pdf"] = source_mono_pdf
            item["source_dual_pdf"] = source_dual_pdf
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
