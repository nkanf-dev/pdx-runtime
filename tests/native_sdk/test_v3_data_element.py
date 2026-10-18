"""Wire/ownership tests only. Native behavior requires Root's staged DLL/live run."""
from __future__ import annotations
import unittest
from unittest.mock import Mock
from pdx_runtime import v3_data as data

class ElementTests(unittest.TestCase):
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
        self.client=Mock(spec_set=["pid", "request", "status"], pid=1)
        self.world_status = Mock()
        self.interface_callback = Mock()
        self.client.request.side_effect = self._wire_request_fixture
        self.interface_callback.return_value={"status":"ok", "error": 0,"request_id":7}
        self.world_status.return_value={"status":"ok","completed_request_id":7}
        self.process=Mock(pid=1)
        self.process.resolve_export.return_value=0x1000
        self.process.allocate.return_value=0x2000

    def packet(self):
        return self.process.write.call_args.args[1]

    def test_compatible_prefix_and_original_expressions(self):
        data.evaluate_element(self.client,"Owner.GetOptions",2,"Option.GetText",
            [data.ObjectBinding(498,42,retained=True)],world=0x8888,
            expected_count=3,output_bytes=128,process=self.process)
        raw=self.packet(); fields=data.REQUEST.unpack_from(raw)
        suffix=data.ELEMENT_SUFFIX.unpack_from(raw,data.ACTION_HEADER_SIZE)
        self.assertEqual((data.REQUEST.size,data.ACTION_HEADER_SIZE,data.ELEMENT_HEADER_SIZE),(368,392,432))
        self.assertEqual(fields[2:7],(13,0,-1,2,3))
        self.assertEqual(suffix[0],0x8888)
        self.assertEqual(raw[fields[20]:fields[20]+fields[21]],b"Option.GetText")
        self.assertEqual(raw[suffix[1]:suffix[1]+suffix[2]],b"Owner.GetOptions")
        self.assertEqual(data.BINDING_INPUT.unpack_from(raw,fields[22]),(498,1,42))
        self.assertEqual(fields[22]%8,0);self.assertEqual(fields[24]%8,0)
        self.assertEqual(self.interface_callback.call_count,1)

    def test_explicit_action_condition_and_no_pending_replay(self):
        q=data.invoke_element_action(self.client,"Owner.GetOptions",0,"Option.Select",
            data.TypedInput.empty(),world=0x8888,condition="Option.IsEnabled",process=self.process)
        raw=self.packet(); fields=data.REQUEST.unpack_from(raw)
        suffix=data.ELEMENT_SUFFIX.unpack_from(raw,data.ACTION_HEADER_SIZE)
        self.assertEqual(fields[2:4],(14,1));self.assertEqual(fields[6],0xffffffff)
        self.assertEqual(raw[suffix[3]:suffix[3]+suffix[4]],b"Option.IsEnabled")
        self.world_status.return_value={"status":"ok","completed_request_id":6}
        self.assertIsNone(q.read());self.assertIsNone(q.read())
        self.process.free.assert_not_called()
        self.assertEqual(self.interface_callback.call_count,1)

    def completed(self, *, error=0,kind=data.Kind.BOOL,bits=1,invoked=0,accepted=0,pointer=0):
        raw=bytearray(self.packet()); fields=list(data.REQUEST.unpack_from(raw))
        fields[1]=error;fields[11]=kind;fields[13]=pointer;fields[26]=bits
        raw[:data.REQUEST.size]=data.REQUEST.pack(*fields)
        action=list(data.ACTION_SUFFIX.unpack_from(raw,data.REQUEST.size));action[2:5]=[3,invoked,accepted]
        raw[data.REQUEST.size:data.ACTION_HEADER_SIZE]=data.ACTION_SUFFIX.pack(*action)
        elem=list(data.ELEMENT_SUFFIX.unpack_from(raw,data.ACTION_HEADER_SIZE));elem[5:]=[497,1,1,1]
        raw[data.ACTION_HEADER_SIZE:data.ELEMENT_HEADER_SIZE]=data.ELEMENT_SUFFIX.pack(*elem)
        return bytes(raw)

    def test_copied_boolean_no_retained_element_handle(self):
        q=data.evaluate_element(self.client,"Owner.GetOptions",0,"Option.IsEnabled",world=0x88,process=self.process)
        self.process.read.return_value=self.completed()
        result=q.read();self.assertTrue(result.value)
        self.assertEqual(result.result.value_handle,0);self.assertEqual(result.element_type_id,497)
        self.assertEqual(result.source_count,1)
        self.assertIs(q.read(),result);self.process.free.assert_called_once_with(0x2000)
        self.assertEqual(self.interface_callback.call_count,1)

    def test_error_after_invocation_keeps_exact_receipt(self):
        q=data.invoke_element_action(self.client,"Owner.GetOptions",0,"Option.Select",
            data.TypedInput.empty(),world=0x88,process=self.process)
        self.process.read.return_value=self.completed(error=234,kind=data.Kind.EMPTY,bits=0,invoked=1,accepted=1)
        result=q.read();self.assertEqual((result.error,result.invoked,result.accepted),(234,1,1))
        self.assertEqual(result.result.value_handle,0)
        self.assertIs(q.read(),result);self.assertEqual(self.interface_callback.call_count,1)

    def test_decoder_rejects_any_element_pointer_escape(self):
        data.evaluate_element(self.client,"Owner.GetOptions",0,"Option.GetText",world=0x88,process=self.process)
        with self.assertRaises(ValueError):data.ElementResult.from_bytes(self.completed(pointer=0x1234))

    def test_input_validation_is_prequeue(self):
        for kwargs in ({"world":0},{"world":-1},{"world":1,"expected_count":-1},{"world":1,"output_bytes":-1}):
            with self.assertRaises(ValueError):data.evaluate_element(self.client,"Owner.GetOptions",0,"Option.GetText",process=self.process,**kwargs)
        with self.assertRaises(ValueError):data.evaluate_element(self.client,"Owner.GetOptions",-1,"Option.GetText",world=1,process=self.process)
        with self.assertRaises(ValueError):data.evaluate_element(self.client,"Owner.GetOptions\0",0,"Option.GetText",world=1,process=self.process)
        self.interface_callback.assert_not_called()


if __name__=="__main__":unittest.main()
