"""Native ArticleType catalog as copied POD/UTF-8; no work on import.

Provider returns &slot. All later foreign reads are ReadProcessMemory in ONE
existing Access batch, with SIZE_T received cells. No unsafe keyview/DB consumer
follows a failed probe. Definition CString layout is the original7B37F0 layout.
"""
from __future__ import annotations
from pdx_runtime.v3_wire import resolve_v3_export
import struct
from ... import native_call, v3_access, v3_target_profiles
from ...target_descriptor import verify_loaded_target
from pdx_runtime.v3_game.diplomacy.treaty import proposal as mature

PROCESS_SELF = 0xFFFFFFFFFFFFFFFF
MAX_ORDINAL = 0x7FFFFFFF


def compose_article_type(*, read_process_memory, provider_address, layout, ordinal, key_capacity=0):
    """Return initial/prepare/decode for one fresh ordinal, using no7B37F0.

    cap0 is metadata-only for heap keys, but an SSO key is copied as eight
    uint16 scalars. This avoids returning a contiguous heap-pointer candidate in
    the inline-key POD region. A heap copy has a separate fresh full walk; it
    uses the caller's allocated capacity, never an untrusted dynamic write size.
    """
    if type(ordinal) is not int or not 0 <= ordinal <= MAX_ORDINAL:
        raise ValueError("ordinal must fit the original nonnegative signed32 ABI")
    if type(key_capacity) is not int or not 0 <= key_capacity <= 0x7FFFFFFF:
        raise ValueError("key_capacity must be a nonnegative owned-buffer size")
    if not read_process_memory or not provider_address:
        raise ValueError("explicit read export and provider address are required")
    # These are caller-explicit raw inputs. Only article_types' automatic path
    # selects a known exact build; the generic NativeCall SDK stays unrestricted.
    layout = dict(layout)
    required = {"database_header_size", "database_array", "database_capacity", "database_count",
        "definition_prefix_size", "definition_ordinal", "definition_kind", "definition_payer",
        "definition_key", "key_length", "key_capacity", "key_inline_limit"}
    if required - layout.keys() or any(type(layout[key]) is not int or layout[key] < 0 for key in required):
        raise ValueError("explicit nonnegative catalog layout fields are required")
    for key,size in (("database_array",8),("database_capacity",4),("database_count",4)):
        if layout[key]+size>layout["database_header_size"]:
            raise ValueError("catalog database field exceeds its copied prefix")
    for key,size in (("definition_ordinal",4),("definition_kind",1),("definition_payer",1),
                     ("definition_key",16),("key_length",8),("key_capacity",8)):
        if layout[key]+size>layout["definition_prefix_size"]:
            raise ValueError("catalog definition field exceeds its copied prefix")
    if layout["key_inline_limit"]>15:
        raise ValueError("inline key limit exceeds this explicit 16-byte POD copier")
    header_size = layout["database_header_size"]
    definition_size = layout["definition_prefix_size"]
    array_bytes = (ordinal + 1) * 8
    labels = ["provider", "slot_source", "slot", "db_source", "db_header",
        "array_source", "array_prefix", "definition_source", "definition_prefix"]
    if key_capacity: labels += ["heap_source", "heap_key"]
    labels += ["copy_count", "copy_db_capacity", "copy_ordinal", "copy_kind",
        "copy_payer", "copy_key_length", "copy_key_capacity"]
    labels += ["copy_inline_u16_" + str(i) for i in range(8)]
    scrubs = (("slot", 8), ("header", header_size), ("array", array_bytes), ("definition", definition_size))
    labels += ["scrub_" + name for name, _ in scrubs]
    source_calls = [2, 4, 6, 8] + ([10] if key_capacity else [])
    labels += ["scrub_source_" + str(i) for i in source_calls] + ["scrub_provider_rax"]
    count = len(labels)
    args_at = native_call.BATCH_REQUEST.size + count * native_call.REQUEST.size
    end = args_at + count * 40  # All requests carry five explicit Win64 words.
    offsets = {}
    def reserve(name, size):
        nonlocal end
        end = (end + 7) & ~7; offsets[name] = end; end += size
    for name, size in (("slot",8),("header",header_size),("array",array_bytes),("definition",definition_size),
        ("key",max(1,key_capacity)),("scalars",96),("received",count*8),
        ("zero",max(header_size,definition_size,array_bytes))): reserve(name,size)
    requested = [None,8,8,8,header_size,8,array_bytes,8,definition_size]
    if key_capacity: requested += [8,key_capacity]
    requested += [4,4,4,1,1,8,8] + [2]*8 + [size for _,size in scrubs] + [8]*(len(source_calls)+1)
    heap_index = 10 if key_capacity else None
    metadata_start = 11 if key_capacity else 9
    scrub_start = metadata_start + 15

    def prepare(address):
        def at(name,extra=0): return address + offsets[name] + extra
        def arg(index,pos=0): return address + args_at + index*40 + pos*8
        def rax(index): return address + native_call.BATCH_REQUEST.size + index*native_call.REQUEST.size + 32
        def rpm(index,source,dest,size):
            return read_process_memory,(PROCESS_SELF,source,dest,size,at("received",index*8))
        calls = [(provider_address,(0,0,0,0,0)),
            rpm(1,rax(0),arg(2,1),8),rpm(2,0,at("slot"),8),
            rpm(3,at("slot"),arg(4,1),8),rpm(4,0,at("header"),header_size),
            rpm(5,at("header",layout["database_array"]),arg(6,1),8),rpm(6,0,at("array"),array_bytes),
            rpm(7,at("array",ordinal*8),arg(8,1),8),rpm(8,0,at("definition"),definition_size)]
        if key_capacity:
            calls += [rpm(9,at("definition",layout["definition_key"]),arg(10,1),8),rpm(10,0,at("key"),key_capacity)]
        scalar_reads = (("header",layout["database_count"],0,4),("header",layout["database_capacity"],4,4),
            ("definition",layout["definition_ordinal"],8,4),("definition",layout["definition_kind"],12,1),("definition",layout["definition_payer"],13,1),
            ("definition",layout["key_length"],16,8),("definition",layout["key_capacity"],24,8))
        for name,source_offset,target_offset,size in scalar_reads:
            calls.append(rpm(len(calls),at(name,source_offset),at("scalars",target_offset),size))
        for i in range(8):
            calls.append(rpm(len(calls),at("definition",layout["definition_key"]+i*2),at("scalars",32+i*8),2))
        for name,size in scrubs: calls.append(rpm(len(calls),at("zero"),at(name),size))
        for index in source_calls: calls.append(rpm(len(calls),at("zero"),arg(index,1),8))
        calls.append(rpm(len(calls),at("zero"),rax(0),8))
        assert len(calls)==count
        raw=native_call.encode_batch(calls,request_address=address)
        return raw+bytes(end-len(raw))

    def decode(payload):
        batch=native_call.NativeCallBatchResult.from_bytes(payload)
        if len(payload)!=end or len(batch.calls)!=count:
            raise ValueError("incomplete original catalog packet")
        received=struct.unpack_from('<'+str(count)+'Q',payload,offsets['received'])
        settled=(batch.error==0 and batch.completed==count and all(call.error==0 for call in batch.calls))
        def ok(index):
            return settled and (batch.calls[index].rax&0xFFFFFFFF)!=0 and received[index]==requested[index]
        scrub_ranges=[(offsets[name],size) for name,size in scrubs]
        scrub_ranges += [(args_at+i*40+8,8) for i in source_calls]
        scrub_ranges += [(native_call.BATCH_REQUEST.size+32,8)]
        scrubbed=bool(settled and all(ok(i) for i in range(scrub_start,count))
            and all(not any(payload[o:o+n]) for o,n in scrub_ranges))
        total,capacity,found=struct.unpack_from('<iii',payload,offsets['scalars'])
        kind,payer=struct.unpack_from('<BB',payload,offsets['scalars']+12)
        length,string_capacity=struct.unpack_from('<QQ',payload,offsets['scalars']+16)
        header_ok=bool(scrubbed and all(ok(i) for i in (1,2,3,4,metadata_start,metadata_start+1))
            and 0<=total<=capacity)
        essential=[1,2,3,4,5,6,7,8]+list(range(metadata_start,scrub_start))
        valid=bool(header_ok and all(ok(i) for i in essential) and 0<=ordinal<total and found==ordinal
            and length<=string_capacity and (string_capacity>layout["key_inline_limit"] or length<=layout["key_inline_limit"]))
        full_key=False;key=None
        if valid and string_capacity<=layout["key_inline_limit"]:
            inline=b''.join(payload[offsets['scalars']+32+i*8:offsets['scalars']+34+i*8] for i in range(8))
            key=inline[:length].decode('utf-8','strict');full_key=True
        elif valid and key_capacity and length==key_capacity and ok(9) and ok(heap_index):
            key=bytes(payload[offsets['key']:offsets['key']+length]).decode('utf-8','strict');full_key=True
        return {'ordinal':ordinal,'count':total if header_ok else None,'native_capacity':capacity if header_ok else None,
            'header_copied':header_ok,'valid':valid,'key':key,'key_length':length if valid else None,
            'key_complete':full_key,'kind':'mutual' if valid and kind==1 else 'directed' if valid and kind==0 else None,
            'native_kind':kind if valid else None,'native_payer':payer if valid else None,
            'borrowed_storage_scrubbed':scrubbed,'key_copy_requested':bool(key_capacity),
            'receipts':[{'label':labels[i],'adapter_error':call.error,
                'read_succeeded':None if i==0 else bool(call.rax&0xFFFFFFFF),
                'received':received[i],'requested':requested[i]} for i,call in enumerate(batch.calls)]}
    decode.layout={'offsets':offsets,'args_at':args_at,'size':end,'labels':tuple(labels),
        'heap_index':heap_index,'count':count,'received_width':8,'read_api':'kernel32.ReadProcessMemory'}
    return prepare(0),prepare,decode


