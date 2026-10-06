from pathlib import Path
import tempfile
import unittest

from bibreview.relevance import (
    RelevanceEvidence,
    analyze_relevance,
    merge_relevance_evidence,
    record_human_relevance_decision,
    relevance_evidence_data,
    relevance_evidence_from_data,
)


class RelevanceEvidenceTests(unittest.TestCase):
    def evidence(self, doi, text, *, outcome="review", batch="batch-0001"):
        return RelevanceEvidence(
            doi=doi,
            title=text,
            abstract="",
            keywords=(),
            work_type="journal-article",
            screening_outcome=outcome,
            batch_id=batch,
            attempt=1,
        )

    def test_round_trip_and_human_decision(self):
        original = self.evidence(
            "10.1/example",
            "Partitioned coupling method for fluid structure interaction",
        )
        loaded = relevance_evidence_from_data(
            relevance_evidence_data((original,))
        )
        self.assertEqual(loaded, (original,))

        decided = record_human_relevance_decision(
            loaded,
            doi="10.1/example",
            decision="keep",
        )
        self.assertEqual(decided[0].human_decision, "keep")

        refreshed = RelevanceEvidence(
            doi="10.1/example",
            title="Updated provider title",
            abstract="Updated abstract",
            keywords=("fsi",),
            work_type="journal-article",
            screening_outcome="queued",
        )
        merged = merge_relevance_evidence(decided, (refreshed,))
        self.assertEqual(merged[0].title, "Updated provider title")
        self.assertEqual(merged[0].human_decision, "keep")
        self.assertEqual(merged[0].batch_id, "batch-0001")
        self.assertEqual(merged[0].attempt, 1)

    def test_analysis_replays_rules_and_discovers_signals(self):
        entries = (
            self.evidence(
                "10.1/k1",
                "Partitioned coupling algorithm for fluid structure interaction",
                outcome="queued",
                batch="batch-0001",
            ),
            self.evidence(
                "10.1/k2",
                "Partitioned coupling method for fluid structure interaction",
                outcome="review",
                batch="batch-0002",
            ),
            self.evidence(
                "10.1/r1",
                "Experimental test bench for offshore turbine design",
                outcome="review",
                batch="batch-0001",
            ),
            self.evidence(
                "10.1/r2",
                "Experimental test bench for pump performance",
                outcome="rejected",
                batch="batch-0002",
            ),
        )
        labels = {
            "10.1/k1": ("keep", "canonical"),
            "10.1/k2": ("keep", "human"),
            "10.1/r1": ("reject", "human"),
            "10.1/r2": ("reject", "terminal"),
        }
        analysis = analyze_relevance(
            entries,
            labels,
            accept_patterns=(r"partitioned[-\s]+coupling",),
            reject_patterns=(r"experimental[-\s]+test[-\s]+bench",),
            unmatched="manual-review",
        )

        self.assertEqual(analysis["summary"]["labeled"], 4)
        self.assertEqual(analysis["summary"]["human_labeled"], 2)
        self.assertEqual(analysis["current_rules"]["auto_accept"], 2)
        self.assertEqual(analysis["current_rules"]["auto_reject"], 2)
        self.assertEqual(analysis["current_rules"]["manual_review"], 0)
        self.assertEqual(analysis["current_rules"]["accept_false_positives"], 0)
        self.assertEqual(analysis["current_rules"]["reject_false_negatives"], 0)

        accept_phrases = {
            item["phrase"] for item in analysis["signals"]["accept"]
        }
        reject_phrases = {
            item["phrase"] for item in analysis["signals"]["reject"]
        }
        self.assertIn("partitioned coupling", accept_phrases)
        self.assertIn("experimental test", reject_phrases)

    def test_small_sample_reports_statistics_without_claiming_automation(self):
        entry = self.evidence(
            "10.1/one",
            "Rare promising phrase",
        )
        analysis = analyze_relevance(
            (entry,),
            {"10.1/one": ("keep", "human")},
        )
        self.assertEqual(analysis["summary"]["labeled"], 1)
        self.assertEqual(analysis["signals"]["accept"], [])
        self.assertEqual(analysis["signals"]["reject"], [])


if __name__ == "__main__":
    unittest.main()
