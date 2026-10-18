"""CPU-only StorageAPI composition tests. No files, process or cloud are touched."""
from __future__ import annotations

from types import SimpleNamespace as N
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch

from pdx_runtime import v3_lifecycle as life
from pdx_runtime.v3_game.storage import SaveCatalog, SaveDeletion, SaveTarget, StorageAPI


def descriptor(name="one", *, cloud=0, valid=1):
    return life.SaveDescriptor(56, cloud, valid, 0, 1, 0, -1, 0, 0, 0, 0,
        "save games/子目录/" + name + ".v3", "save games/子目录", name,
        "1.13.11", "", "", "1836.1.1")


def catalog_result(records=(), *, error=0, required=0, size=0, total=None):
    return life.SaveCatalogResult(72, error, life.SaveCatalogAction.LIST, 1,
        0, 0, size, size, required, len(records), len(records) if total is None else total,
        17, 0x30000, 0x40000, 0, tuple(records))


def deletion_result(*, error=0, success=1, tag=0xffffffff, raw=b"", required=None):
    return life.SaveManagementResult(64, error, life.SaveManagementAction.DELETE_NAMED,
        0, 3, 0, len(raw), len(raw), len(raw) if required is None else required,
        success, tag, 17, 0x30000, 0x40000, raw)


class Context:
    def __init__(self, results=()):
        self.native = object()
        self.process = object()
        self.adapters = {"lifecycle": 0xABCD}
        self.results = iter(results)
        self.labels = []
        self.perform_labels = []

    async def query(self, label, factory):
        self.labels.append(label)
        factory()
        value = next(self.results)
        if isinstance(value, BaseException):
            raise value
        return value

    async def perform(self, label, operation):
        self.perform_labels.append(label)
        return await operation()


