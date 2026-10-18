"""Owned sequence wire/carrier tests. No game is attached.
SPDX-License-Identifier: MPL-2.0
"""
import unittest
from unittest.mock import Mock, patch
from pdx_runtime import v3_data as data, v3_access as access

class ActionSequenceTests(unittest.TestCase):
    def setUp(self):
        self.client=Mock(pid=1)
        self.client.request.side_effect=lambda c: ({"status":"ok","request_id":7,"error":0}
            if c.startswith("interface-callback ") else {"status":"ok","completed_request_id":7})
        self.process=Mock(pid=1)
        self.process.resolve_export.side_effect=lambda adapter,symbol,**kwargs: (0xA100 if symbol=="PdxV3DataInvokeActionSequenceInterface" else 0xB100)
        self.process.export_owner.return_value=0xA000
        self.process.allocate.return_value=0x2000
        self.steps=(data.ActionStep("GetGameRules.SetPresetName",data.TypedInput.string("new name"),data.ActionContinuation.INVOKED_AND_ACCEPTED,output_bytes=16),
                    data.ActionStep("GetGameRules.SavePreset",data.TypedInput.empty(),data.ActionContinuation.ALWAYS,output_bytes=16))

    def submit(self):
        query=data.invoke_action_sequence(self.client,self.steps,process=self.process)
        self.raw=bytearray(self.process.write.call_args.args[1])
        return query

    def returned(self, completed=2, stopped=0, first_error=0, scope_error=0):
        h=list(access.REQUEST.unpack_from(self.raw));h[1]=scope_error;h[12]=h[13]=1
        access.REQUEST.pack_into(self.raw,0,*h)
        off=access.PAYLOAD_OFFSET;s=list(data.ACTION_SEQUENCE.unpack_from(self.raw,off))
        s[1]=first_error;s[4]=completed;s[5]=stopped
        data.ACTION_SEQUENCE.pack_into(self.raw,off,*s)
        for index in range(completed):
            pointer,_=data.ACTION_SEQUENCE_STEP.unpack_from(self.raw,off+s[3]+index*8)
            at=off+pointer;r=list(data.REQUEST.unpack_from(self.raw,at));r[1]=first_error if index==0 else 0
            r[14]=100+index;r[11]=data.Kind.STRING if index==0 else data.Kind.EMPTY
            if index==0:r[9]=5;r[19]=2;self.raw[at+r[24]:at+r[24]+2]=b"ab"
            data.REQUEST.pack_into(self.raw,at,*r)
            a=list(data.ACTION_SUFFIX.unpack_from(self.raw,at+data.REQUEST.size));a[2:5]=[4,1,1]
            data.ACTION_SUFFIX.pack_into(self.raw,at+data.REQUEST.size,*a)
        self.process.read.return_value=bytes(self.raw)

    def test_unique_encoder_matches_ordinary_action_packet(self):
        data.invoke_action(self.client,self.steps[0].expression,self.steps[0].input,output_bytes=16,process=self.process)
        old=self.process.write.call_args.args[1]
        encoded=data.encode_action_request(self.steps[0].expression,self.steps[0].input,output_bytes=16)
        self.assertEqual(old,encoded)
        sequence=data.encode_action_sequence(self.steps)
        pointer,policy=data.ACTION_SEQUENCE_STEP.unpack_from(sequence,data.ACTION_SEQUENCE.size)
        self.assertEqual(policy,1);self.assertEqual(sequence[pointer:pointer+len(encoded)],encoded)

    def test_one_original_access_allocation_and_actual_data_adapter(self):
        query=self.submit()
        self.assertEqual(query.data_adapter,0xA000)
        header=access.REQUEST.unpack_from(self.raw)
        self.assertEqual((header[2],header[3],header[4]),(1,0,0xA100))
        self.assertEqual(self.process.allocate.call_count,1)
        self.assertEqual(sum(c.args[0].startswith("interface-callback ") for c in self.client.request.call_args_list),1)
        self.assertEqual(query.callee_address,query.result_address+access.PAYLOAD_OFFSET)

    def test_pending_keeps_original_allocation_without_replay(self):
        query=self.submit();self.client.request.side_effect=lambda _: {"status":"ok","completed_request_id":6}
        self.assertIsNone(query.read());self.process.free.assert_not_called()
        self.assertEqual(self.process.allocate.call_count,1)

    def test_accepted_postcopy_error_and_handles_remain_original(self):
        query=self.submit();self.returned(first_error=234)
        result=query.read();sequence=result.callee_result
        self.assertEqual((result.error,sequence.error,sequence.completed,sequence.stopped),(0,234,2,False))
        first=sequence.native_results[0]
        self.assertEqual((first.invoked,first.accepted,first.result.error,first.value_handle),(1,1,234,100))
        self.assertEqual((first.result.snapshot,first.result.total),(b"ab",5))
        self.assertEqual(sequence.native_results[1].value_handle,101)
        self.assertEqual(self.process.free.call_count,1) # only callback storage

    def test_rejected_prefix_keeps_unexecuted_pending_suffix(self):
        query=self.submit();self.returned(completed=1,stopped=1)
        off=access.PAYLOAD_OFFSET;pointer,_=data.ACTION_SEQUENCE_STEP.unpack_from(self.raw,off+32)
        at=off+pointer+data.REQUEST.size;a=list(data.ACTION_SUFFIX.unpack_from(self.raw,at));a[4]=0
        data.ACTION_SUFFIX.pack_into(self.raw,at,*a);self.process.read.return_value=bytes(self.raw)
        sequence=query.read().callee_result
        self.assertEqual((sequence.completed,sequence.stopped),(1,True))
        self.assertEqual((sequence.native_results[1].result.error,sequence.native_results[1].value_handle),(997,0))
        self.assertEqual(sequence.native_results[0].accepted,0)

    def test_fault_keeps_allocation_and_current_handle_beyond_completed(self):
        query=self.submit();self.returned(completed=2,first_error=997,scope_error=574)
        off=access.PAYLOAD_OFFSET;s=list(data.ACTION_SEQUENCE.unpack_from(self.raw,off));s[4]=1
        data.ACTION_SEQUENCE.pack_into(self.raw,off,*s);self.process.read.return_value=bytes(self.raw)
        with self.assertRaises(data.ActionSequenceReadError) as caught:query.read()
        error=caught.exception;self.assertIs(error.query,query)
        self.assertEqual(error.partial_result.callee_result.native_results[1].value_handle,101)
        self.assertEqual(error.partial_result.callee_result.completed,1)
        self.process.free.assert_not_called();self.assertEqual(self.process.allocate.call_count,1)

    def test_observed_callback_status_survives_pending_payload_without_relabel(self):
        query=self.submit();self.returned(completed=1,first_error=997)
        status={"status":"ok","request_id":7,"completed_request_id":7,"callback_error":574}
        self.client.request.side_effect=lambda _:status
        before=self.client.request.call_count
        with self.assertRaises(data.ActionSequenceReadError) as caught:query.read()
        error=caught.exception
        self.assertEqual(query.completion_status,status)
        self.assertIs(error.completion_status,query.completion_status)
        self.assertEqual((error.partial_result.error,error.partial_result.callee_result.error),(0,997))
        self.assertEqual(error.partial_result.callee_result.native_results[0].value_handle,100)
        self.assertEqual(self.client.request.call_count,before+1)
        self.process.free.assert_not_called()

    def test_later_status_remains_raw_without_attributing_its_error(self):
        query=self.submit();self.returned(completed=1,first_error=997)
        status={"status":"ok","request_id":9,"completed_request_id":9,"callback_error":574}
        self.client.request.side_effect=lambda _:status
        with self.assertRaises(data.ActionSequenceReadError) as caught:query.read()
        error=caught.exception
        self.assertEqual(error.completion_status,status)
        self.assertIs(error.completion_status,query.completion_status)
        self.assertEqual(error.partial_result.callee_result.error,997)
        self.assertEqual(error.query.request_id,7)
        self.process.free.assert_not_called()

    def test_undecodable_copy_preserves_original_exception_status_and_bytes(self):
        query=self.submit()
        status={"status":"ok","request_id":7,"completed_request_id":7,"callback_error":574}
        self.client.request.side_effect=lambda _:status
        copied=b"\x01\x02"
        self.process.read.return_value=copied
        with self.assertRaises(Exception) as caught:query.read()
        error=caught.exception
        self.assertNotIsInstance(error,data.ActionSequenceReadError)
        self.assertIs(error.query,query)
        self.assertEqual(error.completion_status,status)
        self.assertIs(error.completion_status,query.completion_status)
        self.assertEqual(error.copied_bytes,copied)
        self.process.free.assert_not_called()

    def assert_later_decode_failure_keeps_prefix(self, defect):
        query=self.submit();self.returned(completed=2,first_error=234,scope_error=574)
        status={"status":"ok","request_id":7,"completed_request_id":7,"callback_error":574}
        self.client.request.side_effect=lambda _:status
        off=access.PAYLOAD_OFFSET
        header=list(data.ACTION_SEQUENCE.unpack_from(self.raw,off));header[4]=0
        data.ACTION_SEQUENCE.pack_into(self.raw,off,*header)
        descriptor=off+header[3]+data.ACTION_SEQUENCE_STEP.size
        second,policy=data.ACTION_SEQUENCE_STEP.unpack_from(self.raw,descriptor)
        at=off+second
        if defect=="descriptor":data.ACTION_SEQUENCE_STEP.pack_into(self.raw,descriptor,0,policy)
        elif defect=="request_size":
            import struct
            struct.pack_into("<I",self.raw,at,data.ACTION_HEADER_SIZE-1)
        else:
            fields=list(data.REQUEST.unpack_from(self.raw,at))
            if defect=="action":fields[2]=data.Action.SNAPSHOT
            else:fields[19]=1;fields[24]=fields[0]+8
            data.REQUEST.pack_into(self.raw,at,*fields)
        self.process.read.return_value=bytes(self.raw)
        before=self.client.request.call_count
        with self.assertRaises(ValueError) as caught:query.read()
        error=caught.exception
        self.assertTrue(hasattr(error,"partial_result"),"decoded native prefix must be published on original error")
        self.assertIs(error.query,query);self.assertIs(error.partial_result,query.partial_result)
        self.assertIs(error.completion_status,query.completion_status)
        self.assertEqual(error.copied_bytes,bytes(self.raw))
        partial=error.partial_result
        self.assertEqual((partial.error,partial.callee_result.error,partial.callee_result.completed),(574,234,0))
        self.assertIs(error.partial_sequence,partial.callee_result)
        self.assertEqual(len(partial.callee_result.native_results),1)
        first=partial.callee_result.native_results[0]
        self.assertEqual((first.value_handle,first.error,first.invoked,first.accepted),(100,234,1,1))
        self.assertFalse(partial.callee_result.complete)
        self.assertEqual(self.client.request.call_count,before+1)
        self.assertEqual(self.process.read.call_count,1)
        self.process.free.assert_not_called()
        with self.assertRaises(ValueError) as direct:
            data.ActionSequenceResult.from_bytes(bytes(self.raw[off:off+header[0]]))
        self.assertEqual(direct.exception.partial_sequence.native_results,(first,))
        self.assertEqual(str(direct.exception),str(error))

    def test_later_invalid_descriptor_keeps_decoded_prefix(self):
        self.assert_later_decode_failure_keeps_prefix("descriptor")

    def test_later_invalid_request_size_keeps_decoded_prefix(self):
        self.assert_later_decode_failure_keeps_prefix("request_size")

    def test_later_invalid_data_result_keeps_decoded_prefix(self):
        self.assert_later_decode_failure_keeps_prefix("data_result")

    def test_later_wrong_action_keeps_decoded_prefix(self):
        self.assert_later_decode_failure_keeps_prefix("action")

    def test_later_decoder_exception_keeps_exact_exception_and_prefix(self):
        query=self.submit();self.returned()
        off=access.PAYLOAD_OFFSET
        first_offset,_=data.ACTION_SEQUENCE_STEP.unpack_from(self.raw,off+data.ACTION_SEQUENCE.size)
        first=data.ActionResult.from_bytes(bytes(self.raw[off+first_offset:]))
        fault=ValueError("original second action decode fault")
        with patch.object(data.ActionResult,"from_bytes",side_effect=(first,fault)) as decode:
            with self.assertRaises(ValueError) as caught:query.read()
        self.assertIs(caught.exception,fault)
        self.assertIs(fault.partial_result,query.partial_result)
        self.assertEqual(fault.partial_sequence.native_results,(first,))
        self.assertEqual(decode.call_count,2)
        self.process.free.assert_not_called()

    def test_partial_copy_keeps_copied_header_handle_and_original_query(self):
        query=self.submit();self.returned()
        off=access.PAYLOAD_OFFSET;second,_=data.ACTION_SEQUENCE_STEP.unpack_from(self.raw,off+40)
        self.process.read.return_value=bytes(self.raw[:off+second+data.ACTION_HEADER_SIZE])
        with self.assertRaises(data.ActionSequenceReadError) as caught:query.read()
        payload=caught.exception.partial_result.callee_result
        self.assertFalse(payload.complete);self.assertEqual(payload.native_results[1].value_handle,101)
        self.process.free.assert_not_called()

    def test_partial_postinput_snapshot_preserves_complete_action_header(self):
        query=self.submit();self.returned(first_error=234)
        off=access.PAYLOAD_OFFSET;first,_=data.ACTION_SEQUENCE_STEP.unpack_from(self.raw,off+32)
        at=off+first;header=data.REQUEST.unpack_from(self.raw,at)
        self.process.read.return_value=bytes(self.raw[:at+header[24]+1])
        with self.assertRaises(data.ActionSequenceReadError) as caught:query.read()
        payload=caught.exception.partial_result.callee_result
        first=payload.native_results[0]
        self.assertEqual((first.value_handle,first.invoked,first.accepted,first.error),(100,1,1,234))
        self.assertEqual((first.result.snapshot_bytes,first.result.snapshot),(2,b"a"))
        self.assertFalse(payload.complete);self.process.free.assert_not_called()

    def test_bad_policy_or_empty_sequence_never_allocates(self):
        with self.assertRaises(ValueError):data.invoke_action_sequence(self.client,(),process=self.process)
        bad=data.ActionStep("A",data.TypedInput.empty(),9)
        with self.assertRaises(ValueError):data.invoke_action_sequence(self.client,(bad,),process=self.process)
        self.process.allocate.assert_not_called()

if __name__=="__main__":unittest.main()
