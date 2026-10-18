from __future__ import annotations
from dataclasses import replace
import struct
import unittest
from unittest.mock import Mock
from pdx_runtime import v3_data as data

class DataTests(unittest.TestCase):
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
        self.process=Mock(pid=1);self.process.resolve_export.return_value=0x1000;self.process.allocate.return_value=0x2000
    def test_native_layouts_and_full_id_binding(self):
        self.assertEqual((data.REQUEST.size,data.TYPE_INFO.size,data.FUNCTION_INFO.size),(368,424,448))
        query=data.evaluate(self.client,"Country.GetGDP",[data.ObjectBinding.country(2,0xff000001)],process=self.process)
        initial=self.process.write.call_args.args[1];header=data.REQUEST.unpack_from(initial)
        self.assertEqual(header[22]%8,0)
        self.assertEqual(data.BINDING_INPUT.unpack_from(initial,header[22]),(2,2,0xff000001))
        self.assertEqual(initial[header[20]:header[20]+header[21]],b"Country.GetGDP")
        self.world_status.return_value={"status":"ok","completed_request_id":6}
        self.assertIsNone(query.read());self.process.free.assert_not_called()
    def test_typetable_exact_rows(self):
        raw=data.TYPE_INFO.pack(2,7,4,1,0x30,0x40,0x50,b"Country",b"test")
        initial=bytearray(data._request(data.Action.TYPES,capacity=1,output_bytes=len(raw)))
        fields=list(data.REQUEST.unpack_from(initial));fields[1]=0;fields[7]=1;fields[8]=3;fields[9]=3;fields[19]=len(raw)
        initial[:data.REQUEST.size]=data.REQUEST.pack(*fields);initial[fields[24]:]=raw
        result=data.DataResult.from_bytes(bytes(initial));self.assertEqual(result.rows[0].name,"Country");self.assertTrue(result.rows[0].can_bind_object)
    def test_function_return_abi_not_invented(self):
        raw=data.FUNCTION_INFO.pack(2,9,0,1,6,0,0x300,0x400,0,0,0,b"GetGDP",b"")
        fn=data.FunctionInfo.from_bytes(raw);self.assertEqual(fn.callbacks,(0x400,0,0,0));self.assertEqual(fn.return_type_id,9)
    def test_typed_snapshot_raw_bits_preserved(self):
        result=data.DataResult.from_bytes(data._request(data.Action.SNAPSHOT,value_handle=42))
        fixed=replace(result,error=0,kind=data.Kind.FIXED,raw_bits=0xffffffffffffffff)
        self.assertEqual(fixed.value.signed,-1);self.assertEqual(fixed.value.bits,0xffffffffffffffff)
        numeric=replace(result,error=0,kind=data.Kind.DOUBLE,raw_bits=struct.unpack("<Q",struct.pack("<d",2.5))[0]);self.assertEqual(numeric.value,2.5)
        obj=replace(result,error=0,kind=data.Kind.OBJECT,type_id=2,object=0x55,value_handle=42);self.assertEqual(obj.value.binding(),data.ObjectBinding(2,42,True))
        with self.assertRaises(ValueError):replace(obj,type_id=-1).value.binding()
    def test_release_distinct_from_callback_storage(self):
        query=data.snapshot(self.client,42,process=self.process)
        initial=self.process.write.call_args.args[1];fields=list(data.REQUEST.unpack_from(initial));fields[1]=0;fields[4]=2
        self.process.read.return_value=data.REQUEST.pack(*fields)+initial[data.REQUEST.size:]
        result=query.read();self.assertEqual(result.value_handle,42);self.assertEqual(result.type_id,2);self.process.free.assert_called_once_with(0x2000)
        release=query.release_value(process=self.process);self.assertEqual(self.interface_callback.call_count,2)
        self.assertEqual(data.REQUEST.unpack_from(self.process.write.call_args.args[1])[2],data.Action.RELEASE)
    def test_list_count_and_element_use_native_actions_without_getter_replay(self):
        count=data.list_count(self.client,42,process=self.process)
        fields=data.REQUEST.unpack_from(self.process.write.call_args.args[1]);self.assertEqual(fields[2],data.Action.LIST_COUNT);self.assertEqual(fields[14],42)
        element=data.list_element(self.client,42,3,output_bytes=64,process=self.process)
        fields=data.REQUEST.unpack_from(self.process.write.call_args.args[1]);self.assertEqual(fields[2],data.Action.LIST_ELEMENT);self.assertEqual(fields[5],3);self.assertEqual(fields[14],42)
        self.assertEqual(fields[25],64);self.assertEqual(self.interface_callback.call_count,2)
        with self.assertRaises(ValueError):data.list_element(self.client,42,-1,process=self.process)

    def test_action_explicit_intent_typed_inputs_and_exact_wire(self):
        query=data.invoke_action(self.client,"Type.SetValue",data.TypedInput.float32(1.5),
            [data.ObjectBinding(2,0x1234)],process=self.process)
        raw=self.process.write.call_args.args[1];fields=data.REQUEST.unpack_from(raw)
        suffix=data.ACTION_SUFFIX.unpack_from(raw,data.REQUEST.size)
        self.assertEqual(fields[2:4],(data.Action.INVOKE_ACTION,1))
        self.assertEqual(data.ACTION_HEADER_SIZE,392);self.assertEqual(fields[20],392)
        self.assertEqual(suffix[0]%8,0);self.assertEqual(data.ANY_INPUT.unpack_from(raw,suffix[0]),(data.Kind.FLOAT,0,0x3fc00000,0))
        self.assertEqual(fields[24]%8,0)
        self.world_status.return_value={"status":"ok","completed_request_id":6}
        self.assertIsNone(query.read());self.process.free.assert_not_called()

    def test_action_native_rejection_keeps_post_any_owned_without_replay(self):
        query=data.invoke_action(self.client,"Type.SetValue",data.TypedInput.retained(42),process=self.process)
        raw=self.process.write.call_args.args[1];fields=list(data.REQUEST.unpack_from(raw));fields[1]=0;fields[14]=43
        suffix=list(data.ACTION_SUFFIX.unpack_from(raw,data.REQUEST.size));suffix[2:5]=[3,1,0]
        self.process.read.return_value=data.REQUEST.pack(*fields)+data.ACTION_SUFFIX.pack(*suffix)+raw[data.ACTION_HEADER_SIZE:]
        result=query.read();self.assertEqual((result.compiled_mode,result.invoked,result.accepted),(3,1,0));self.assertEqual(result.value_handle,43)
        self.assertIs(query.read(),result);self.assertEqual(self.interface_callback.call_count,1)
        query.release_value(process=self.process);self.assertEqual(self.interface_callback.call_count,2)
        self.assertEqual(data.REQUEST.unpack_from(self.process.write.call_args.args[1])[2],data.Action.RELEASE)

    def test_action_input_types_preserve_bits_and_string_lifetime_wire(self):
        inputs=[data.TypedInput.boolean(True),data.TypedInput.i32(-3),data.TypedInput.u32(3),data.TypedInput.i64(-7),data.TypedInput.u64(7),data.TypedInput.float64(2.5),data.TypedInput.fixed(data.FixedPoint(100000)),data.TypedInput.empty()]
        for value in inputs:self.assertEqual(len(value.pack()),24)
        string=data.TypedInput.string("真实输入").pack();kind,length,bits,handle=data.ANY_INPUT.unpack_from(string)
        self.assertEqual(kind,data.Kind.STRING);self.assertEqual(length,len("真实输入".encode()));self.assertEqual(string[24:],"真实输入".encode())
        self.assertEqual(data.ANY_INPUT.unpack(data.TypedInput.i64(-7).pack())[2],0xfffffffffffffff9)
        self.assertEqual(data.ANY_INPUT.unpack(data.TypedInput.retained(42).pack()),(data.RETAINED_INPUT,0,0,42))
        utf8=data.TypedInput.utf8_string("真实输入").pack()
        self.assertEqual(data.ANY_INPUT.unpack_from(utf8),(data.UTF8_INPUT,len("真实输入".encode()),0,0))
        self.assertEqual(utf8[24:],string[24:]);self.assertNotEqual(utf8[:4],string[:4])

    def test_full_id_bindings_keep_domain_id_and_old_country_forms(self):
        constructors=[("Country",data.ObjectBinding.country,2),("State",data.ObjectBinding.state,3),
            ("Pop",data.ObjectBinding.pop,4),("Market",data.ObjectBinding.market,5),("Building",data.ObjectBinding.building,6)]
        for domain,constructor,flag in constructors:
            binding=constructor(123,0xab000001)
            self.assertEqual(data.BINDING_INPUT.unpack(binding.pack()),(123,flag,0xab000001))
            self.assertEqual(data.BINDING_INPUT.unpack(data.ObjectBinding.full_id(domain,123,0xab000001).pack()),(123,flag,0xab000001))
        self.assertEqual(data.ObjectBinding(83,1,country_id=True).pack(),data.ObjectBinding.country(83,1).pack())
        with self.assertRaises(ValueError):data.ObjectBinding.full_id("State",85,-1)
        with self.assertRaises(ValueError):data.ObjectBinding.full_id("unknown",85,1)

    def test_property_action_preserves_native_expression_arguments_not_input_any(self):
        expression="Country.SortStates('name')"
        data.invoke_property_action(self.client,expression,[data.ObjectBinding.country(83,1)],process=self.process)
        raw=self.process.write.call_args.args[1];fields=data.REQUEST.unpack_from(raw);suffix=data.ACTION_SUFFIX.unpack_from(raw,data.REQUEST.size)
        self.assertEqual(raw[fields[20]:fields[20]+fields[21]],expression.encode())
        self.assertEqual(data.ANY_INPUT.unpack_from(raw,suffix[0]),(data.Kind.EMPTY,0,0,0))
        self.assertEqual(fields[2:4],(data.Action.INVOKE_ACTION,data.EXPLICIT_ACTION))
        self.assertEqual(self.interface_callback.call_count,1)

    def test_isolated_state_name_context_and_draft_use_owned_native_actions(self):
        data.create_state_name_context(self.client,0xab000005,process=self.process)
        initial=self.process.write.call_args.args[1];fields=data.REQUEST.unpack_from(initial)
        self.assertEqual(fields[2],data.Action.CREATE_STATE_NAME_CONTEXT);self.assertEqual(fields[5],0xab000005)
        data.state_name_draft(self.client,42,index=1,output_bytes=128,process=self.process)
        fields=data.REQUEST.unpack_from(self.process.write.call_args.args[1]);self.assertEqual((fields[2],fields[5],fields[14],fields[25]),(data.Action.STATE_NAME_DRAFT,1,42,128))
        with self.assertRaises(ValueError):data.state_name_draft(self.client,42,index=6,process=self.process)
        with self.assertRaises(ValueError):data.create_state_name_context(self.client,-1,process=self.process)

    def test_shared_data_command_snapshot_is_borrowed_not_unique_owner(self):
        initial=bytearray(data._request(data.Action.SNAPSHOT,value_handle=42,output_bytes=24))
        fields=list(data.REQUEST.unpack_from(initial));fields[1]=0;fields[4]=3;fields[11]=data.Kind.COMMAND;fields[18]=fields[19]=fields[9]=24;fields[-1]=b".?AU?$SAnyType@VCDataCommand@@@PdxAnyImpl@@"
        initial[:data.REQUEST.size]=data.REQUEST.pack(*fields);initial[data.REQUEST.size:]=struct.pack("<3Q",0x100,0x200,0x118)
        result=data.DataResult.from_bytes(initial);value=result.value
        self.assertEqual((value.primary_pointer,value.shared_control,value.game_command_pointer,value.value_handle),(0x100,0x200,0x118,42))
        self.assertNotIsInstance(value,data.NativeObject)
        with self.assertRaises(ValueError):replace(result,snapshot_bytes=8).value
        data.state_name_override(self.client,0xef000005,index=1,process=self.process)
        fields=data.REQUEST.unpack_from(self.process.write.call_args.args[1]);self.assertEqual((fields[2],fields[3],fields[5]),(data.Action.STATE_NAME_OVERRIDE,1,0xef000005))
        with self.assertRaises(ValueError):data.state_name_override(self.client,5,index=6,process=self.process)

    def test_command_value_keeps_generation_world_and_native24_snapshot_contract(self):
        data.command_value(self.client,42,process=self.process)
        fields=data.REQUEST.unpack_from(self.process.write.call_args.args[1]);self.assertEqual((fields[2],fields[14],fields[25]),(data.Action.COMMAND_VALUE,42,32))
        initial=bytearray(data._request(data.Action.COMMAND_VALUE,value_handle=42,output_bytes=32))
        fields=list(data.REQUEST.unpack_from(initial));fields[1]=0;fields[9]=fields[19]=32
        initial[:data.REQUEST.size]=data.REQUEST.pack(*fields);initial[data.REQUEST.size:]=struct.pack("<4Q",0x100,0x200,0x118,0x9999)
        result=data.DataResult.from_bytes(initial);self.assertEqual(result.value.generation_world,0x9999)
        self.assertEqual(result.value.primary_pointer,0x100)
        with self.assertRaises(ValueError):replace(result,snapshot_bytes=24).value

    def test_bad_snapshot_bounds_rejected(self):
        fields=list(data.REQUEST.unpack(data._request(data.Action.SNAPSHOT)));fields[19]=4;fields[24]=0
        with self.assertRaises(ValueError):data.DataResult.from_bytes(data.REQUEST.pack(*fields))

if __name__=="__main__":unittest.main()
