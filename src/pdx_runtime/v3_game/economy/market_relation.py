"""Copy the native Country-to-Market relation within one owned callback."""
from __future__ import annotations
from pdx_runtime.v3_wire import resolve_v3_export
import struct
from pdx_runtime import v3_data as data, native_call, v3_access

# Offsets of the existing368B PdxV3DataRequest, not game object addresses.
OBJECT_OFFSET = 56
HANDLE_OFFSET = 64
POINTER_METADATA_OFFSET = 48
RAW_BITS_AND_ANY_OFFSET = 128


def _allocate(state, size, initial=None):
    address=state["process"].allocate(size)
    state["owned_buffers"][address]=size
    state["process"].write(address,bytes(size) if initial is None else initial)
    return address


def compose(*, request, scratch, request_bytes, evaluate, release, runtime_read):
    """Return existing NativeCall batch bytes and address patcher, no dispatch.

    After the native producer every pointer use is SafeRPM; Release receives a
    pointer to its own original handle field and validates zero handles itself.
    No partial POD copy is fed to a game object consumer. The native Market ctor
    1CD6DC0/1CD6DF3 proves wrapper+10 is the complete uint32 Market ID.
    """
    fields=data.REQUEST.unpack_from(request_bytes);snapshot=fields[24]
    count=12
    labels=("evaluate_original_country_market","patch_local_object_source",
        "copy_local_market_prefix","copy_market_full_id","copy_original_value_handle",
        "release_original_value_node","copy_handle_after_release",
        "scrub_pointer_metadata","scrub_raw_any_bits","scrub_pointer_snapshot",
        "scrub_prefix","scrub_dynamic_source")
    def prepare(address):
        def read(index,src,dst,size):return(runtime_read,(src,dst,size,scratch+64+4*index))
        calls=((evaluate,(request,)),read(1,request+OBJECT_OFFSET,0,8),
            read(2,0,scratch,24),read(3,scratch+16,scratch+24,4),
            read(4,request+HANDLE_OFFSET,scratch+32,8),
            (release,(request+HANDLE_OFFSET,)),read(6,request+HANDLE_OFFSET,scratch+40,8),
            read(7,scratch+128,request+POINTER_METADATA_OFFSET,48),
            read(8,scratch+128,request+RAW_BITS_AND_ANY_OFFSET,48),
            read(9,scratch+128,request+snapshot,8),
            read(10,scratch+128,scratch,24),read(11,scratch+128,0,8))
        payload=bytearray(native_call.encode_batch(calls,request_address=address))
        args=[struct.unpack_from("<Q",payload,32+i*56+24)[0] for i in range(count)]
        struct.pack_into("<Q",payload,args[1]-address+8,args[2])
        struct.pack_into("<Q",payload,args[11]-address+8,args[2])
        return bytes(payload)
    return prepare(0),prepare,labels


async def observe_country_market(client,process,*,country_type_id,country_id,world,
                                 adapter,await_result,record=None):
    if record is None:record={}
    if "state" in record:
        return await continue_country_market(record["state"],await_result=await_result,record=record)
    if client.pid!=process.pid or type(country_id) is not int or not 0<=country_id<0xffffffff:
        raise ValueError("same-process genuine complete Country ID required")
    state={"client":client,"process":process,"country_type_id":country_type_id,
        "country_id":country_id,"world":world,"adapter":adapter,"owned_buffers":{},
        "query":None,"pending":None,"result":None,"complete":False,"summary":None}
    record["state"]=state
    try:
        binding=data.ObjectBinding.country(country_type_id,country_id)
        wire=data._request(data.Action.EVALUATE,expression=b"Country.GetMarket",
                           bindings=binding.pack(),output_bytes=8)
        state["request_bytes"]=wire
        state["request"]=_allocate(state,len(wire),wire)
        state["scratch"]=_allocate(state,192)
        evaluate=resolve_v3_export(process, adapter,"PdxV3DataEvaluate")
        release=resolve_v3_export(process, adapter,"PdxV3DataRelease")
        runtime_read=resolve_v3_export(process, adapter,"PdxRuntimeRead")
        callback=resolve_v3_export(process, adapter,"PdxNativeCallBatchInterface")
        initial,prepare,labels=compose(request=state["request"],scratch=state["scratch"],
            request_bytes=wire,evaluate=evaluate,release=release,runtime_read=runtime_read)
        state["labels"]=labels
        state["submission_started"]=True
        try:
            state["query"]=v3_access.submit_callback(client,callback,initial,mode=1,flag=0,
                dispatch="interface",process=process,adapter=adapter,call_adapter=adapter,
                prepare_payload=prepare,decode_payload=native_call.NativeCallBatchResult.from_bytes)
        except BaseException as error:
            state["query"]=getattr(error,"query",None);state["submission_error"]=error;raise
        state["pending"]=state["query"]
        return await continue_country_market(state,await_result=await_result,record=record)
    except BaseException as error:
        error.query_state=state;raise