async def article_types(ctx,state):
    """Current all-type catalog, fresh complete walk per read; no native keys guess.

    SSO completes in the metadata callback. A heap key permits one fresh sizing
    callback using the observed length; a change/failure raises, never loops to
    reconstruct unknown work or pretends an empty/complete result.
    """
    if state['pending'] is not None:
        raise RuntimeError("earlier original query unresolved; do not reconstruct/replay")
    process=ctx.process
    target=verify_loaded_target(process,ctx.image_base,definitions=v3_target_profiles.KNOWN_TARGETS)
    rpm=process.resolve_export('kernel32.dll','ReadProcessMemory')
    handler=resolve_v3_export(process, ctx.adapters['call'],'PdxNativeCallBatchInterface')
    async def one(index,capacity):
        mature._world(state,'article_type_before')
        provider=target.function_address("article_type_provider",read=process.read,image_base=ctx.image_base)
        initial,prepare,decode=compose_article_type(read_process_memory=rpm,provider_address=provider,
            layout=target.definition.layout,ordinal=index,key_capacity=capacity)
        result=await mature.accept._operation(state,'native_article_type_'+str(index)+'_'+str(capacity),lambda:
            v3_access.submit_callback(ctx.native,handler,initial,mode=1,flag=0,dispatch='interface',
                adapter=ctx.adapters['access'],call_adapter=ctx.adapters['call'],process=process,
                prepare_payload=prepare,decode_payload=decode))
        if result.error or result.entered!=1 or result.left!=1:
            state['cleanup_uncertain']=True
            raise RuntimeError("original ArticleType Access callback unresolved/failed; retain query")
        mature._world(state,'article_type_after')
        return result.callee_result
    first=await one(0,0)
    if first['header_copied'] and first['count']==0:return []
    if not first['valid']:raise RuntimeError("original ArticleType database metadata could not be copied")
    total=first['count'];result=[]
    for index in range(total):
        row=first if index==0 else await one(index,0)
        if not row['valid'] or row['count']!=total:
            raise RuntimeError("original ArticleType database changed or copy failed")
        if not row['key_complete']:
            required=row['key_length']
            if not required:raise RuntimeError("original ArticleType has no nonempty key")
            row=await one(index,required)
            if not row['valid'] or row['count']!=total or row['key_length']!=required or not row['key_complete']:
                raise RuntimeError("fresh original ArticleType key changed or was not copied completely")
        if not row['key']:raise RuntimeError("original ArticleType has an empty key")
        result.append(row)
    return result
