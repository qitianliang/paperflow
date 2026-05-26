# Multi-topic workflow

Use one Notion data source and one Obsidian vault for multiple research topics.
Each topic gets:

- A Zotero collection configured through an environment variable.
- A `Topic` value in Notion, used for filtered views and independent decisions.
- Dedicated cache, PDF staging, translated output, and Obsidian note directories.

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
