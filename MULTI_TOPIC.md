# Multi-topic workflow

Use one Notion data source and one Obsidian vault for multiple research topics.
Each topic gets:

- A Zotero collection configured through an environment variable.
- A `Topic` value in Notion, used for filtered views and independent decisions.
- Dedicated cache, PDF staging, translated output, and Obsidian note directories.

Translated outputs retain the topic boundary twice: generated PDFs remain under
`Literature/PDFs/Translated/<topic>/<zotero_key>/`, while PDFs referenced by
Obsidian notes are published under
`<vault>/Literature/PDFs/Translated/<topic>/<zotero_key>/`. Deep reads keep
using original PDFs as evidence inputs.

Example local configuration:

```yaml
project:
  active_topic: "urban_mobility"
  topics:
    urban_mobility:
      research_topic: "Urban mobility prediction"
      collection_id_env: "URBAN_MOBILITY_COLLECTION_ID"
      notion_tag: "urban-mobility"
      obsidian_subdir: "urban-mobility"
    poi_recommendation:
      research_topic: "POI recommendation"
      collection_id_env: "POI_COLLECTION_ID"
      notion_tag: "poi-recommendation"
      obsidian_subdir: "poi-recommendation"
```

Switch topics by changing `project.active_topic`. Do not create a second Notion
database unless access control or schema requirements differ. Create Notion
views filtered by `Topic`; the same Zotero paper may have a separate page and
separate AI assessment in each topic.

Notion API versions from `2025-09-03` onward expose table schema and queries
through a data source. `paperflow` discovers the first data source from
`NOTION_DATABASE_ID`, or accepts `NOTION_DATA_SOURCE_ID` to avoid a discovery
request.

## Testing workflow with TEST_COLLECTION_ID

To safely test paperflow without touching production data, create a dedicated
test topic and point it to a separate Zotero collection via `TEST_COLLECTION_ID`.

Example `config.yaml`:

```yaml
project:
  active_topic: "test_topic"
  topics:
    test_topic:
      label: "Test workflow"
      research_topic: "Test research topic"
      collection_id_env: "TEST_COLLECTION_ID"
      notion_tag: "test-topic"
      obsidian_subdir: "test-topic"
```

Set in `.env`:
```
TEST_COLLECTION_ID=your_test_collection_id
```

Then switch between test and production by changing `project.active_topic`.
Each topic maintains isolated cache, staging, and output directories.

### Re-sync from local cache (no Zotero API call)

When the test collection is empty or the Zotero API is unavailable, you can
still validate Notion sync using `--from-cache`. This reads papers directly
from `data/cache/<topic_slug>/` without calling Zotero:

```bash
# Preview what would be synced
paperflow sync-notion --from-cache --dry-run

# Actually sync to Notion
paperflow sync-notion --from-cache
```

Typical use cases:
- Verify Notion database schema and field mappings before running on production
- Re-populate Notion after bulk edits or accidental deletions
- Test topic configuration changes without re-fetching from Zotero

### Re-export Obsidian from local cache (no Notion API call)

Similarly, `export-obsidian` supports `--from-cache` to skip Notion queries and
export directly from local cache. This requires each paper to have all three
components cached (`metadata.json`, `speed_card.json`, `deep_read.json`):

```bash
paperflow export-obsidian --from-cache
```

Typical use cases:
- Rebuild Obsidian notes after vault migration or template changes
- Offline export when Notion API is unavailable
- CI/CD automated publishing

### Default behavior: cache-priority sync

Even without `--from-cache`, `sync-notion` now prefers cached metadata over
re-parsing Zotero items on every run. It still queries Zotero to detect new
papers, but for items already cached it reads `metadata.json` directly. This
reduces redundant parsing and speeds up repeated syncs.

Caveats:
- `--from-cache` only sees papers already cached for the current `active_topic`.
  Cross-topic sync requires copying cache directories or re-fetching.
- Content fingerprints in `notion_sync.json` still apply; unchanged papers are
  skipped automatically.

## Reliability controls

- PDF inputs are bounded and sampled across beginning, middle, and end.
- AI recommendation and priority are recalculated from configured weighted
  sub-scores, rather than trusting model-generated aggregate values.
- Notion sync and Obsidian export store local content fingerprints and skip
  unchanged writes or LLM calls.
- Notion requests retry transient network failures; set
  `notion.use_environment_proxy: false` if a local proxy breaks Notion TLS.
- `scripts/cleanup_test.py --execute --include-legacy-cache` archives only
  current-topic pages and matching legacy untagged pages, then removes matching
  local artifacts.
