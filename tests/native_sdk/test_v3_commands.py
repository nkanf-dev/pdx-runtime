from __future__ import annotations
import struct
import unittest
from unittest.mock import Mock
from tests.native_sdk.test_v3_query import bind_fixture_wire
from pdx_runtime import v3, v3_commands as commands, v3_session

class MemoryProcess:
    def __init__(self):
        self.pid = 123
        self.base = 0x20000
        self.memory = bytearray(65536)
        self.freed = []
        self.closed = False
    def resolve_export(self, module, symbol, *, aliases=None):
        return {"PdxV3CommandInterface":0x10000,"PdxV3CommandSession":0x11000,
                "PdxNativeCallInterface":0x12000,"PdxV3QueueSessionCallback":0x13000,
                "PdxV3SessionCallbackState":0x14000}[symbol]
    def allocate(self, size):
        self.size = size
        return self.base
    def read(self, address, size):
        offset = address-self.base
        return bytes(self.memory[offset:offset+size])
    def write(self, address, data):
        offset = address-self.base
        self.memory[offset:offset+len(data)] = data
        return len(data)
    def free(self, address): self.freed.append(address)
    def close(self): self.closed = True

class CommandsTests(unittest.TestCase):
    def setUp(self):
        self.process = MemoryProcess()
        self.client = Mock(pid=123)
        bind_fixture_wire(self.client)
        self.client.queue_v3_interface_callback.return_value = {"error": 0, "status":"ok","request_id":7}
        self.client.v3_world_status.return_value = {"status":"ok","request_id":7,"completed_request_id":6}

    def finish(self, handle, command=0x55555, error=0, world=0x88888):
        fields = list(commands.RESULT.unpack(self.process.read(handle.result_address,commands.RESULT.size)))
        fields[1],fields[4],fields[15] = error,command,world
        if not error and command:
            fields[3] = 12490  # callback queries the remaining command's type only on error0
        self.process.write(handle.result_address,commands.RESULT.pack(*fields))
        self.client.v3_world_status.return_value = {"status":"ok","request_id":7,"completed_request_id":7}

    def test_native_typed_nested_roundtrip(self):
        parameters = {10304:commands.UInt32(0xffffffff),439:commands.UInt32(5),
            10232:"building_iron_mine",22805:3,22308:commands.Token(603),
            219:{10304:commands.UInt32(1)},65:[commands.Token(602), True,
                commands.Int64(-0x8000000000000000),commands.UInt64(0xffffffffffffffff),
                commands.Float32(1.25)]}
        encoded = commands.encode_command(12490,parameters)
        decoded = commands.decode_command(encoded)
        self.assertEqual(decoded.key,commands.Token(12490))
        self.assertEqual(decoded.value[0],commands.Assignment(commands.Token(10304),commands.UInt32(0xffffffff)))
        self.assertEqual(commands.encode_command(12490,decoded.value),encoded)
        self.assertEqual(encoded[:6],struct.pack("<3H",12490,1,3))

    def test_binary_rejects_truncation_and_preserves_duplicate_fields(self):
        value=(commands.Assignment(commands.Token(439),commands.UInt32(5)),
               commands.Assignment(commands.Token(439),commands.UInt32(6)))
        encoded=commands.encode_command(12490,value)
        self.assertEqual(commands.decode_command(encoded).value,value)
        with self.assertRaises(ValueError): commands.decode_command(encoded[:-1])
        with self.assertRaises(ValueError): commands.encode_value(commands.Token(12))

    def test_parameter_input_is_in_same_owned_callback_allocation(self):
        handle=commands.create(self.client,12490,parameters={439:commands.UInt32(5)},actor_id=1,process=self.process)
        fields=commands.RESULT.unpack(self.process.read(self.process.base,commands.RESULT.size))
        self.assertEqual(fields[0],88); self.assertEqual(fields[2],commands.Action.DESERIALIZE)
        self.assertEqual(fields[5],self.process.base+88)
        self.assertEqual(self.process.read(fields[5],fields[7]),commands.encode_command(12490,{10304:commands.UInt32(1),65:{439:commands.UInt32(5)}}))
        self.assertIsNone(handle.read()); self.assertFalse(self.process.freed)
        self.finish(handle)
        result=handle.read()
        self.assertEqual(result.command,0x55555)
        self.assertEqual(self.process.freed,[self.process.base])
        self.assertIs(handle.read(),result)

    def test_explicit_ownership_pending_and_consumed_on_destroy(self):
        result=commands.CommandResult(88,0,2,12490,0x55555,0,0,0,0,4,0,0,3,0,1,0x88888,2)
        owned=commands.NativeCommand.from_result(self.client,result,process=self.process)
        handle=owned.validate()
        with self.assertRaises(RuntimeError): owned.destroy()
        self.finish(handle)
        self.assertEqual(owned.complete().command,0x55555)
        # Owner dispatch records its original intent; replacing pending is not a new attempt.
        owned.destroy()
        self.finish(owned.pending,command=0)
        owned.complete()
        with self.assertRaises(RuntimeError): owned.validate()

    def test_unknown_callback_execution_retains_pending_ownership(self):
        result=commands.CommandResult(88,0,2,12490,0x55555,0,0,0,0,4,0,0,3,0,1,0x88888,2)
        owned=commands.NativeCommand.from_result(self.client,result,process=self.process)
        handle=owned.destroy()
        self.client.v3_world_status.return_value={"status":"ok","request_id":7,
            "completed_request_id":7,"world_error":31}
        with self.assertRaises(v3.QueryExecutionError): owned.complete()
        self.assertIs(owned.pending,handle)
        with self.assertRaises(RuntimeError): owned.destroy()

    def test_submit_composes_existing_session_queue_and_no_second_transport(self):
        handle=commands.request(self.client,commands.Action.SUBMIT,command=0x55555,world=0x88888,process=self.process)
        self.assertIsInstance(handle,v3_session.SessionCallbackResult)
        fields=commands.RESULT.unpack(self.process.read(handle.opaque_address,88))
        self.assertEqual(fields[2],commands.Action.SUBMIT)
        self.assertEqual(fields[4],0x55555); self.assertEqual(fields[9],4)
        self.assertEqual(handle.callbackfn,0x11000)
        self.client.queue_v3_interface_callback.assert_called_once()

    def test_validation_reason_uses_existing_buffer_and_retains_domain_false(self):
        result=commands.CommandResult(88,0,2,12490,0x55555,0,0,0,0,4,0,0,3,0,1,0x88888,2)
        owned=commands.NativeCommand.from_result(self.client,result,process=self.process)
        handle=owned.validate(capacity=64)
        fields=list(commands.RESULT.unpack(self.process.read(self.process.base,88)))
        self.assertEqual(fields[5],self.process.base+88)
        self.assertEqual(fields[6],64)
        self.assertIsNone(owned.complete())
        reason="不能在这个地区建造".encode("utf-8")
        fields[1],fields[3],fields[7],fields[10]=0,12490,len(reason),0
        self.process.write(self.process.base,commands.RESULT.pack(*fields)+reason)
        self.client.v3_world_status.return_value={"status":"ok","request_id":7,"completed_request_id":7}
        answer=owned.complete()
        self.assertEqual(answer.validated,0)
        self.assertEqual(answer.reason,"不能在这个地区建造")
        self.assertEqual(answer.data,reason)
        self.assertFalse(answer.reason_truncated)
        self.assertEqual(owned.command,0x55555)
        self.assertEqual(self.process.freed,[self.process.base])
        self.client.queue_v3_interface_callback.assert_called_once()

    def test_reason_truncation_and_nullable_validation_never_retry(self):
        fields=commands.RESULT.pack(88,122,5,12490,0x55555,0,5,9,0,4,0,0,3,0,1,0x88888,2)
        data="不能建".encode("utf-8")[:5]
        result=commands.CommandResult.from_bytes(fields+data)
        self.assertEqual(result.error,122)
        self.assertTrue(result.reason_truncated)
        self.assertEqual(result.buffer_size,9)
        self.assertEqual(result.data,data)
        self.assertEqual(result.reason,"不�")
        with self.assertRaises(ValueError): commands.CommandResult.from_bytes(fields+data[:-1])
        handle=commands.request(self.client,commands.Action.VALIDATE,command=0x55555,process=self.process)
        nullable=commands.RESULT.unpack(self.process.read(handle.result_address,88))
        self.assertEqual(nullable[5:7],(0,0))
        self.client.queue_v3_interface_callback.assert_called_once()

    def test_native_clone_returns_a_distinct_owner_without_consuming_source(self):
        result=commands.CommandResult(88,0,2,12490,0x55555,0,0,0,0,4,0,0,3,0,1,0x88888,2)
        original=commands.NativeCommand.from_result(self.client,result,process=self.process)
        handle=original.clone()
        fields=list(commands.RESULT.unpack(self.process.read(handle.result_address,88)))
        self.assertEqual(fields[2],commands.Action.CLONE)
        self.assertEqual(fields[6],8)
        self.assertIsNone(original.complete())
        fields[1],fields[3],fields[7]=0,12490,8
        self.process.write(handle.result_address,commands.RESULT.pack(*fields)+struct.pack("<Q",0x66666))
        self.client.v3_world_status.return_value={"status":"ok","request_id":7,"completed_request_id":7}
        cloned_result=original.complete()
        copy=commands.NativeCommand.from_result(self.client,cloned_result,process=self.process)
        self.assertEqual(original.command,0x55555)
        self.assertEqual(copy.command,0x66666)
        self.assertEqual(copy.world,original.world)
        self.assertEqual(original.attempt.outcome,"owned")
        self.assertEqual(copy.type_token,12490)
        self.assertEqual(cloned_result.data,struct.pack("<Q",0x66666))
        self.client.queue_v3_interface_callback.assert_called_once()
        self.assertEqual(self.process.freed,[self.process.base])
        failed=list(commands.RESULT.unpack(self.process.read(self.process.base,88)))
        failed[1]=574
        raw=commands.CommandResult.from_bytes(commands.RESULT.pack(*failed)+struct.pack("<Q",0x66666))
        self.assertEqual(raw.cloned_command,0x66666)
        with self.assertRaises(ValueError): commands.NativeCommand.from_result(self.client,raw)

    def test_dynamic_token_lookup_and_name_copy_use_public_owned_wire(self):
        query=commands.resolve_token(self.client,"Native动态",process=self.process)
        fields=list(commands.RESULT.unpack(self.process.read(query.result_address,88)))
        self.assertEqual(fields[2],commands.Action.RESOLVE_TOKEN)
        self.assertEqual(fields[3],0xffffffff)
        self.assertEqual(self.process.read(fields[5],fields[7]),"Native动态".encode("utf-8"))
        self.assertIsNone(query.read())
        fields[1],fields[3]=0,12490
        self.process.write(query.result_address,commands.RESULT.pack(*fields))
        self.client.v3_world_status.return_value={"status":"ok","request_id":7,"completed_request_id":7}
        self.assertEqual(query.read().type_token,12490)
        self.client.queue_v3_interface_callback.assert_called_once()
        copied=commands.RESULT.pack(88,0,commands.Action.TOKEN_NAME,12490,0,1,32,18,0,4,0,0,3,0,1,2,3)
        answer=commands.CommandResult.from_bytes(copied+b"queue_construction"+bytes(14))
        self.assertEqual(answer.token_name,"queue_construction")
        self.assertEqual(answer.data,b"queue_construction")
        self.assertFalse(answer.token_name_truncated)
        short=list(commands.RESULT.unpack(copied)); short[1],short[6]=122,5
        truncated=commands.CommandResult.from_bytes(commands.RESULT.pack(*short)+b"queue")
        self.assertTrue(truncated.token_name_truncated)
        self.assertEqual(truncated.token_name,"queue")

    def test_native_serialized_parameters_and_named_tokens_reverse_losslessly(self):
        actual=bytes.fromhex("ca300100030040280100140001000000f30101000300db000100140000000000400201000c0000000000c60001000c00000000009c0601001400000000000400410001000300f82701000f0022006275696c64696e675f676f7665726e6d656e745f61646d696e697374726174696f6eb7010100140005000000db0001000300402801001400010000000400155901000c0001000000245701005b02585e0100460304000400")
        value=commands.decode_command(actual)
        self.assertEqual(commands.encode_command(value.key.value,value.value),actual)
        self.assertEqual(commands.encode_tree(commands.name_tokens(value,
            {12490:"queue_construction",439:"state"})),actual)
        named=commands.encode_command(12490,{commands.NamedToken("native_dynamic_key"):
            [commands.NamedToken("native_dynamic_value"),commands.UInt32(0xffffffff)]})
        decoded=commands.decode_command(named)
        self.assertEqual(commands.encode_command(decoded.key.value,decoded.value),named)

    def test_readable_native_tree_preserves_duplicate_fields_widths_and_wire(self):
        body=(commands.Assignment(commands.Token(439),commands.UInt32(5)),
              commands.Assignment(commands.Token(439),commands.UInt32(6)),
              commands.Assignment(commands.Token(65),(
                commands.Assignment(commands.Token(22308),commands.Token(603)),
                commands.Assignment(commands.NamedToken("native_dynamic_key"),
                    commands.UInt64(0xffffffffffffffff)))))
        wire=commands.encode_command(12490,body)
        tree=commands.decode_command(wire)
        self.assertEqual(commands.token_ids(tree),(12490,439,65,22308,603))
        named=commands.name_tokens(tree,{12490:"queue_construction",439:"state",603:"native_enum"})
        self.assertEqual(named.key,commands.ResolvedToken(12490,"queue_construction"))
        self.assertEqual(named.value[0].key,commands.ResolvedToken(439,"state"))
        self.assertEqual(named.value[1].key,named.value[0].key)
        self.assertEqual(named.value[2].key,commands.Token(65))
        self.assertEqual(commands.encode_tree(named),wire)
        self.assertEqual(commands.token_ids(named),commands.token_ids(tree))
        self.assertEqual(tree.value[0].key,commands.Token(439))

    def test_production_method_native_full_id_vector_and_reader_field_order(self):
        # Native CReader consumes the outer vector '{' before ReadField117a610;
        # C5EA10 then reads each naked marker20 element as numeric12, not key11.
        # Key11='id' is only for an individual nested reference block.
        fields={10232:[commands.UInt32(87),commands.UInt32(0x01000057)],
                21409:"building_government_administration",13209:"pm_simple_organization"}
        wire=commands.encode_command(13208,{10304:commands.UInt32(1),65:fields})
        tree=commands.decode_command(wire)
        actor,domain=tree.value
        self.assertEqual(actor,commands.Assignment(commands.Token(10304),commands.UInt32(1)))
        self.assertEqual(domain.value[0],commands.Assignment(commands.Token(10232),
            (commands.UInt32(87),commands.UInt32(0x01000057))))
        self.assertEqual([item.key for item in domain.value],
            [commands.Token(10232),commands.Token(21409),commands.Token(13209)])
        self.assertEqual(commands.encode_tree(tree),wire)
        native_named=commands.name_tokens(tree,{13208:"activate_production_method_command",
            10232:"building",21409:"building_type",13209:"production_method"})
        self.assertEqual(commands.encode_tree(native_named),wire)

    def test_country_law_instance_reference_is_uint32_not_definition_key(self):
        for token in (22275,22276):
            fields={11018:commands.UInt32(0x01001234)}
            wire=commands.encode_command(token,{10304:commands.UInt32(1),65:fields})
            tree=commands.decode_command(wire)
            self.assertEqual(tree.key,commands.Token(token))
            self.assertEqual(tree.value[1].value,
                (commands.Assignment(commands.Token(11018),commands.UInt32(0x01001234)),))
            self.assertEqual(commands.encode_tree(tree),wire)
            named=commands.name_tokens(tree,{token:"country_law_command",11018:"law"})
            self.assertEqual(commands.encode_tree(named),wire)
            definition_key=commands.encode_command(token,{10304:commands.UInt32(1),
                65:{11018:"law_definition_key_is_not_an_instance_id"}})
            self.assertNotEqual(wire,definition_key)

    def test_live_registration_decode_and_native_size_check(self):
        name=b"queue_construction"+bytes(110)
        data=commands.RESULT.pack(88,0,1,0,0,1,144,0,1,4,0,0,9,0,1,2,3)+commands.REGISTRATION.pack(12490,4490,0xabcdef,name)
        result=commands.CommandResult.from_bytes(data)
        self.assertEqual(result.registrations,(commands.Registration(12490,4490,0xabcdef,"queue_construction"),))
        with self.assertRaises(ValueError): commands.CommandResult.from_bytes(data[:-1])

if __name__=="__main__": unittest.main()
