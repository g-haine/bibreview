from __future__ import annotations

from copy import deepcopy
import unittest

from bibreview.campaign import (
    CampaignError,
    campaign_data,
    campaign_from_data,
    campaign_progress,
    close_batch,
    create_campaign,
    open_next_batch,
    record_item_result,
)


class CampaignTests(unittest.TestCase):
    def test_create_campaign_preserves_stable_order_and_default_batch_size(self):
        campaign = create_campaign("audit", ["pub-c", "pub-a", "pub-b"])

        self.assertEqual(campaign.kind, "audit")
        self.assertEqual(campaign.default_batch_size, 50)
        self.assertEqual(
            tuple(item.key for item in campaign.items),
            ("pub-c", "pub-a", "pub-b"),
        )
        progress = campaign_progress(campaign)
        self.assertEqual(progress.total, 3)
        self.assertEqual(progress.pending, 3)
        self.assertIsNone(progress.open_batch)
        self.assertFalse(progress.exhausted)

    def test_open_batch_is_bounded_stable_and_resumable(self):
        campaign = create_campaign(
            "audit",
            ["one", "two", "three"],
            batch_size=2,
        )

        opened, batch = open_next_batch(campaign)
        self.assertIsNotNone(batch)
        self.assertEqual(batch.id, "batch-0001")
        self.assertEqual(batch.keys, ("one", "two"))
        self.assertEqual(
            tuple(item.state for item in opened.items),
            ("active", "active", "pending"),
        )
        self.assertEqual(
            tuple(item.attempts for item in opened.items),
            (1, 1, 0),
        )

        resumed, same_batch = open_next_batch(opened)
        self.assertEqual(resumed, opened)
        self.assertEqual(same_batch, batch)

    def test_next_batch_size_can_be_overridden_without_changing_campaign_default(self):
        campaign = create_campaign(
            "audit",
            ["one", "two", "three", "four", "five"],
            batch_size=3,
        )

        campaign, first = open_next_batch(campaign, batch_size=1)
        self.assertEqual(first.keys, ("one",))
        self.assertEqual(campaign.default_batch_size, 3)
        campaign = record_item_result(
            campaign,
            batch_id=first.id,
            key="one",
            state="completed",
        )
        campaign = close_batch(campaign, batch_id=first.id)

        campaign, second = open_next_batch(campaign)
        self.assertEqual(second.keys, ("two", "three", "four"))
        self.assertEqual(campaign.default_batch_size, 3)

    def test_open_batch_ignores_new_size_override_on_resume(self):
        campaign, first = open_next_batch(
            create_campaign("audit", ["one", "two", "three"], batch_size=2),
            batch_size=1,
        )
        resumed, same = open_next_batch(campaign, batch_size=3)
        self.assertEqual(resumed, campaign)
        self.assertEqual(same, first)
        self.assertEqual(same.keys, ("one",))

    def test_partial_checkpoint_keeps_same_open_batch_after_resume(self):
        campaign, batch = open_next_batch(
            create_campaign("audit", ["one", "two"], batch_size=2)
        )
        campaign = record_item_result(
            campaign,
            batch_id=batch.id,
            key="one",
            state="completed",
        )

        resumed, same_batch = open_next_batch(campaign)
        self.assertEqual(resumed, campaign)
        self.assertEqual(same_batch, batch)
        self.assertEqual(
            {item.key: item.state for item in campaign.items},
            {"one": "completed", "two": "active"},
        )
        with self.assertRaisesRegex(CampaignError, "cannot close with 1 active"):
            close_batch(campaign, batch_id=batch.id)

    def test_pending_first_pass_precedes_retryable_work(self):
        campaign = create_campaign(
            "audit",
            ["one", "two", "three"],
            batch_size=2,
        )
        campaign, first = open_next_batch(campaign)
        campaign = record_item_result(
            campaign,
            batch_id=first.id,
            key="one",
            state="completed",
        )
        campaign = record_item_result(
            campaign,
            batch_id=first.id,
            key="two",
            state="retryable",
            detail="temporary provider outage",
        )
        campaign = close_batch(campaign, batch_id=first.id)

        campaign, second = open_next_batch(campaign)
        self.assertEqual(second.id, "batch-0002")
        self.assertEqual(second.keys, ("three",))
        self.assertEqual(
            next(item for item in campaign.items if item.key == "two").state,
            "retryable",
        )

        campaign = record_item_result(
            campaign,
            batch_id=second.id,
            key="three",
            state="completed",
        )
        campaign = close_batch(campaign, batch_id=second.id)

        campaign, third = open_next_batch(campaign)
        self.assertEqual(third.id, "batch-0003")
        self.assertEqual(third.keys, ("two",))
        retried = next(item for item in campaign.items if item.key == "two")
        self.assertEqual(retried.state, "active")
        self.assertEqual(retried.attempts, 2)
        self.assertEqual(retried.detail, "")

    def test_open_batch_prevents_premature_campaign_completion(self):
        campaign, batch = open_next_batch(
            create_campaign("audit", ["one"], batch_size=1)
        )
        campaign = record_item_result(
            campaign,
            batch_id=batch.id,
            key="one",
            state="completed",
        )

        progress = campaign_progress(campaign)
        self.assertFalse(progress.exhausted)
        self.assertFalse(progress.successful)
        self.assertEqual(progress.open_batch, "batch-0001")

        campaign = close_batch(campaign, batch_id=batch.id)
        progress = campaign_progress(campaign)
        self.assertTrue(progress.exhausted)
        self.assertTrue(progress.successful)

    def test_completed_and_failed_items_are_terminal_for_campaign(self):
        campaign, batch = open_next_batch(
            create_campaign("audit", ["one", "two"], batch_size=2)
        )
        campaign = record_item_result(
            campaign,
            batch_id=batch.id,
            key="one",
            state="completed",
        )
        campaign = record_item_result(
            campaign,
            batch_id=batch.id,
            key="two",
            state="failed",
            detail="unsupported permanent condition",
        )
        campaign = close_batch(campaign, batch_id=batch.id)

        final, next_batch = open_next_batch(campaign)
        self.assertEqual(final, campaign)
        self.assertIsNone(next_batch)

        progress = campaign_progress(final)
        self.assertTrue(progress.exhausted)
        self.assertFalse(progress.successful)
        self.assertEqual(progress.completed, 1)
        self.assertEqual(progress.failed, 1)
        self.assertEqual(progress.batches_opened, 1)
        self.assertEqual(progress.batches_closed, 1)

    def test_successful_empty_campaign_is_already_exhausted(self):
        campaign = create_campaign("audit", [], batch_size=25)
        progress = campaign_progress(campaign)

        self.assertTrue(progress.exhausted)
        self.assertTrue(progress.successful)
        updated, batch = open_next_batch(campaign)
        self.assertEqual(updated, campaign)
        self.assertIsNone(batch)

    def test_campaign_serialization_round_trip_preserves_open_checkpoint(self):
        campaign, batch = open_next_batch(
            create_campaign("init", ["10.1/a", "10.1/b"], batch_size=2)
        )
        campaign = record_item_result(
            campaign,
            batch_id=batch.id,
            key="10.1/a",
            state="retryable",
            detail="HTTP 429",
        )

        payload = campaign_data(campaign)
        restored = campaign_from_data(deepcopy(payload))

        self.assertEqual(restored, campaign)
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["default_batch_size"], 2)
        self.assertNotIn("batch_size", payload)
        self.assertEqual(
            campaign_progress(restored).data(),
            {
                "total": 2,
                "pending": 0,
                "active": 1,
                "completed": 0,
                "retryable": 1,
                "failed": 0,
                "batches_opened": 1,
                "batches_closed": 0,
                "open_batch": "batch-0001",
            },
        )

    def test_schema_v1_campaign_migrates_losslessly_to_schema_v2(self):
        campaign = create_campaign(
            "audit",
            ["one", "two", "three"],
            batch_size=50,
        )
        campaign, first = open_next_batch(campaign, batch_size=2)
        campaign = record_item_result(
            campaign,
            batch_id=first.id,
            key="one",
            state="completed",
        )
        campaign = record_item_result(
            campaign,
            batch_id=first.id,
            key="two",
            state="completed",
        )
        campaign = close_batch(campaign, batch_id=first.id)

        current = campaign_data(campaign)
        legacy = deepcopy(current)
        legacy["schema_version"] = 1
        legacy["batch_size"] = legacy.pop("default_batch_size")

        restored = campaign_from_data(legacy)

        self.assertEqual(restored, campaign)
        self.assertEqual(restored.default_batch_size, 50)
        self.assertEqual(restored.batches[0].keys, ("one", "two"))
        self.assertEqual(
            tuple(item.state for item in restored.items),
            ("completed", "completed", "pending"),
        )

        migrated = campaign_data(restored)
        self.assertEqual(migrated["schema_version"], 2)
        self.assertEqual(migrated["default_batch_size"], 50)
        self.assertNotIn("batch_size", migrated)
        self.assertEqual(migrated["batches"], current["batches"])
        self.assertEqual(migrated["items"], current["items"])

    def test_rejects_duplicate_keys_and_invalid_batch_size(self):
        with self.assertRaisesRegex(CampaignError, "duplicate"):
            create_campaign("audit", ["same", "same"])
        with self.assertRaisesRegex(CampaignError, "positive integer"):
            create_campaign("audit", ["one"], batch_size=0)
        with self.assertRaisesRegex(CampaignError, "surrounding whitespace"):
            create_campaign(" audit ", ["one"])
        with self.assertRaisesRegex(CampaignError, "surrounding whitespace"):
            create_campaign("audit", [" one "])

    def test_record_result_rejects_wrong_batch_key_or_state(self):
        campaign, batch = open_next_batch(
            create_campaign("audit", ["one", "two"], batch_size=1)
        )

        with self.assertRaisesRegex(CampaignError, "not the current open"):
            record_item_result(
                campaign,
                batch_id="batch-9999",
                key="one",
                state="completed",
            )
        with self.assertRaisesRegex(CampaignError, "not part of open batch"):
            record_item_result(
                campaign,
                batch_id=batch.id,
                key="two",
                state="completed",
            )
        with self.assertRaisesRegex(CampaignError, "result state"):
            record_item_result(
                campaign,
                batch_id=batch.id,
                key="one",
                state="manual-review",
            )

    def test_persisted_state_rejects_corrupt_invariants(self):
        campaign, batch = open_next_batch(
            create_campaign("audit", ["one"], batch_size=1)
        )
        payload = campaign_data(campaign)

        wrong_id = deepcopy(payload)
        wrong_id["batches"][0]["id"] = "batch-0002"
        with self.assertRaisesRegex(CampaignError, "batch-0001"):
            campaign_from_data(wrong_id)

        wrong_attempts = deepcopy(payload)
        wrong_attempts["items"][0]["attempts"] = 0
        with self.assertRaisesRegex(CampaignError, "do not match"):
            campaign_from_data(wrong_attempts)

        unknown_key = deepcopy(payload)
        unknown_key["batches"][0]["keys"] = ["missing"]
        with self.assertRaisesRegex(CampaignError, "unknown campaign item"):
            campaign_from_data(unknown_key)

        closed_active = deepcopy(payload)
        closed_active["batches"][0]["closed"] = True
        with self.assertRaisesRegex(
            CampaignError,
            "active item must belong to the open batch",
        ):
            campaign_from_data(closed_active)

    def test_progress_summary_is_compact_and_deterministic(self):
        campaign, batch = open_next_batch(
            create_campaign("audit", ["one", "two"], batch_size=1)
        )
        progress = campaign_progress(campaign)
        self.assertEqual(
            progress.summary(),
            "total: 2; pending: 1; active: 1; completed: 0; "
            "retryable: 0; failed: 0; batches: 0/1",
        )


if __name__ == "__main__":
    unittest.main()
