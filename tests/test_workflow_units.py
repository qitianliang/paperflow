import os
import unittest
from unittest.mock import patch

from paperflow.config import Config
from paperflow.notion_client import AI_DETAILS_TITLE, NotionClientWrapper
from paperflow.schemas import PaperMetadata, SpeedCard
from paperflow.screening import Screener
from paperflow.utils import sample_long_text


class TopicConfigTests(unittest.TestCase):
    def test_active_topic_resolves_collection_from_environment(self):
        config = Config(
            project={
                "active_topic": "test",
                "topics": {
                    "test": {
                        "collection_id_env": "UNIT_COLLECTION",
                        "notion_tag": "unit-test",
                        "obsidian_subdir": "unit-test",
                    }
                },
            }
        )
        with patch.dict(os.environ, {"UNIT_COLLECTION": "COL123"}):
            self.assertEqual(config.project.collection_id, "COL123")
            self.assertEqual(config.project.topic_label, "unit-test")
            self.assertEqual(config.project.topic_slug, "unit-test")


class TextSamplingTests(unittest.TestCase):
    def test_samples_start_middle_and_end_with_limit(self):
        text = "A" * 100 + "B" * 100 + "C" * 100
        sampled = sample_long_text(text, 100, sections=3)
        self.assertLessEqual(len(sampled), 100)
        self.assertIn("A", sampled)
        self.assertIn("B", sampled)
        self.assertIn("C", sampled)
        self.assertIn("sampled gap", sampled)

    def test_recommendation_is_calculated_from_weighted_scores(self):
        screener = Screener()
        card = SpeedCard(
            topic_relevance_score=5,
            method_relevance_score=4,
            data_relevance_score=4,
            novelty_score=3,
            reproducibility_score=3,
            priority_score=99,
            ai_suggestion="Read",
        )
        normalized = screener.normalize_decision(card)
        self.assertEqual(normalized.priority_score, 4.1)
        self.assertEqual(normalized.ai_suggestion, "Scan")


class _FakeChildren:
    def __init__(self):
        self.appended = None

    def list(self, block_id):
        return {
            "results": [
                {"id": "human", "type": "paragraph", "paragraph": {}},
                {
                    "id": "managed",
                    "type": "toggle",
                    "toggle": {"rich_text": [{"plain_text": AI_DETAILS_TITLE}]},
                },
            ]
        }

    def append(self, block_id, children):
        self.appended = children


class _FakeBlocks:
    def __init__(self):
        self.children = _FakeChildren()
        self.deleted = []

    def delete(self, block_id):
        self.deleted.append(block_id)


class NotionBodyTests(unittest.TestCase):
    def test_sync_ai_details_replaces_only_managed_toggle(self):
        wrapper = NotionClientWrapper.__new__(NotionClientWrapper)
        wrapper.client = type("FakeClient", (), {"blocks": _FakeBlocks()})()
        metadata = PaperMetadata(zotero_key="K", title="Title", authors="")
        card = SpeedCard(ai_suggestion="Scan", key_evidence=["Evidence"])

        wrapper.sync_ai_details("page", metadata, card)

        self.assertEqual(wrapper.client.blocks.deleted, ["managed"])
        toggle = wrapper.client.blocks.children.appended[0]["toggle"]
        self.assertEqual(toggle["rich_text"][0]["text"]["content"], AI_DETAILS_TITLE)
        self.assertIn("Evidence", toggle["children"][0]["paragraph"]["rich_text"][0]["text"]["content"])


if __name__ == "__main__":
    unittest.main()
