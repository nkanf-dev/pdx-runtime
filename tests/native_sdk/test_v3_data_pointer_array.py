"""Focused wire/static-scoped-source tests; no native/game behavior claim."""
import unittest
from unittest.mock import Mock
from pdx_runtime import v3_data as d

class PointerArrayTests(unittest.TestCase):
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
        self.process=Mock(pid=1);self.process.resolve_export.return_value=0x1000
        self.process.allocate.return_value=0x2000
        self.source=d.PointerArraySource("NativeRoot.Self",16,498,1234,index=2,expected_count=3)
    def packet(self):return self.process.write.call_args.args[1]
    def test_new472_old368392432_abi_unchanged(self):
        self.assertEqual((d.REQUEST.size,d.ACTION_HEADER_SIZE,d.ELEMENT_HEADER_SIZE,d.ARRAY_ELEMENT_HEADER_SIZE),(368,392,432,472))
        d.evaluate_element(self.client,"NativeContext.GetOptions",1,"Option.IsEnabled",
            world=0x88,pointer_array=self.source,process=self.process)
        raw=self.packet();h=d.REQUEST.unpack_from(raw)
        a=d.POINTER_ARRAY_SUFFIX.unpack_from(raw,d.ELEMENT_HEADER_SIZE)
        self.assertEqual(h[2],15);self.assertEqual(h[20],472)
        self.assertEqual(raw[a[0]:a[0]+a[1]],b"NativeRoot.Self")
        self.assertEqual(a[2:],(16,498,2,3,1234,0,0,0))
        self.assertEqual(h[22]%8,0);self.assertEqual(h[24]%8,0)
        self.assertEqual(self.interface_callback.call_count,1)
    def test_action_condition_remains_original_vm_and_explicit(self):
        d.invoke_element_action(self.client,"NativeContext.GetOptions",0,"Option.Select",
            d.TypedInput.empty(),world=0x88,condition="Option.IsEnabled",
            pointer_array=self.source,process=self.process)
        raw=self.packet();h=d.REQUEST.unpack_from(raw);e=d.ELEMENT_SUFFIX.unpack_from(raw,d.ACTION_HEADER_SIZE)
        self.assertEqual(h[2:4],(16,1))
        self.assertEqual(raw[e[3]:e[3]+e[4]],b"Option.IsEnabled")
        self.assertEqual(d.POINTER_ARRAY_SUFFIX.unpack_from(raw,d.ELEMENT_HEADER_SIZE)[7],0)
    def test_count_only_and_direct_context_are_distinct(self):
        d.pointer_array_count(self.client,self.source,world=0x88,process=self.process)
        raw=self.packet();h=d.REQUEST.unpack_from(raw);e=d.ELEMENT_SUFFIX.unpack_from(raw,d.ACTION_HEADER_SIZE)
        self.assertEqual(h[21],0);self.assertEqual(e[2],0)
        self.assertEqual(d.POINTER_ARRAY_SUFFIX.unpack_from(raw,d.ELEMENT_HEADER_SIZE)[7],d.ARRAY_COUNT_ONLY)
        d.evaluate_pointer_array(self.client,self.source,"NativeContext.GetTitle",world=0x88,process=self.process)
        raw=self.packet();h=d.REQUEST.unpack_from(raw)
        self.assertEqual(raw[h[20]:h[20]+h[21]],b"NativeContext.GetTitle")
        self.assertEqual(d.POINTER_ARRAY_SUFFIX.unpack_from(raw,d.ELEMENT_HEADER_SIZE)[7],d.ARRAY_DIRECT_CONTEXT)
    def completed(self, *, error=0,kind=d.Kind.EMPTY,root_count=0,stages=3):
        raw=bytearray(self.packet());h=list(d.REQUEST.unpack_from(raw));h[1]=error;h[11]=kind
        raw[:d.REQUEST.size]=d.REQUEST.pack(*h)
        a=list(d.POINTER_ARRAY_SUFFIX.unpack_from(raw,d.ELEMENT_HEADER_SIZE));a[8:]=[root_count,stages]
        raw[d.ELEMENT_HEADER_SIZE:d.ARRAY_ELEMENT_HEADER_SIZE]=d.POINTER_ARRAY_SUFFIX.pack(*a)
        return bytes(raw)
    def test_true_empty_source_has_completed_root_array_stages(self):
        q=d.pointer_array_count(self.client,self.source,world=0x88,process=self.process)
        self.process.read.return_value=self.completed()
        r=q.read();self.assertEqual((r.error,r.root_count,r.source_stages),(0,0,3))
        self.assertIsNone(r.value);self.assertEqual(r.result.value_handle,0)
        self.process.free.assert_called_once_with(0x2000)
    def test_wrong_owner_thread_is_not_empty_or_replayed(self):
        q=d.pointer_array_count(self.client,self.source,world=0x88,process=self.process)
        self.process.read.return_value=self.completed(error=1444,stages=0)
        r=q.read();self.assertEqual((r.error,r.source_stages),(1444,0))
        self.assertIs(q.read(),r);self.assertEqual(self.interface_callback.call_count,1)
    def test_pending_read_preserves_exact_storage_and_queue(self):
        q=d.pointer_array_count(self.client,self.source,world=0x88,process=self.process)
        self.world_status.return_value={"status":"ok","completed_request_id":6}
        self.assertIsNone(q.read());self.assertIsNone(q.read())
        self.process.free.assert_not_called();self.assertEqual(self.interface_callback.call_count,1)
    def test_invalid_source_contract_is_prequeue(self):
        for kwargs in ({"byte_offset":1},{"interface_thread_id":0},{"type_id":-1},{"index":-1},{"expected_count":-1}):
            args=dict(root_expression="NativeRoot.Self",byte_offset=16,type_id=498,interface_thread_id=1234)
            args.update(kwargs)
            with self.assertRaises(ValueError):d.PointerArraySource(**args)
        with self.assertRaises(ValueError):d._element_request("",0,"",(),world=1,input=d.TypedInput.empty(),condition="",expected_count=None,output_bytes=0,pointer_array=self.source,count_only=True)
        with self.assertRaises(ValueError):d._element_request("",0,"NativeContext.Close",(),world=1,input=d.TypedInput.empty(),condition="",expected_count=None,output_bytes=0,pointer_array=self.source,direct_context=True)
        self.interface_callback.assert_not_called()
    def guarded_source(self, tag="native-opaque-value"):
        return d.PointerArraySource("NativeRoot.Self",16,498,1234,
            guard_expression="NativeContext.GetIdentity",guard_input=d.TypedInput.string(tag))
    def test_guard496_wire_preserves_native_value_and_enabled_condition(self):
        source=self.guarded_source()
        d.invoke_element_action(self.client,"NativeContext.GetOptions",0,"Option.Select",
            d.TypedInput.empty(),world=0x88,condition="Option.IsEnabled",
            pointer_array=source,process=self.process)
        raw=self.packet();h=d.REQUEST.unpack_from(raw)
        a=d.POINTER_ARRAY_SUFFIX.unpack_from(raw,d.ELEMENT_HEADER_SIZE)
        g=d.SOURCE_GUARD_SUFFIX.unpack_from(raw,d.ARRAY_ELEMENT_HEADER_SIZE)
        self.assertEqual(d.GUARDED_ARRAY_HEADER_SIZE,496);self.assertEqual(h[20],496)
        self.assertEqual(a[7],d.ARRAY_SOURCE_GUARD)
        self.assertEqual(raw[g[0]:g[0]+g[1]],b"NativeContext.GetIdentity")
        kind,n,bits,handle=d.ANY_INPUT.unpack_from(raw,g[2])
        self.assertEqual((kind,bits,handle),(d.Kind.STRING,0,0))
        self.assertEqual(raw[g[2]+d.ANY_INPUT.size:g[2]+d.ANY_INPUT.size+n],b"native-opaque-value")
        e=d.ELEMENT_SUFFIX.unpack_from(raw,d.ACTION_HEADER_SIZE)
        self.assertEqual(raw[e[3]:e[3]+e[4]],b"Option.IsEnabled")
    def guard_completed(self, *, passed, invoked, error):
        raw=bytearray(self.packet());h=list(d.REQUEST.unpack_from(raw));h[1]=error;h[11]=d.Kind.EMPTY
        raw[:d.REQUEST.size]=d.REQUEST.pack(*h)
        action=list(d.ACTION_SUFFIX.unpack_from(raw,d.REQUEST.size));action[3:5]=[invoked,invoked]
        raw[d.REQUEST.size:d.ACTION_HEADER_SIZE]=d.ACTION_SUFFIX.pack(*action)
        a=list(d.POINTER_ARRAY_SUFFIX.unpack_from(raw,d.ELEMENT_HEADER_SIZE));a[8:]=[1,135 | (256 if passed else 0)]
        raw[d.ELEMENT_HEADER_SIZE:d.ARRAY_ELEMENT_HEADER_SIZE]=d.POINTER_ARRAY_SUFFIX.pack(*a)
        g=list(d.SOURCE_GUARD_SUFFIX.unpack_from(raw,d.ARRAY_ELEMENT_HEADER_SIZE));g[4:]=[1,passed]
        raw[d.ARRAY_ELEMENT_HEADER_SIZE:d.GUARDED_ARRAY_HEADER_SIZE]=d.SOURCE_GUARD_SUFFIX.pack(*g)
        return bytes(raw)
    def test_false_source_guard_is_no_action_and_not_replayed(self):
        q=d.invoke_element_action(self.client,"NativeContext.GetOptions",0,"Option.Select",
            d.TypedInput.empty(),world=0x88,pointer_array=self.guarded_source("wrong"),process=self.process)
        self.process.read.return_value=self.guard_completed(passed=0,invoked=0,error=1237)
        result=q.read();self.assertEqual((result.guard_checked,result.guard_passed,result.invoked),(1,0,0))
        self.assertEqual(result.error,1237);self.assertIs(q.read(),result)
        self.assertEqual(self.interface_callback.call_count,1)
    def test_true_guard_does_not_hide_post_action_copy_failure(self):
        q=d.invoke_element_action(self.client,"NativeContext.GetOptions",0,"Option.Select",
            d.TypedInput.empty(),world=0x88,pointer_array=self.guarded_source(),process=self.process)
        self.process.read.return_value=self.guard_completed(passed=1,invoked=1,error=234)
        result=q.read();self.assertEqual((result.guard_checked,result.guard_passed,result.invoked,result.error),(1,1,1,234))
        self.assertIs(q.read(),result);self.assertEqual(self.interface_callback.call_count,1)
    def test_guard_inputs_are_copied_scalars_not_old_owners(self):
        with self.assertRaises(ValueError):d.PointerArraySource("NativeRoot.Self",16,498,1234,guard_expression="NativeContext.GetIdentity")
        with self.assertRaises(ValueError):d.PointerArraySource("NativeRoot.Self",16,498,1234,guard_expression="NativeContext.GetIdentity",guard_input=d.TypedInput.retained(41))
        with self.assertRaises(ValueError):d.pointer_array_count(self.client,self.guarded_source(),world=1,process=self.process)
        self.interface_callback.assert_not_called()


if __name__=="__main__":unittest.main()
