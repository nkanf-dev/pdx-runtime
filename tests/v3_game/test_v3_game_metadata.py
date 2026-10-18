"""CPU-only shared V3 metadata discovery; no process, pipe or native game calls."""
import unittest
from collections import Counter
from types import SimpleNamespace as N
from unittest.mock import patch

from pdx_runtime.v3_game._core import GameContext, NativeRejection


class Query:
    def __init__(self, result):
        self.result = result
        self._result, self._released = None, False
    def read(self):
        self._result, self._released = self.result, True
        return self.result


class Native:
    def __init__(self):
        self.world = 0
        self.calls = Counter()
    def request(self, command):
        self.calls[command] += 1
        if command != "world-status":
            raise AssertionError(command)
        return {"status": "ok", "world": hex(self.world), "interface_thread_id": 8}
    def status(self):
        self.calls["status"] += 1
        return {"status": "ok", "main_module_base": "0x1000"}


def page(rows, *, next=1, total=1, error=0):
    return N(error=error, rows=[N(name=name, type_id=identity) for name, identity in rows],
             next=next, total=total)


class MetadataTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.native = Native()
        self.ctx = GameContext(N(native=self.native, namespace={}), object())
        self.pages = {0: page([("FixtureGlobalUI", 901)])}
        self.starts = []
        def enumerate_types(client, *, start, capacity, **kwargs):
            self.assertIs(client, self.native)
            self.starts.append(start)
            return Query(self.pages[start])
        self.enumerate = self.enterContext(patch("pdx_runtime.v3_game._core.data.enumerate_types",
                                                side_effect=enumerate_types))
        self.lifecycle = self.enterContext(patch("pdx_runtime.v3_game._core.lifecycle.observe",
            side_effect=AssertionError("metadata must not require a lifecycle observation")))

    def assert_no_world_preflight(self):
        self.lifecycle.assert_not_called()
        self.assertEqual(self.native.calls, {})

    async def test_world_zero_type_lookup_uses_metadata_not_lifecycle(self):
        self.assertEqual(await self.ctx.type_id("FixtureGlobalUI"), 901)
        self.assertEqual(self.ctx.world, 0)
        self.assertEqual(self.ctx.image_base, 0)
        self.assertEqual(self.starts, [0])
        self.assert_no_world_preflight()

    async def test_world_zero_catalog_uses_metadata_and_returns_copied_snapshot(self):
        result = await self.ctx.all_types()
        self.assertEqual(result, {"FixtureGlobalUI": 901})
        result["NotNative"] = 99
        self.assertNotIn("NotNative", self.ctx._types)
        self.assert_no_world_preflight()

    async def test_positive_type_hit_has_no_io_even_without_world(self):
        self.ctx._types["CopiedNativeType"] = 37
        self.assertEqual(await self.ctx.type_id("CopiedNativeType"), 37)
        self.assertEqual(self.starts, [])
        self.assert_no_world_preflight()

    async def test_catalog_snapshot_hit_has_no_io(self):
        first = await self.ctx.all_types()
        self.assertEqual(await self.ctx.all_types(), first)
        self.assertEqual(self.starts, [0])
        self.assert_no_world_preflight()

    async def test_paged_lookup_preserves_actual_ids_and_other_discovered_names(self):
        self.pages = {0: page([("FixtureEarlyUI", 17)], next=128, total=129),
                      128: page([("FixtureGlobalUI", 901)], next=129, total=129)}
        self.assertEqual(await self.ctx.type_id("FixtureGlobalUI"), 901)
        self.assertEqual(await self.ctx.type_id("FixtureEarlyUI"), 17)
        self.assertEqual(self.ctx._types, {"FixtureEarlyUI": 17, "FixtureGlobalUI": 901})
        self.assertEqual(self.starts, [0, 128])
        self.assert_no_world_preflight()

    async def test_metadata_native_error_keeps_exact_page_and_prior_discoveries(self):
        failure = page([("PartialFailureRow", 777)], error=21, next=129, total=129)
        self.pages = {0: page([("FixtureEarlyUI", 17)], next=128, total=129), 128: failure}
        with self.assertRaises(NativeRejection) as caught:
            await self.ctx.type_id("FixtureGlobalUI")
        self.assertEqual(caught.exception.label, "metadata.types")
        self.assertIs(caught.exception.result, failure)
        self.assertEqual(caught.exception.result.rows[0].type_id, 777)
        self.assertEqual(self.ctx._types["FixtureEarlyUI"], 17)
        self.assertEqual(self.ctx.pending_queries, [])
        self.assert_no_world_preflight()

    async def test_missing_name_is_not_a_permanent_negative_cache(self):
        with self.assertRaises(LookupError):
            await self.ctx.type_id("LateRegistered")
        self.pages[0] = page([("FixtureGlobalUI", 901), ("LateRegistered", 1301)])
        self.assertEqual(await self.ctx.type_id("LateRegistered"), 1301)
        self.assertEqual(self.starts, [0, 0])
        self.assertEqual(self.ctx._types["FixtureGlobalUI"], 901)
        self.assert_no_world_preflight()

    async def test_new_discovery_invalidates_only_catalog_completion_snapshot(self):
        await self.ctx.all_types()
        self.pages[0] = page([("FixtureGlobalUI", 901), ("LateRegistered", 1301)])
        self.assertEqual(await self.ctx.type_id("LateRegistered"), 1301)
        self.assertEqual(await self.ctx.all_types(), {"FixtureGlobalUI": 901, "LateRegistered": 1301})
        self.assertEqual(self.starts, [0, 0, 0])
        self.assert_no_world_preflight()

    async def test_explicit_fresh_catalog_discovers_late_append_without_world_io(self):
        first = await self.ctx.all_types()
        self.pages[0] = page([("FixtureGlobalUI", 901), ("LateRegistered", 1301)])
        self.assertEqual(await self.ctx.all_types(), first)
        self.assertEqual(self.starts, [0])
        self.assertEqual(await self.ctx.all_types(refresh=True),
                         {"FixtureGlobalUI": 901, "LateRegistered": 1301})
        self.assertEqual(self.starts, [0, 0])
        self.assertEqual(await self.ctx.type_id("LateRegistered"), 1301)
        self.assertEqual(self.starts, [0, 0])
        self.assert_no_world_preflight()

    async def test_failed_fresh_catalog_keeps_prior_and_completed_pages_and_retries(self):
        await self.ctx.all_types()
        failure = page([("FailedPageValue", 777)], error=21, next=129, total=129)
        self.pages = {0: page([("FixtureGlobalUI", 901), ("CompletedPageValue", 17)], next=128, total=129),
                      128: failure}
        with self.assertRaises(NativeRejection) as caught:
            await self.ctx.all_types(refresh=True)
        self.assertIs(caught.exception.result, failure)
        self.assertEqual(self.ctx._types, {"FixtureGlobalUI": 901, "CompletedPageValue": 17})
        self.assertEqual(caught.exception.result.rows[0].type_id, 777)
        self.pages[128] = page([("RecoveredPageValue", 1301)], next=129, total=129)
        self.assertEqual(await self.ctx.all_types(),
            {"FixtureGlobalUI": 901, "CompletedPageValue": 17, "RecoveredPageValue": 1301})
        self.assertEqual(self.starts, [0, 0, 128, 0, 128])
        self.assert_no_world_preflight()

    async def test_explicit_observed_world_change_still_invalidates_existing_caches(self):
        self.ctx.world = self.native.world = 77
        await self.ctx.all_types()
        self.ctx._functions["copied"] = 3
        old = self.ctx.shared_cache("domain")
        old["copied"] = 1
        self.native.world = 88
        self.pages[0] = page([("FixtureGlobalUI", 1301)])
        # A cached schema lookup is not another lifecycle checkpoint.
        self.assertEqual(await self.ctx.type_id("FixtureGlobalUI"), 901)
        self.assert_no_world_preflight()
        with patch("pdx_runtime.v3_game._core.lifecycle.observe", return_value=Query(N(error=0, world=88))):
            await self.ctx.refresh()
        self.assertEqual(self.ctx._types, {})
        self.assertEqual(self.ctx._functions, {})
        self.assertIsNot(self.ctx.shared_cache("domain"), old)
        before = self.native.calls.copy()
        self.assertEqual(await self.ctx.all_types(), {"FixtureGlobalUI": 1301})
        self.assertEqual(self.native.calls, before)
        self.assertEqual(self.starts, [0, 0])




if __name__ == "__main__":
    unittest.main(verbosity=2)
