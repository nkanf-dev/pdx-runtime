"""Options wire/scoped ownership composition only; native host tests cover C++ paths."""
from __future__ import annotations
import struct
import unittest
from unittest.mock import Mock, patch
from pdx_runtime import v3, v3_access, v3_options
from pdx_runtime.native_bridge import BridgeError
from pdx_runtime.v3_wire import V3_EXPORT_ALIASES

class OptionsTests(unittest.TestCase):
    def _wire_request_fixture(self, command):
        """CPU transport: preserve raw responses through exact V3 wire commands."""
        if command == "world-status":
            return self.world_status()
        words = command.split()
        if len(words) == 3 and words[0] == "interface-callback":
            callback, address = int(words[1], 0), int(words[2], 0)
            self.assertEqual(command, f"interface-callback {callback:#x} {address:#x}")
            return self.interface_callback(callback, address)
        raise AssertionError(f"unexpected V3 fixture wire command: {command!r}")

    def setUp(self):
        self.client=Mock(spec_set=["pid", "request", "status"], pid=123)
        self.world_status = Mock()
        self.interface_callback = Mock()
        self.client.request.side_effect = self._wire_request_fixture
        self.interface_callback.return_value={"status":"ok", "error": 0,"request_id":7}
        self.world_status.return_value={"status":"ok","completed_request_id":6}
        self.process=Mock(pid=123)
        exports={"PdxV3OptionsCallback":0x40000,"PdxV3AccessCallback":0x50000}
        def resolve(module, name, *, aliases):
            self.assertIs(aliases, V3_EXPORT_ALIASES)
            return exports[name]
        self.process.resolve_export.side_effect=resolve
        self.process.allocate.return_value=0x20000
    def submit(self, action, **kwargs):
        return v3_options.submit(self.client,action,mode=1,flag=0,dispatch="interface",
            adapter=0xA0000,access_adapter=0xB0000,process=self.process,**kwargs)
    def result(self,action=1,ownership=1,options=0x60000,error=0,source=0,settings=0):
        return v3_options.REQUEST.pack(64,error,action,ownership,options,source,settings,0,1,1234,0,0)
    def access_result(self, query, payload, *, error=0):
        header=v3_access.REQUEST.pack(88,error,1,0,0x40000,query.callee_address,
            query.source_address,0,0,0xABC,0xCDE,1234,1,1,0)
        return header+payload+b"source\0"
    def test_create_uses_whole_scoped_buffer_and_pending_keeps_storage(self):
        query=self.submit(v3_options.Action.CREATE)
        self.assertIsInstance(query,v3.CallbackResult)
        self.assertEqual((query.options_action,query.options_pointer,query.source_settings_address),(1,0,0))
        self.interface_callback.assert_called_once_with(0x50000,0x20000)
        fields=v3_options.REQUEST.unpack(self.process.write.call_args_list[-1].args[1][88:152])
        self.assertEqual(fields[:7],(64,997,1,0,0,0,0))
        self.assertIsNone(query.read());self.process.free.assert_not_called()
    def test_copied_native_owner_survives_request_free_and_has_no_auto_destructor(self):
        query=self.submit(v3_options.Action.CREATE)
        self.world_status.return_value={"status":"ok","completed_request_id":7}
        self.process.read.return_value=self.access_result(query,self.result())
        outer=query.read();owned=outer.callee_result
        self.assertEqual(outer.error,0);self.assertEqual((outer.entered,outer.left),(1,1))
        self.assertTrue(owned.owns_pointer);self.assertEqual(owned.options,0x60000)
        self.assertIs(query.read(),outer)
        self.process.free.assert_called_once_with(0x20000)
        self.assertFalse(hasattr(owned,"__del__"))
        self.interface_callback.assert_called_once()
    def test_explicit_copy_has_constructed_source_address_not_synthetic_settings_bytes(self):
        query=self.submit(v3_options.Action.COPY_SETTINGS,options=0x60000,source_settings=0x70008)
        payload=self.process.write.call_args_list[-1].args[1][88:152]
        fields=v3_options.REQUEST.unpack(payload)
        self.assertEqual(fields[:7],(64,997,4,1,0x60000,0x70008,0))
        self.assertEqual(query.source_settings_address,0x70008)
        self.assertEqual(v3_options.SETTINGS_BYTES,112);self.assertEqual(v3_options.OPTIONS_BYTES,224)
    def test_known_create_copy_failure_no_owner_is_not_scope_failure(self):
        query=self.submit(5,source_settings=0x70008)
        self.world_status.return_value={"status":"ok","completed_request_id":7}
        self.process.read.return_value=self.access_result(query,self.result(
            action=5,ownership=0,options=0,error=574,source=0x70008))
        outer=query.read()
        self.assertEqual(outer.error,0)
        self.assertEqual(outer.callee_result.error,574)
        self.assertFalse(outer.callee_result.owns_pointer)
        self.assertFalse(outer.callee_result.ownership_unknown)
        self.interface_callback.assert_called_once()
    def test_partial_factory_and_started_destroy_unknown_state_preserves_raw_output(self):
        for action,pointer in [(1,0x60000),(2,0)]:
            with self.subTest(action=action):
                wire=bytearray(self.result(action=action,ownership=3,options=pointer,error=574))
                struct.pack_into("<Q",wire,40,0x60000) # diagnostic acted-on pointer, not another owner
                result=v3_options.OptionsResult.from_bytes(wire)
                self.assertTrue(result.ownership_unknown)
                self.assertEqual(result.options,pointer)
                self.assertEqual(result.operation_options,0x60000)
                self.assertFalse(result.owns_pointer)
                self.assertEqual(result.started,1)
    def test_destroy_is_one_explicit_operation_pointer_clears_only_after_native_result(self):
        query=self.submit(2,options=0x60000)
        self.assertEqual(query.options_pointer,0x60000)
        self.assertIsNone(query.read())
        self.world_status.return_value={"status":"ok","completed_request_id":7}
        self.process.read.return_value=self.access_result(query,self.result(
            action=2,ownership=2,options=0))
        result=query.read().callee_result
        self.assertEqual((result.error,result.ownership,result.options),(0,2,0))
        self.assertFalse(result.owns_pointer)
        self.interface_callback.assert_called_once()
    def test_unknown_delivery_preserves_all_scope_request_addresses_without_replay(self):
        self.interface_callback.side_effect=BridgeError("ReadFile",109)
        with self.assertRaises(BridgeError) as caught:
            self.submit(4,options=0x60000,source_settings=0x70008)
        query=caught.exception.query
        self.assertEqual((query.options_action,query.options_pointer,query.source_settings_address),
                         (4,0x60000,0x70008))
        self.assertEqual(query.result_address,0x20000)
        self.assertEqual(query.callee_address,0x20058)
        self.process.free.assert_not_called();self.process.close.assert_not_called()
        self.interface_callback.assert_called_once()
    def test_unknown_with_internally_owned_process_does_not_close_or_free(self):
        self.interface_callback.side_effect=BridgeError("ReadFile",109)
        with patch.object(v3_options,"RemoteProcess",return_value=self.process):
            with self.assertRaises(BridgeError) as caught:
                v3_options.create(self.client,mode=1,flag=0,dispatch="interface",adapter=0xA0000)
        self.assertIs(caught.exception.query.process,self.process)
        self.process.free.assert_not_called();self.process.close.assert_not_called()
    def test_missing_export_closes_only_prequeue_owned_process(self):
        self.process.resolve_export.side_effect=BridgeError("resolve_export",126)
        with patch.object(v3_options,"RemoteProcess",return_value=self.process):
            with self.assertRaises(BridgeError):
                v3_options.create(self.client,mode=1,flag=0,dispatch="interface")
        self.process.close.assert_called_once();self.interface_callback.assert_not_called()
    def test_returned_layout_and_borrowed_settings_do_not_copy_or_clone_an_owner(self):
        result=v3_options.OptionsResult.from_bytes(self.result(action=3,settings=0x60008))
        self.assertEqual(result.settings_address,0x60008)
        self.assertTrue(result.owns_pointer)
        with self.assertRaises(ValueError):v3_options.OptionsResult.from_bytes(bytes(64))
if __name__=="__main__":unittest.main()