class SaveTargetTests(TestCase):
    def test_native_descriptor_selects_exact_path_or_cloud_name(self):
        local = descriptor("本地")
        cloud = descriptor("云存档", cloud=1)
        self.assertEqual(SaveTarget.from_descriptor(local), SaveTarget(local.path, by_path=True))
        self.assertEqual(SaveTarget.from_descriptor(cloud), SaveTarget("云存档", cloud=True))

    def test_native_paths_are_not_normalized_or_sandboxed(self):
        text = "native:/save games/../literal/原名.v3"
        self.assertEqual(SaveTarget.native_path(text).source, text)

    def test_only_input_shape_and_provider_contract_are_checked(self):
        for args in (("",), ("bad\0name",), ("name", True, True)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                SaveTarget(*args)
        for args in (("name", 1), ("name", False, 1), (1,)):
            with self.subTest(args=args), self.assertRaises(TypeError):
                SaveTarget(*args)
        with self.assertRaises(ValueError):
            SaveTarget.from_descriptor(descriptor(cloud=7))

    def test_empty_native_catalog_is_complete_not_unavailable(self):
        source = catalog_result()
        value = SaveCatalog((source,))
        self.assertTrue(value.complete)
        self.assertEqual(value.records, ())
        self.assertIs(value.result, source)

    def test_partial_records_zero_values_invalid_headers_stay_intact(self):
        record = descriptor(valid=0)
        source = catalog_result((record,), error=234, size=80, required=160, total=2)
        value = SaveCatalog((source,))
        self.assertFalse(value.complete)
        self.assertIs(value.records[0], record)
        self.assertEqual((record.cloud, record.is_valid, record.country_flag_id), (0, 0, 0))
        self.assertEqual(record.modified_time_raw, -1)


class StorageAPITests(IsolatedAsyncioTestCase):
    async def test_catalog_uses_existing_context_and_native_sources_without_world_query(self):
        source = catalog_result((descriptor(valid=0),))
        ctx = Context((source,))
        with patch.object(life, "list_saves") as native, patch.object(life, "observe") as observe:
            value = await StorageAPI(ctx).catalog(cloud=True, directory="原目录", name_filter="国", output_capacity=17)
        self.assertIs(value.result, source)
        native.assert_called_once_with(ctx.native, directory="原目录", name_filter="国",
            sources=life.SaveSource.LOCAL | life.SaveSource.CLOUD, output_capacity=17,
            adapter=0xABCD, process=ctx.process)
        observe.assert_not_called()
        self.assertEqual(ctx.perform_labels, ["storage.catalog"])

    async def test_short_read_grows_to_exact_native_size_and_preserves_both_observations(self):
        earlier = catalog_result((descriptor("earlier"),), error=234, size=40, required=900, total=2)
        later = catalog_result((descriptor("later"),), size=900, required=900)
        ctx = Context((earlier, later))
        with patch.object(life, "list_saves") as native:
            value = await StorageAPI(ctx).catalog(output_capacity=64)
        self.assertEqual([c.kwargs["output_capacity"] for c in native.call_args_list], [64, 900])
        self.assertEqual(value.attempts, (earlier, later))
        self.assertEqual(value.records[0].name, "later")
        self.assertEqual(value.attempts[0].records[0].name, "earlier")
        self.assertTrue(value.complete)

    async def test_native_failure_with_partial_records_is_returned_not_replaced_by_empty(self):
        source = catalog_result((descriptor(),), error=5, total=3)
        ctx = Context((source,))
        with patch.object(life, "list_saves") as native:
            value = await StorageAPI(ctx).catalog()
        self.assertIs(value.result, source)
        self.assertEqual(len(value.records), 1)
        self.assertFalse(value.complete)
        native.assert_called_once()

    async def test_more_data_without_a_larger_requirement_does_not_loop(self):
        source = catalog_result(error=234, required=12, size=12, total=7)
        ctx = Context((source,))
        with patch.object(life, "list_saves") as native:
            value = await StorageAPI(ctx).catalog(output_capacity=64)
        self.assertIs(value.result, source)
        native.assert_called_once()

    async def test_failure_after_short_read_preserves_completed_native_records_on_original_error(self):
        source = catalog_result((descriptor(),), error=234, required=900, size=80, total=2)
        failure = RuntimeError("original callback read failed")
        ctx = Context((source, failure))
        with patch.object(life, "list_saves") as native:
            with self.assertRaises(RuntimeError) as caught:
                await StorageAPI(ctx).catalog(output_capacity=80)
        self.assertIs(caught.exception, failure)
        self.assertEqual(failure.save_catalog_attempts, (source,))
        self.assertEqual(native.call_count, 2)

    async def test_describe_preserves_cloud_native_path_and_error(self):
        source = catalog_result(error=2)
        ctx = Context((source,))
        with patch.object(life, "describe_save") as native, patch.object(life, "load") as load:
            value = await StorageAPI(ctx).describe("native cloud/原存档", cloud=True, output_capacity=23)
        self.assertIs(value.result, source)
        native.assert_called_once_with(ctx.native, "native cloud/原存档", cloud=True,
            output_capacity=23, adapter=0xABCD, process=ctx.process)
        load.assert_not_called()

    async def test_delete_local_catalog_row_uses_only_its_exact_native_path(self):
        row = descriptor("selected")
        source = deletion_result()
        ctx = Context((source,))
        with patch.object(life, "delete_save") as exact, patch.object(life, "delete_named_save") as named:
            value = await StorageAPI(ctx).delete(row)
        exact.assert_called_once_with(ctx.native, row.path, message_capacity=4096,
            adapter=0xABCD, process=ctx.process)
        named.assert_not_called()
        self.assertEqual(value.target, SaveTarget(row.path, by_path=True))
        self.assertIs(value.result, source)

    async def test_delete_cloud_catalog_row_uses_its_provider_and_original_name(self):
        row = descriptor("云端", cloud=1)
        source = deletion_result(success=0)
        ctx = Context((source,))
        with patch.object(life, "delete_save") as exact, patch.object(life, "delete_named_save") as named:
            value = await StorageAPI(ctx).delete(row)
        exact.assert_not_called()
        named.assert_called_once_with(ctx.native, "云端", cloud=True, message_capacity=4096,
            adapter=0xABCD, process=ctx.process)
        self.assertEqual(value.result.native_success, 0)
        self.assertEqual(value.result.error, 0)
        self.assertIs(value.result, source)

    async def test_native_message_prefix_and_success_survive_more_data_without_replay(self):
        prefix = "原生结果".encode()[:4]
        source = deletion_result(error=234, success=1, tag=0, raw=prefix, required=24)
        ctx = Context((source,))
        with patch.object(life, "delete_named_save") as native:
            value = await StorageAPI(ctx).delete("原名", cloud=True, message_capacity=4)
        native.assert_called_once()
        self.assertIs(value.result, source)
        self.assertEqual((value.result.error, value.result.native_success), (234, 1))
        self.assertEqual(value.result.message_raw, prefix)
        self.assertEqual(value.result.message_required, 24)

    async def test_exact_save_target_is_not_resolved_by_a_new_catalog_search(self):
        source = deletion_result()
        target = SaveTarget.native_path("native exact:/原目录/存档.v3")
        ctx = Context((source,))
        with patch.object(life, "delete_save") as native, patch.object(life, "list_saves") as listed:
            value = await StorageAPI(ctx).delete(target)
        listed.assert_not_called()
        self.assertEqual(native.call_args.args[1], target.source)
        self.assertIs(value.target, target)

    async def test_invalid_late_batch_input_prevents_every_mutation(self):
        ctx = Context()
        with patch.object(life, "delete_named_save") as native:
            with self.assertRaises(ValueError):
                await StorageAPI(ctx).delete_many(["valid", "bad\0name"])
        native.assert_not_called()
        self.assertEqual(ctx.perform_labels, [])

    async def test_batch_retains_each_original_native_refusal_and_continues_explicit_targets(self):
        first = deletion_result(success=0, tag=1, raw=b"denied")
        second = deletion_result(success=1)
        ctx = Context((first, second))
        with patch.object(life, "delete_named_save") as native:
            values = await StorageAPI(ctx).delete_many(("one", "two"))
        self.assertEqual([c.args[1] for c in native.call_args_list], ["one", "two"])
        self.assertEqual([r.result for r in values], [first, second])
        self.assertEqual(ctx.perform_labels, ["storage.delete_many"])

    async def test_batch_unknown_delivery_keeps_original_error_and_prior_results_no_later_submit(self):
        first = deletion_result()
        failure = RuntimeError("delivery unknown")
        failure.query = object()
        original_query = failure.query
        ctx = Context((first, failure))
        with patch.object(life, "delete_named_save") as native:
            with self.assertRaises(RuntimeError) as caught:
                await StorageAPI(ctx).delete_many(("one", "two", "three"))
        self.assertIs(caught.exception, failure)
        self.assertIs(failure.query, original_query)
        self.assertEqual([c.args[1] for c in native.call_args_list], ["one", "two"])
        self.assertIs(failure.completed_save_deletions[0].result, first)
        self.assertEqual(failure.pending_save_target.source, "two")

    async def test_empty_explicit_batch_returns_empty_without_queries(self):
        ctx = Context()
        value = await StorageAPI(ctx).delete_many(())
        self.assertEqual(value, ())
        self.assertEqual(ctx.labels, [])

    async def test_pending_operation_is_returned_from_existing_context_not_polled_or_replayed(self):
        marker = object()
        saved = {}
        ctx = Context((deletion_result(),))
        async def pending(label, operation):
            saved.update(label=label, operation=operation)
            return marker
        ctx.perform = pending
        with patch.object(life, "delete_named_save") as native:
            value = await StorageAPI(ctx).delete("name")
            self.assertIs(value, marker)
            native.assert_not_called()
            completed = await saved["operation"]()
        self.assertIsInstance(completed, SaveDeletion)
        native.assert_called_once()

    async def test_input_errors_submit_nothing(self):
        ctx = Context()
        api = StorageAPI(ctx)
        with patch.object(life, "list_saves") as listed, patch.object(life, "delete_named_save") as deleted:
            for value in (True, -1, 2**32, "100"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    await api.catalog(output_capacity=value)
            with self.assertRaises(TypeError):
                await api.catalog(cloud=1)
            with self.assertRaises(ValueError):
                await api.describe("bad\0path")
            with self.assertRaises(TypeError):
                await api.delete_many("single string")
            with self.assertRaises(TypeError):
                await api.delete(object())
        listed.assert_not_called()
        deleted.assert_not_called()

    async def test_invalid_utf8_in_late_batch_target_is_rejected_before_first_mutation(self):
        ctx = Context()
        with patch.object(life, "delete_named_save") as native:
            with self.assertRaises(UnicodeEncodeError):
                await StorageAPI(ctx).delete_many(["valid", "bad\ud800"])
        native.assert_not_called()
        self.assertEqual(ctx.perform_labels, [])

    async def test_real_shared_pending_executor_resumes_the_one_original_delete(self):
        import threading
        from pdx_runtime.v3_game._core import GameContext, PendingOperation

        ready = threading.Event()
        source = deletion_result()
        class Query:
            def __init__(self):
                self._released = False
                self._result = None
            def read(self):
                if not ready.is_set():
                    return None
                self._released = True
                self._result = source
                return source
        query = Query()
        session = N(native=object(), namespace={})
        ctx = GameContext(session, object(), timeout=.001, poll_interval=.001)
        with patch.object(life, "delete_named_save", return_value=query) as native:
            pending = await StorageAPI(ctx).delete("selected")
            self.assertIsInstance(pending, PendingOperation)
            native.assert_called_once()
            ready.set()
            value = await pending.resume(timeout=1)
        self.assertIs(value.result, source)
        native.assert_called_once()

