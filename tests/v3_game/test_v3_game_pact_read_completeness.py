"""Native-view completeness must not be replaced by unrelated coverage notes."""
import copy
import unittest
from unittest.mock import AsyncMock, patch

from pdx_runtime.v3_game.diplomacy import country_details


class Context:
    async def perform(self, label, operation):
        return await operation()


class PactReadCompletenessTests(unittest.IsolatedAsyncioTestCase):
    async def read_view(self, *, empty=False, failed=False):
        summary = {"country_id": 0, "complete": True, "is_subject": False,
                   "reasons": {}, "native_pages": []}
        group = {"source_index": 0, "group_country_id": 0, "group_type_key": "",
                 "reasons": {}}
        groups = {"country_id": 0, "complete": True, "source_count": 0 if empty else 1,
                  "rows": [] if empty else [group], "reasons": {}, "native_pages": []}
        row = {"source_index": 0, "group_country_id": 0, "group_type_key": "",
               "pact_type_key": "", "pact_id": 0, "first_country_id": 0,
               "second_country_id": 0xAB000008, "linked_action_maintenance_cost": 0,
               "about_to_break_reason": "", "reasons": {}}
        child = {"country_id": 0, "complete": not failed, "rows": [row],
                 "reasons": ["native field unavailable"] if failed else [],
                 "native_pages": [{"raw": b"original"}]}

        async def source(api, label, expression, fields, **kwargs):
            return copy.deepcopy(groups if expression == country_details._PACT_GROUP_SOURCE else child)

        api = country_details.CountryDiplomacyMixin()
        api._ctx = Context()
        with patch.object(country_details, "country_snapshot", new=AsyncMock(return_value=summary)), \
             patch.object(country_details, "country_source", new=AsyncMock(side_effect=source)) as reads:
            result = await api.pacts(country_id=0)
        return result, reads

    async def test_successful_native_view_is_complete_and_preserves_falsy_values(self):
        result, _ = await self.read_view()
        self.assertTrue(result["complete"])
        self.assertTrue(result["read_complete"])
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["reasons"], {})
        row = result["pacts"][0]
        self.assertEqual((row["pact_id"], row["first_country_id"], row["second_country_id"]),
                         (0, 0, 0xAB000008))
        self.assertEqual(row["linked_action_maintenance_cost"], 0)
        self.assertEqual(row["about_to_break_reason"], "")
        self.assertTrue(result["scope_notes"])
        self.assertFalse(result["command_validation_performed"])

    async def test_real_read_failure_stays_partial_without_dropping_rows(self):
        result, _ = await self.read_view(failed=True)
        self.assertFalse(result["complete"])
        self.assertFalse(result["read_complete"])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["pacts"][0]["pact_id"], 0)
        self.assertIn("active_pact_sources[0]", result["reasons"])
        self.assertEqual(result["native_pages"], [{"raw": b"original"}])

    async def test_empty_native_collection_is_a_complete_empty_view(self):
        result, reads = await self.read_view(empty=True)
        self.assertTrue(result["complete"])
        self.assertEqual(result["pacts"], [])
        self.assertEqual(result["reasons"], {})
        self.assertEqual(reads.await_count, 1)  # no invented child or subject read


if __name__ == "__main__":
    unittest.main()
