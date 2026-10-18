"""Owned context wire/decoder fixtures, without a game process.
SPDX-License-Identifier: MPL-2.0
"""
import unittest
from unittest.mock import Mock
from pdx_runtime import v3_data as data

class OwnedContextTests(unittest.TestCase):
    def setUp(self):
        self.client=Mock(pid=1)
        self.client.request.side_effect=lambda c: ({"status":"ok","request_id":7,"error":0}
            if c.startswith("interface-callback ") else {"status":"ok","completed_request_id":7})
        self.process=Mock(pid=1);self.process.allocate.return_value=0x2000
        self.process.resolve_export.return_value=0xA100;self.process.export_owner.return_value=0xA000

    def test_four_factories_consume_original_handles_in_one_interface(self):
        factories=(data.create_release_country_context,data.create_power_bloc_context,
                   data.create_military_formation_name_context,data.create_power_bloc_formation_context)
        for kind,factory in enumerate(factories,1):
            with self.subTest(kind=kind):
                query=factory(self.client,901,output_bytes=24,process=self.process)
                header=data.REQUEST.unpack_from(self.process.write.call_args.args[1])
                self.assertEqual((header[2],header[3],header[14],header[25]),(22,kind,901,24))
                self.assertEqual(query.adapter,0xA000)
                self.assertEqual(self.process.resolve_export.call_args.args[1],"PdxV3DataCreateOwnedContextInterface")
        self.assertEqual(self.process.allocate.call_count,4)
        self.assertEqual(sum(c.args[0].startswith("interface-callback ") for c in self.client.request.call_args_list),4)

    def test_original_partial_error_keeps_new_owned_handle_and_exact_nondata_type(self):
        query=data.create_release_country_context(self.client,901,process=self.process)
        raw=bytearray(self.process.write.call_args.args[1]);h=list(data.REQUEST.unpack_from(raw))
        h[1]=234;h[4]=17;h[11]=data.Kind.OBJECT;h[13]=0xABC0;h[14]=902
        h[28]=b".?AU?$SAnyType@V?$CPdxPtr@VCReleaseCountryWindow@@@@@PdxAnyImpl@@".ljust(192,b"\0")
        data.REQUEST.pack_into(raw,0,*h);self.process.read.return_value=bytes(raw)
        result=query.read()
        self.assertEqual((result.error,result.value_handle,result.value.pointer,result.value.type_id),(234,902,0xABC0,17))
        self.assertIn("VCReleaseCountryWindow",result.value.native_type_name)
        self.assertEqual(self.process.free.call_count,1) # callback storage, not native Any

    def test_missing_capability_does_not_publish_input_handle_as_new_output(self):
        self.process.resolve_export.side_effect=LookupError("owned context unavailable")
        with self.assertRaises(LookupError):data.create_power_bloc_context(self.client,901,process=self.process)
        self.process.allocate.assert_not_called()

    def test_pending_preserves_original_request(self):
        query=data.create_military_formation_name_context(self.client,901,process=self.process)
        self.client.request.side_effect=lambda _: {"status":"ok","completed_request_id":6}
        self.assertIsNone(query.read());self.process.free.assert_not_called()


if __name__=="__main__":unittest.main()
