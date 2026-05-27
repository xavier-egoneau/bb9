from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bb9.core.compaction import (
    CompactionConfig,
    CompactionLevel,
    auto_compact_session,
    compact_session,
    compaction_level,
    estimate_tokens_from_chars,
)
from bb9.core.model_metadata import _metadata_from_openai_doc, resolve_model_metadata
from bb9.core.models import Session


class CompactionTests(unittest.TestCase):
    def test_compaction_level_respects_thresholds(self) -> None:
        config = CompactionConfig(context_window_tokens=100)

        self.assertEqual(CompactionLevel.NONE, compaction_level(59, config))
        self.assertEqual(CompactionLevel.TRIM, compaction_level(60, config))
        self.assertEqual(CompactionLevel.TRIM, compaction_level(75, config))
        self.assertEqual(CompactionLevel.SUMMARIZE, compaction_level(80, config))
        self.assertEqual(CompactionLevel.RESET, compaction_level(90, config))

    def test_soft_input_limit_triggers_compaction_before_context_window(self) -> None:
        config = CompactionConfig(context_window_tokens=1_050_000, soft_input_limit_tokens=250_000)

        self.assertEqual(CompactionLevel.NONE, compaction_level(249_999, config))
        self.assertEqual(CompactionLevel.SUMMARIZE, compaction_level(250_000, config))

    def test_manual_compaction_keeps_recent_messages_and_summary(self) -> None:
        session = Session()
        for index in range(6):
            session = session.with_message("user", f"message {index}", max_messages=20)

        result = compact_session(
            session,
            force=True,
            config=CompactionConfig(keep_recent_messages=2),
        )

        self.assertTrue(result.changed)
        self.assertEqual(4, result.compacted_messages)
        self.assertEqual(2, len(result.session.messages))
        self.assertIn("message 0", result.session.compaction_summary)
        self.assertIn("message 5", result.session.as_prompt_context())

    def test_auto_compaction_can_trigger_on_message_count(self) -> None:
        session = Session()
        for index in range(5):
            session = session.with_message("assistant", f"reply {index}", max_messages=20)

        result = auto_compact_session(
            session,
            config=CompactionConfig(auto_message_threshold=5, keep_recent_messages=2),
        )

        self.assertTrue(result.changed)
        self.assertEqual(3, result.compacted_messages)
        self.assertEqual(3, result.session.compacted_count)

    def test_estimate_tokens_from_chars_is_bounded(self) -> None:
        self.assertEqual(0, estimate_tokens_from_chars(0))
        self.assertEqual(1, estimate_tokens_from_chars(1))
        self.assertEqual(2, estimate_tokens_from_chars(8))

    def test_openai_doc_metadata_parser_extracts_window_and_soft_limit(self) -> None:
        metadata = _metadata_from_openai_doc(
            "gpt-5.5",
            "GPT-5.5 1,050,000 context window. prompts with >272K input tokens are priced higher.",
            source="test",
        )

        self.assertEqual(1_050_000, metadata.context_window_tokens)
        self.assertEqual(272_000, metadata.soft_input_limit_tokens)

    def test_known_model_metadata_works_without_network(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            metadata = resolve_model_metadata(
                "gpt-5.5",
                cache_path=Path(tmp) / "models.json",
            )

        self.assertEqual(1_050_000, metadata.context_window_tokens)
        self.assertEqual(272_000, metadata.soft_input_limit_tokens)

    def test_known_model_metadata_accepts_provider_prefixed_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            metadata = resolve_model_metadata(
                "openai/gpt-5.5",
                cache_path=Path(tmp) / "models.json",
            )

        self.assertEqual("openai/gpt-5.5", metadata.model)
        self.assertEqual(1_050_000, metadata.context_window_tokens)


if __name__ == "__main__":
    unittest.main()