def _finish_known_cleanup(state,record):
    # Summary was copied before frees. Resume only remaining frees after a
    # known cleanup interruption, never re-read an already freed request.
    summary=state["summary"]
    if summary["ownership_settled"]:
        for address in tuple(state["owned_buffers"]):
            state["process"].free(address)
            state["owned_buffers"].pop(address)
    state["complete"]=True
    if record is not None:record["summary"]=summary
    return summary


async def continue_country_market(state,*,await_result,record=None):
    if state["complete"]:return state["summary"]
    if state["summary"] is not None:
        try:return _finish_known_cleanup(state,record)
        except BaseException as error:error.query_state=state;raise
    query=state["query"]
    if query is None:
        raise RuntimeError("retain original relation submission state; no unidentified replay")
    try:
        result=await await_result("economy.original_country_market_same_access",query)
        if result is None:
            return {"status":"waiting_original_query","market_id":None,
                "source":{"country_id":state["country_id"],"world":state["world"]},
                "receipts":[],"owners_released":False}
        state["result"]=result;state["pending"]=None
        batch=result.callee_result
        if (result.error or not result.entered or not result.left or batch is None
            or batch.error or batch.completed!=12 or len(batch.calls)!=12
            or any(call.error for call in batch.calls)):
            raise RuntimeError("original same-Access relation prefix unresolved; keep its storage")
        raw=state["process"].read(state["request"],len(state["request_bytes"]))
        scratch=state["process"].read(state["scratch"],192)
        evaluated=data.DataResult.from_bytes(raw)
        received=struct.unpack_from("<12I",scratch,64)
        before,after=struct.unpack_from("<2Q",scratch,32)
        market_id=struct.unpack_from("<I",scratch,24)[0]
        expected={1:8,2:24,3:4,4:8,6:8,7:48,8:48,9:8,10:24,11:8}
        receipts=[{"label":state["labels"][i],"adapter_error":call.error,
            "native_return":call.rax,"received":received[i] if i in expected else None,
            "requested":expected.get(i)} for i,call in enumerate(batch.calls)]
        copies=all(batch.calls[i].rax&0xffffffff==0 and received[i]==n for i,n in expected.items())
        release_error=batch.calls[5].rax&0xffffffff
        # Original Release success itself confirms consumption/clearing. A
        # failed observational handle-copy is not a reason to strand ownership.
        owners_released=release_error==0
        name=evaluated.native_type_name
        typed=bool(evaluated.kind==data.Kind.OBJECT and evaluated.payload_size==8
                   and "CDataMarket" in name)
        ok=bool(copies and batch.calls[0].rax&0xffffffff==0 and not evaluated.error
                and typed and owners_released and market_id!=0xffffffff)
        summary={"status":"observed" if ok else "relation_unavailable_receipts_retained",
            "market_id":market_id if ok else None,"source":{"expression":"Country.GetMarket",
                "country_id":state["country_id"],"world":state["world"],
                "receiver_type_id":state["country_type_id"],"returned_type_id":evaluated.type_id,
                "native_type_name":name,"request_id":getattr(query,"request_id",None),
                "execution_thread_id":result.execution_thread_id},
            "evaluate_error":evaluated.error,"evaluate_return":batch.calls[0].rax&0xffffffff,
            "release_error":release_error,"original_value_handle_observation":before,
            "handle_after_release":after,"owners_released":owners_released,
            "prefix_copied":copies,"receipts":receipts,"raw_request":raw,
            "private_pointer_storage_scrubbed":not any(scratch[:24]) and
                not any(raw[48:96]) and not any(raw[128:176]),
            "original_evaluation_receipt_after_private_scratch_scrub":evaluated,
            "scope_result":result}
        # A known Eval failure with proven zero handles has no native owner.
        # Do not mislabel this settled condition as a live owner hold.
        handle_reads_ok=all(batch.calls[i].rax&0xffffffff==0 and received[i]==8 for i in (4,6))
        no_owner=handle_reads_ok and before==after==0 and bool(evaluated.error or batch.calls[0].rax&0xffffffff)
        summary.update(known_no_owner=bool(no_owner),native_release_consumed=owners_released,
            ownership_settled=bool(owners_released or no_owner),
            owners_released=bool(owners_released or no_owner))
        state["summary"]=summary
        return _finish_known_cleanup(state,record)
    except BaseException as error:
        state["pending"]=query if state["result"] is None else None
        error.query_state=state;raise
