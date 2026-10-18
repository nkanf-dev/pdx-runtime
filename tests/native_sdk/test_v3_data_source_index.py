"""Owned source-index wire/continuation tests. No game is attached.
SPDX-License-Identifier: MPL-2.0
"""
import unittest
from unittest.mock import Mock
from pdx_runtime import v3_data as data

class SourceIndexTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock(pid=1)
        self.client.request.side_effect = lambda c: ({"status":"ok", "request_id":7, "error":0}
            if c.startswith("interface-callback ") else {"status":"ok", "completed_request_id":7})
        self.process = Mock(pid=1)
        self.process.resolve_export.return_value = 0x1000
        self.process.allocate.return_value = 0x2000

    def submit(self, index=0, **kwargs):
        return data.invoke_source_index_action(self.client, "GetGameRules.GetRulePresets", index,
            "GetGameRules.OnSelectPreset", process=self.process, **kwargs)

    def test_one_existing_element_wire_with_derived_index_and_observed_world(self):
        query = self.submit(2)
        raw = self.process.write.call_args.args[1]
        h = data.REQUEST.unpack_from(raw)
        a = data.ACTION_SUFFIX.unpack_from(raw, data.REQUEST.size)
        e = data.ELEMENT_SUFFIX.unpack_from(raw, data.REQUEST.size + data.ACTION_SUFFIX.size)
        self.assertEqual((h[2], h[3], h[5], h[6]), (21, data.EXPLICIT_ACTION, 2, 0xffffffff))
        self.assertEqual(e[0], 0)
        self.assertEqual(data.ANY_INPUT.unpack_from(raw, a[0]), (data.Kind.I32, 0, 0, 0))  # placeholder; native derives 2 only after selection
        self.assertEqual(self.process.resolve_export.call_args.args,
            ("pdx_v3_data.dll", "PdxV3DataInvokeSourceIndexInterface"))
        self.assertEqual(query.result_address, 0x2000)
        self.assertEqual(sum(c.args[0].startswith("interface-callback ") for c in self.client.request.call_args_list), 1)

    def test_invalid_signed_index_never_submits(self):
        for index in (-1, 0x80000000):
            with self.subTest(index=index), self.assertRaises((ValueError, OverflowError)):
                self.submit(index)
        self.process.allocate.assert_not_called()
        self.client.request.assert_not_called()

    def test_old_capability_missing_never_uses_unsafe_plain_property_action(self):
        self.process.resolve_export.side_effect = LookupError("missing source-index capability")
        with self.assertRaises(LookupError): self.submit()
        self.process.allocate.assert_not_called()
        self.client.request.assert_not_called()

    def test_pending_then_original_accepted_partial_result_no_replay(self):
        query = self.submit()
        raw = bytearray(self.process.write.call_args.args[1])
        self.client.request.side_effect = lambda _: {"status":"ok", "completed_request_id":6}
        self.assertIsNone(query.read())
        self.process.free.assert_not_called()
        h = list(data.REQUEST.unpack_from(raw))
        h[1] = 234; h[9] = 5; h[11] = data.Kind.STRING; h[19] = 2
        raw[h[24]:h[24]+2] = b"ab"
        data.REQUEST.pack_into(raw, 0, *h)
        a = list(data.ACTION_SUFFIX.unpack_from(raw, data.REQUEST.size))
        a[2:5] = [4, 1, 1]
        data.ACTION_SUFFIX.pack_into(raw, data.REQUEST.size, *a)
        e = list(data.ELEMENT_SUFFIX.unpack_from(raw, data.REQUEST.size + data.ACTION_SUFFIX.size))
        e[-1] = 3; e[5] = 9
        data.ELEMENT_SUFFIX.pack_into(raw, data.REQUEST.size + data.ACTION_SUFFIX.size, *e)
        self.process.read.return_value = bytes(raw)
        self.client.request.side_effect = lambda _: {"status":"ok", "completed_request_id":7}
        result = query.read()
        self.assertEqual((result.action.invoked, result.action.accepted, result.action.result.error), (1, 1, 234))
        self.assertEqual((result.action.result.snapshot, result.action.result.total), (b"ab", 5))
        self.assertEqual((result.source_world, result.source_count), (0, 3))
        self.assertEqual(self.process.write.call_count, 1)
        self.assertEqual(self.process.free.call_count, 1)


if __name__ == "__main__": unittest.main()
