import os
import unittest
from unittest.mock import patch
from pydantic import ValidationError

from paperflow.config import Config, DeepReadSelectionConfig
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

    def test_invalid_scoring_config_is_rejected(self):
        with self.assertRaises(ValidationError):
            Config(screening={
                "score_weights": {
                    "topic_relevance_score": 1.0,
                    "method_relevance_score": 1.0,
                    "data_relevance_score": 1.0,
                    "novelty_score": 1.0,
                    "reproducibility_score": 1.0,
                },
                "suggestion_thresholds": {"must_read": 4.2, "scan": 3.2, "park": 2.2},
            })


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
        card = screener.card_from_data({
            "topic_relevance_score": 5,
            "method_relevance_score": 4,
            "data_relevance_score": 4,
            "novelty_score": 3,
            "reproducibility_score": 3,
            "priority_score": 99,
            "ai_suggestion": "Read",
            "confidence": "High",
            "key_evidence": ["Evidence"],
        })
        normalized = card
        self.assertEqual(normalized.priority_score, 4.1)
        self.assertEqual(normalized.ai_suggestion, "Scan")

    def test_low_evidence_blocks_must_read_recommendation(self):
        screener = Screener()
        card = screener.card_from_data({
            "topic_relevance_score": 5,
            "method_relevance_score": 5,
            "data_relevance_score": 5,
            "novelty_score": 5,
            "reproducibility_score": 5,
            "priority_score": 99,
            "ai_suggestion": "Whatever",
            "confidence": "High",
            "key_evidence": [],
        }, has_full_text=False)
        self.assertEqual(card.priority_score, 5.0)
        self.assertEqual(card.confidence, "Low")
        self.assertEqual(card.ai_suggestion, "Scan")
        self.assertTrue(card.risk_need_check)

    def test_model_string_list_fields_are_normalized(self):
        screener = Screener()
        card = screener.card_from_data({
            "limitations": "No stated limitations.",
            "future_work": "No stated future work.",
            "key_evidence": "Evidence sentence.",
        })
        self.assertEqual(card.limitations, ["No stated limitations."])
        self.assertEqual(card.future_work, ["No stated future work."])
        self.assertEqual(card.key_evidence, ["Evidence sentence."])

    def test_prompt_change_invalidates_assessment_signature(self):
        screener = Screener()
        metadata = PaperMetadata(zotero_key="K", title="Title", authors="")
        with patch.object(screener, "load_prompt_template", return_value="prompt v1"):
            first = screener.assessment_signature(metadata)
        with patch.object(screener, "load_prompt_template", return_value="prompt v2"):
            second = screener.assessment_signature(metadata)
        self.assertNotEqual(first, second)


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

    def test_deep_read_selects_weighted_top_n_plus_manual_choice(self):
        wrapper = NotionClientWrapper.__new__(NotionClientWrapper)
        wrapper.deep_read_selection = DeepReadSelectionConfig(
            enabled=True, top_n=2, include_human_must_read=True
        )
        pages = [
            {
                "id": "manual",
                "properties": {
                    "Priority Score": {"number": 1.0},
                    "Human Decision": {"select": {"name": "Must Read"}},
                },
            },
            {
                "id": "top",
                "properties": {
                    "Priority Score": {"number": 4.8},
                    "Human Decision": {"select": {"name": "Unreviewed"}},
                },
            },
            {
                "id": "second",
                "properties": {
                    "Priority Score": {"number": 4.1},
                    "Human Decision": {"select": {"name": "Unreviewed"}},
                },
            },
        ]
        wrapper.list_topic_pages = lambda: pages

        selected = wrapper.get_deep_read_papers()

        self.assertEqual([page["id"] for page in selected], ["top", "second", "manual"])


if __name__ == "__main__":
    unittest.main()
