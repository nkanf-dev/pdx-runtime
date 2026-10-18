from __future__ import annotations
import unittest
import struct
from unittest.mock import Mock
from pdx_runtime import v3_console as console

class ConsoleTests(unittest.TestCase):
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
        self.interface_callback.return_value={"status":"ok", "error": 0,"request_id":9}
        self.process=Mock(pid=123)
        self.process.resolve_export.return_value=0x10000
        self.process.allocate.return_value=0x20000

    def test_execute_retains_input_until_completion(self):
        query=console.execute(self.client,"help",output_capacity=32,process=self.process)
        self.assertEqual(console.REQUEST.size,96)
        blob=self.process.write.call_args.args[1]
        self.assertEqual(blob[96:100],b"help")
        self.world_status.return_value={"status":"ok","completed_request_id":8}
        self.assertIsNone(query.read())
        self.process.free.assert_not_called()
        header=console.REQUEST.pack(132,0,1,0,4,32,15,15,0,0,1,0,7372,0,
                                   0x10,0x20,0,0,1,0)
        self.world_status.return_value={"status":"ok","completed_request_id":9}
        self.process.read.return_value=header+b"help"+b"Unknown command"+bytes(17)
        result=query.read()
        self.assertEqual(result.message,"Unknown command")
        self.assertEqual(result.native_success,0)
        self.assertIs(query.read(),result)
        self.process.free.assert_called_once_with(0x20000)
        self.interface_callback.assert_called_once()

    def test_command_metadata_exact_callback_and_context(self):
        strings=[b"help",b"list commands",b"h",b"name"]
        payload=b"".join(console.U32.pack(len(s))+s for s in strings)
        record=console.RECORD.pack(48+len(payload),1,1,1,1,0,0x100,0x200,0x300)+payload
        header=console.REQUEST.pack(96+len(record),0,2,0,0,len(record),len(record),len(record),
                                   1,1,1,0,7372,0,0x10,0x20,0,0,0,len(record))
        result=console.ConsoleResult.from_bytes(header+record)
        command=result.commands[0]
        self.assertEqual(command.name,"help")
        self.assertEqual(command.aliases,("h",))
        self.assertEqual(command.parameters,("name",))
        self.assertEqual((command.object,command.callback,command.context),(0x100,0x200,0x300))

    def test_truncated_execution_is_never_replayed(self):
        query=console.execute(self.client,"mutation",output_capacity=2,process=self.process)
        self.world_status.return_value={"status":"ok","completed_request_id":9}
        self.process.read.return_value=console.REQUEST.pack(106,234,1,1,8,2,2,100,
            0,0,0,0,7372,0,0x10,0x20,0,0,1,0)+b"mutation"+b"ok"
        result=query.read()
        self.assertFalse(result.complete_output)
        self.assertEqual(result.output_required,100)
        self.assertEqual(result.native_success,1)
        self.interface_callback.assert_called_once()

    def test_parser_return_and_exact_primitive(self):
        parts=[b"one",b"",b"two"]
        payload=b"".join(console.U32.pack(len(s))+s for s in parts)
        result=console.ConsoleResult.from_bytes(console.REQUEST.pack(96+len(payload),0,3,0,
            0,len(payload),len(payload),len(payload),0,0,0,0,1,0,0,0,0,0,3,0)+payload)
        self.assertEqual(result.arguments,("one","","two"))
        query=console.invoke_exact(self.client,0x500,0x600,"two",output_capacity=0,process=self.process)
        values=console.REQUEST.unpack_from(self.process.write.call_args.args[1])
        self.assertEqual(values[2],4)
        self.assertEqual(values[16:18],(0x500,0x600))

    def test_explicit_arguments_preserve_boundaries_and_callback_lifetime(self):
        arguments=["native_probe", 'two words "quoted"', "", "中文"]
        query=console.execute_arguments(self.client,arguments,output_capacity=8,process=self.process)
        blob=self.process.write.call_args.args[1]
        fields=console.REQUEST.unpack_from(blob)
        self.assertEqual(fields[2],8)
        self.assertEqual(console.U32.unpack_from(blob,96)[0],len(arguments))
        cursor=100
        for argument in arguments:
            length=console.U32.unpack_from(blob,cursor)[0]
            cursor+=4
            self.assertEqual(blob[cursor:cursor+length],argument.encode("utf-8"))
            cursor+=length
        self.assertEqual(cursor,96+fields[4])
        self.world_status.return_value={"status":"ok","completed_request_id":8}
        self.assertIsNone(query.read())
        self.process.free.assert_not_called()
        result_fields=list(fields)
        result_fields[1]=234;result_fields[3]=1
        result_fields[6]=8;result_fields[7]=12;result_fields[18]=len(arguments)
        self.process.read.return_value=console.REQUEST.pack(*result_fields)+blob[96:cursor]+b"complete"
        self.world_status.return_value={"status":"ok","completed_request_id":9}
        result=query.read()
        self.assertEqual(result.argument_count,4)
        self.assertEqual(result.message,"complete")
        self.assertFalse(result.complete_output)
        self.assertEqual(result.native_success,1)
        self.process.free.assert_called_once_with(0x20000)
        self.interface_callback.assert_called_once()

    def test_explicit_arguments_reject_missing_name_or_nul_before_queue(self):
        for arguments in ([],[""],["probe","bad\0input"]):
            with self.assertRaises(ValueError):
                console.execute_arguments(self.client,arguments,process=self.process)
        with self.assertRaises(TypeError):
            console.execute_arguments(self.client,"probe one",process=self.process)
        self.interface_callback.assert_not_called()
        self.process.allocate.assert_not_called()

    def test_registration_copies_native_descriptor_wire(self):
        console.register_command(self.client, "abc", 0x500, 0x600,
            description="demo", aliases=("a",), parameters=("count",),
            developer_allowed=True, process=self.process)
        blob=self.process.write.call_args.args[1]
        values=console.REQUEST.unpack_from(blob)
        self.assertEqual(values[2],6)
        self.assertEqual(values[16:18],(0x500,0x600))
        self.assertEqual(struct.unpack_from("<3I",blob,96),(1,1,1))
        self.assertIn(b"abc",blob[108:])
        console.unregister_command(self.client,"abc",process=self.process)
        self.assertEqual(console.REQUEST.unpack_from(self.process.write.call_args.args[1])[2],7)
        with self.assertRaises(ValueError):
            console.register_command(self.client,"a",0x500,aliases=("a","b","c","d"),process=self.process)

if __name__=="__main__": unittest.main()
