"""V3 display-text views; native strings, keys and SDK results stay untouched.

Display views strip only recognized formatting commands. Native contextual
formatting uses original constructors, formatter and destructors with retained
queries and owners. Raw native strings and keys remain available unchanged.
"""
from __future__ import annotations

import re
import struct

from ..v3_wire import resolve_v3_export
from .. import v3_data as data, v3_access, native_call


_STYLES = frozenset((
    "tooltippable_name", "tooltippable", "tooltip", "BOLD", "bold", "italic",
    "underline", "header", "title", "high", "low", "medium", "weak", "strong",
    "positive", "negative", "warning", "neutral", "variable", "v",
    *"BRGYPONVWLH",
))
# Command payloads stop BEFORE the next control or whitespace. Icons stop at
# their own '!': adjacent CJK prose is never part of the token.
_TOKENS = re.compile(
    r"(?P<close>[\x15#§]!)"
    r"|(?P<command>[\x15#](?P<word>[A-Za-z_][A-Za-z_0-9]*"
    r"(?:[:;][^\s\x15\x16#§]*)?)(?=[\s\x15\x16#§]|[^\x00-\x7f]|$)[ \t]?)"
    r"|(?P<legacy>§[A-Za-z0-9])"
    r"|(?P<icon>[\x16@][A-Za-z_][A-Za-z_0-9.:+\-]*!)"
)


def plain_text(text: str) -> str:
    """Render known V3 spans while keeping their nested visible text.

    U+0015/U+0016 are native rich-text markers. ``#...#!``, ``@...!`` and
    section-sign colors also occur in localization. Consume an entire known
    tooltip command, never just its control byte (which would expose its ID).
    Unknown commands and non-format control characters remain in the view.
    """
    parts, end = [], 0
    spans = {"\x15": [], "#": [], "§": []}
    for match in _TOKENS.finditer(text):
        parts.append(text[end:match.start()])
        token = match.group()
        replacement = token
        marker = token[0]
        if match.lastgroup == "close":
            stack = spans[marker]
            # Native strings can include redundant or mixed-spelling resets.
            # An unknown open span keeps its matching reset.
            known = stack.pop() if stack else True
            if known:
                replacement = ""
        elif match.group("command"):
            word = match.group("word")
            base = word.partition(":")[0].partition(";")[0]
            if base == "indent_newline" and word.startswith("indent_newline:") and word[15:].isdigit():
                replacement = "\n"
            else:
                known = (base == "tooltip" and word.startswith("tooltip:") and len(word) > 8
                         or base in _STYLES and (word == base or word.startswith(base + ";")))
                spans[marker].append(known)
                if known:
                    replacement = ""
        elif match.lastgroup == "legacy":
            known = token[1] in "BRGYPONVWLH0123456789"
            spans[marker].append(known)
            if known:
                replacement = ""
        elif match.lastgroup == "icon":
            replacement = ""
        parts.append(replacement)
        end = match.end()
    parts.append(text[end:])
    return "\n".join(line.strip(" \t\r\u00a0") for line in "".join(parts).split("\n")).strip("\n")


def display_field(value, field="name"):
    """Copy one explicit display field plus its exact original ``*_raw`` value.

    No coercion or truthiness test: None, false, zero and non-text native values
    remain themselves. Validation and error handling belong to the caller.
    """
    return {field: plain_text(value) if isinstance(value, str) else value,
            field + "_raw": value}


TEXT_CAPACITY=4096  # Exact existing PdxRuntimeRead single-read bound.
OFF={"context":0,"string":0x1a8,"name_view":0x1c8,"expression_view":0x1d8,
     "object":0x1e8,"type":0x1f0,"metadata":0x200,"inline":0x220,
     "received":0x230,"text":0x280}


def compose(image_base,read_address,storage):
    """Existing Win64 calls only; original scope ctor/builder/formatter/dtors."""
    def at(name):return storage+OFF[name]
    calls=((image_base+0x7bcf80,(at("context"),)),
        (image_base+0x3a93160,(at("context"),at("name_view"),at("object"),at("type"))),
        (image_base+0x3a84710,(at("string"),at("expression_view"),0,0,at("context"),0)),
        (read_address,(at("string"),at("metadata"),32,at("received")+12)),
        (read_address,(at("string"),at("inline"),16,at("received")+16)),
        (read_address,(at("string"),0,8,at("received")+20)),
        (read_address,(at("string")+16,0,8,at("received")+24)),
        (read_address,(0,at("text"),0,at("received")+28)),
        (image_base+0x61f810,(at("string"),)),
        (image_base+0x7bd0b0,(at("context"),)))
    def prepare(address):
        payload=bytearray(native_call.encode_batch(calls,request_address=address))
        arguments=[struct.unpack_from("<Q",payload,32+i*56+24)[0] for i in range(len(calls))]
        # CString heap pointer + exact length feed only bounded SafeRPM. For
        # SSO the heap read is irrelevant and only its inline bytes are used.
        struct.pack_into("<Q",payload,arguments[5]-address+8,arguments[7])
        struct.pack_into("<Q",payload,arguments[6]-address+8,arguments[7]+16)
        return bytes(payload)
    return native_call.encode_batch(calls),prepare


def _retain(ctx,record):
    records=getattr(ctx,"_localization_retained",None)
    if records is None:records=[];ctx._localization_retained=records
    records.append(record)
    return record


async def _eval(ctx,record,expression,bindings):
    result=await ctx.query(expression,lambda:data.evaluate(ctx.native,expression,bindings,
        adapter=ctx.adapters["data"],process=ctx.process))
    if result.value_handle:record["values"].append(result.value_handle)
    record["queries"].append({"expression":expression,"result":result})
    return result


def _finish_record(ctx,record):
    if record.get("values") or record.get("storage"):return
    retained=getattr(ctx,"_localization_retained",[])
    if record in retained:retained.remove(record)
    diagnostics=getattr(ctx,"diagnostics",None)
    if diagnostics is not None and hasattr(diagnostics,"append"):
        diagnostics.append({"operation":"contextual_localization","record":record})


async def _cleanup_values(ctx,record):
    for handle in tuple(reversed(record["values"])):
        result=await ctx.query("localizer.release",lambda h=handle:data.release(ctx.native,h,
            adapter=ctx.adapters["data"],process=ctx.process))
        record["releases"].append(result)
        if not result.error and not result.value_handle:record["values"].remove(handle)


def _phase_settled(record):
    phase=record.get("native_phase")
    return bool(phase and phase.get("settled") is True)


def settled_error(error):
    """True only for the last phase belonging to this exact formatting read."""
    record=getattr(error,"_localizer_record",None)
    return bool(record is not None and getattr(error,"_localizer_phase",None) is record.get("native_phase")
        and _phase_settled(record))


def _annotate_error(error,record):
    error._localizer_record=record
    error._localizer_phase=record.get("native_phase")


def _storage(ctx,record,address,size):
    record["storage"][address]=size
    record.setdefault("storage_free_attempts",set()).discard(address)
    return address


def _free_storage(ctx,record,address):
    attempts=record.setdefault("storage_free_attempts",set())
    if address not in record["storage"] or address in attempts:return
    attempts.add(address)  # A failed free is retained, never blindly repeated.
    ctx.process.free(address)
    record["storage"].pop(address)


def _cleanup_storage(ctx,record):
    first=None
    for address in tuple(reversed(record["storage"])):
        try:_free_storage(ctx,record,address)
        except BaseException as error:
            record.setdefault("storage_errors",[]).append(error)
            if first is None:first=error
    if first is not None:raise first


def _phase_completed(record,result):
    record["native_phase"].update(settled=True,result=result)
    record["native_temporaries_destroyed"]=True


async def _run_phase(ctx,record,count,work):
    # Do this BEFORE allocating/preparing a new fallback, not only before submit.
    # The bounded phase's old completion can never settle a newer native prefix.
    record.pop("native_temporaries_destroyed",None)
    phase={"index":record.get("phase_index",0)+1,"calls":count,"settled":False}
    record["phase_index"]=phase["index"];record["native_phase"]=phase
    try:
        return await work()
    except BaseException as error:
        _annotate_error(error,record)
        raise
    finally:
        if record.get("native_phase") is phase and phase["settled"]:
            try:_cleanup_storage(ctx,record)
            except BaseException as error:
                _annotate_error(error,record)
                raise


async def _format(ctx,record,object_pointer,type_id,type_name,expression):
    return await _run_phase(ctx,record,10,
        lambda:_format_bounded(ctx,record,object_pointer,type_id,type_name,expression))


async def _format_bounded(ctx,record,object_pointer,type_id,type_name,expression):
    name=("BUILDING" if type_name=="Building" else type_name).encode("utf-8")
    code=("["+expression+"]").encode("utf-8")
    tail=OFF["text"]+TEXT_CAPACITY
    name_at=(tail+7)&~7;code_at=(name_at+len(name)+8)&~7
    size=code_at+len(code)+1
    storage=_storage(ctx,record,ctx.process.allocate(size),size)
    ctx.process.write(storage,bytes(size))
    ctx.process.write(storage+name_at,name+b"\0")
    ctx.process.write(storage+code_at,code+b"\0")
    ctx.process.write(storage+OFF["name_view"],struct.pack("<QiB3x",storage+name_at,len(name),0))
    ctx.process.write(storage+OFF["expression_view"],struct.pack("<QiB3x",storage+code_at,len(code),0))
    ctx.process.write(storage+OFF["object"],struct.pack("<Q",object_pointer))
    ctx.process.write(storage+OFF["type"],struct.pack("<I",type_id))
    runtime=ctx.adapters.get("runtime",ctx.adapters["data"])
    read=resolve_v3_export(ctx.process, runtime,"PdxRuntimeRead")
    callback=resolve_v3_export(ctx.process, ctx.adapters["call"],"PdxNativeCallBatchInterface")
    initial,prepare=compose(ctx.image_base,read,storage)
    result=await ctx.query("localizer.original_outer_expression",lambda:v3_access.submit_callback(
        ctx.native,callback,initial,mode=1,flag=0,dispatch="interface",process=ctx.process,
        adapter=ctx.adapters["access"],call_adapter=ctx.adapters["call"],prepare_payload=prepare,
        decode_payload=native_call.NativeCallBatchResult.from_bytes))
    record["format_result"]=result
    batch=result.callee_result
    if (result.error or not result.entered or not result.left or batch is None or batch.error
        or batch.completed!=10 or len(batch.calls)!=10 or any(c.error for c in batch.calls)):
        raise RuntimeError("Original localizer prefix retained; do not retry constructors or guess cleanup")
    _phase_completed(record,result)
    raw=ctx.process.read(storage,size)
    required,capacity=struct.unpack_from("<2Q",raw,OFF["metadata"]+16)
    received=struct.unpack_from("<10I",raw,OFF["received"])
    metadata_ok=all(batch.calls[i].rax&0xffffffff==0 and received[i]==n
        for i,n in ((3,32),(4,16),(5,8),(6,8)))
    identity_ok=batch.calls[0].rax==storage+OFF["context"] and batch.calls[2].rax==storage+OFF["string"]
    if capacity<=15:
        complete=bool(metadata_ok and identity_ok and required<=capacity<=15)
        body=raw[OFF["inline"]:OFF["inline"]+required] if complete else None
    else:
        complete=bool(metadata_ok and identity_ok and required<=capacity and required<=TEXT_CAPACITY
            and (required==0 or (batch.calls[7].rax&0xffffffff==0 and received[7]==required)))
        body=raw[OFF["text"]:OFF["text"]+required] if complete else None
    decode_error=None
    try:text=body.decode("utf-8","strict") if body is not None else None
    except UnicodeDecodeError as exc:text=None;decode_error=str(exc)
    raw_text=text
    if text is not None:text=plain_text(text)
    answer={"expression":expression,"scope_type":type_name,"scope_type_id":type_id,
        "world":ctx.world,"text_bytes":body,"text":text,"raw_text":raw_text,"decode_error":decode_error,
        "text_complete":complete,"required_bytes":required,"capacity":capacity,
        "copy_capacity":TEXT_CAPACITY,"native_temporaries_destroyed":True,"context_supplied":True,
        "localization_success":None,"diagnostic_boundary":"Original formatter returns CString, not a success bool; compare native diagnostics separately",
        "receipts":[{"index":i,"adapter_error":c.error,"native_return":c.rax,
            "received":received[i] if i in (3,4,5,6,7) else None} for i,c in enumerate(batch.calls)]}
    record["answer"]=answer
    _free_storage(ctx,record,storage)
    if metadata_ok and identity_ok and required>TEXT_CAPACITY:
        record.setdefault("settled_short_reads",[]).append(answer)
        return await _format_complete(ctx,record,object_pointer,type_id,type_name,expression)
    return answer


async def read_building_text(ctx,building_id,expression):
    """Read a Building text through its real full-ID owning binding dependency."""
    await ctx.refresh()
    record=_retain(ctx,{"kind":"Building","values":[],"queries":[],"releases":[],"storage":{}})
    known=False
    try:
        type_id=await ctx.type_id("Building")
        held=await _eval(ctx,record,"Building.Self",(data.ObjectBinding.building(type_id,building_id),))
        if held.error or not held.object or "CDataBuilding" not in held.native_type_name:
            known=True;return {"available":False,"reason":"Original Building receiver unavailable"}
        # The ValueNode holds the original owning Binding2 even if Self is a raw
        # pointer Any. The pointer Any alone is NOT claimed to own the wrapper.
        result=await _format(ctx,record,held.object,type_id,"Building",expression)
        known=True;return result
    finally:
        if known or _phase_settled(record):
            await _cleanup_values(ctx,record)
            _finish_record(ctx,record)


async def read_building_tooltip(ctx,building_id):
    """Original native Building tooltip; normal caller behavior is unchanged."""
    return await read_building_text(ctx,building_id,"Building.GetTooltip")


async def read_queue_tooltip(ctx,index=0):
    """One current government queue receiver, promoted by original retained Any."""
    await ctx.refresh();country=await ctx.country_id()
    record=_retain(ctx,{"kind":"ConstructionQueueElement","values":[],"queries":[],"releases":[],"storage":{}})
    known=False
    try:
        binding=data.ObjectBinding.country(await ctx.type_id("Country"),country)
        source=await _eval(ctx,record,"Country.AccessGovernmentConstructionQueue",(binding,))
        if source.error:known=True;return {"available":False,"reason":"Original queue source unavailable"}
        count=await ctx.query("localizer.queue_count",lambda:data.list_count(ctx.native,source.value_handle,
            adapter=ctx.adapters["data"],process=ctx.process))
        if count.error or not 0<=index<count.total:
            known=True;return {"available":False,"reason":"Current native queue has no such element"}
        element=await ctx.query("localizer.queue_element",lambda:data.list_element(ctx.native,source.value_handle,index,
            adapter=ctx.adapters["data"],process=ctx.process))
        if element.value_handle:record["values"].append(element.value_handle)
        if element.error or "CPdxPtr@VCDataConstructionQueueElement" not in element.native_type_name:
            known=True;return {"available":False,"reason":"Original provider did not yield an owned queue wrapper"}
        type_id=await ctx.type_id("ConstructionQueueElement")
        # Existing retained binding calls original AnyToObject1F5FB30 through
        # BindingScope mode2, producing non-null Scope16 flag1. Never flip flag0.
        held=await _eval(ctx,record,"ConstructionQueueElement.Self",
            (data.ObjectBinding(type_id,element.value_handle,retained=True),))
        if held.error or not held.object or "CDataConstructionQueueElement" not in held.native_type_name:
            known=True;return {"available":False,"reason":"Original immutable queue promotion unavailable"}
        result=await _format(ctx,record,held.object,type_id,"ConstructionQueueElement","ConstructionQueueElement.GetTooltip")
        known=True;return result
    finally:
        if known or _phase_settled(record):
            await _cleanup_values(ctx,record)
            _finish_record(ctx,record)


async def read_retained_text(ctx,type_name,value_handle,expression):
    """Read localized text from an existing caller-owned data value.

    The original named receiver converter must accept the retained Any. Its
    successful BindingScope mode2 supplies a flag1 typed object. The new Self
    ValueNode keeps that original binding and dependency alive for formatting.
    Caller ownership of value_handle is unchanged.
    """
    await ctx.refresh()
    record=_retain(ctx,{"kind":type_name,"values":[],"queries":[],"releases":[],"storage":{}})
    known=False
    try:
        type_id=await ctx.type_id(type_name)
        held=await _eval(ctx,record,type_name+".Self",
            (data.ObjectBinding(type_id,value_handle,retained=True),))
        if held.error or not held.object:
            known=True;return {"available":False,"text":None,"raw_text":None,
                "text_complete":False,"reason":"The original native receiver is currently unavailable"}
        answer=await _format(ctx,record,held.object,type_id,type_name,expression)
        known=True;return answer
    finally:
        if known or _phase_settled(record):
            await _cleanup_values(ctx,record)
            _finish_record(ctx,record)


def compose_complete(image_base,read_address,allocate_address,rpm_address,storage):
    """Original formatter plus an exactly sized independent Windows byte copy."""
    def at(name):return storage+OFF[name]
    received=at("received")
    calls=((image_base+0x7bcf80,(at("context"),)),
        (image_base+0x3a93160,(at("context"),at("name_view"),at("object"),at("type"))),
        (image_base+0x3a84710,(at("string"),at("expression_view"),0,0,at("context"),0)),
        (read_address,(at("string"),at("metadata"),32,received+12)),
        (read_address,(at("string"),at("inline"),16,received+16)),
        (read_address,(at("string")+16,0,8,received+20)),
        (allocate_address,(0,0,0x3000,4)),
        (read_address,(at("string"),0,8,received+28)),
        (read_address,(at("string")+16,0,8,received+32)),
        (read_address,(0,0,8,received+36)),
        (rpm_address,(0xffffffffffffffff,0,0,0,received+40)),
        (image_base+0x61f810,(at("string"),)),
        (image_base+0x7bd0b0,(at("context"),)))
    def prepare(address):
        payload=bytearray(native_call.encode_batch(calls,request_address=address))
        arguments=[struct.unpack_from("<Q",payload,32+i*56+24)[0] for i in range(len(calls))]
        # VirtualAlloc's exact SIZE_T and RPM's exact nSize come from the SAME
        # live CString length. Its returned RAX becomes RPM's destination.
        for reader,destination in ((5,arguments[6]+8),(7,arguments[10]+8),
                (8,arguments[10]+24),(9,arguments[10]+16)):
            struct.pack_into("<Q",payload,arguments[reader]-address+8,destination)
        struct.pack_into("<Q",payload,arguments[9]-address,address+32+6*56+32)
        return bytes(payload)
    return native_call.encode_batch(calls),prepare


async def _format_complete(ctx,record,object_pointer,type_id,type_name,expression):
    return await _run_phase(ctx,record,13,
        lambda:_format_complete_native(ctx,record,object_pointer,type_id,type_name,expression))


async def _format_complete_native(ctx,record,object_pointer,type_id,type_name,expression):
    # The first bounded observation has finished and destroyed all native
    # temporaries. This is a fresh READ, never a replay of an unknown prefix.
    name=("BUILDING" if type_name=="Building" else type_name).encode("utf-8")
    code=("["+expression+"]").encode("utf-8")
    name_at=OFF["text"];code_at=(name_at+len(name)+8)&~7
    size=code_at+len(code)+1
    storage=_storage(ctx,record,ctx.process.allocate(size),size)
    ctx.process.write(storage,bytes(size))
    ctx.process.write(storage+name_at,name+b"\0");ctx.process.write(storage+code_at,code+b"\0")
    for view,offset,value in (("name_view",name_at,name),("expression_view",code_at,code)):
        ctx.process.write(storage+OFF[view],struct.pack("<QiB3x",storage+offset,len(value),0))
    ctx.process.write(storage+OFF["object"],struct.pack("<Q",object_pointer))
    ctx.process.write(storage+OFF["type"],struct.pack("<I",type_id))
    runtime=ctx.adapters.get("runtime",ctx.adapters["data"])
    read=resolve_v3_export(ctx.process, runtime,"PdxRuntimeRead")
    allocate=ctx.process.resolve_export("kernel32.dll","VirtualAlloc")
    rpm=ctx.process.resolve_export("kernel32.dll","ReadProcessMemory")
    callback=resolve_v3_export(ctx.process, ctx.adapters["call"],"PdxNativeCallBatchInterface")
    initial,prepare=compose_complete(ctx.image_base,read,allocate,rpm,storage)
    result=await ctx.query("localizer.original_complete_text",lambda:v3_access.submit_callback(
        ctx.native,callback,initial,mode=1,flag=0,dispatch="interface",process=ctx.process,
        adapter=ctx.adapters["access"],call_adapter=ctx.adapters["call"],prepare_payload=prepare,
        decode_payload=native_call.NativeCallBatchResult.from_bytes))
    record["complete_format_result"]=result;batch=result.callee_result
    copied_buffer=0
    if batch is not None and len(batch.calls)>6 and batch.completed>6 and not batch.calls[6].error:
        copied_buffer=batch.calls[6].rax
        if copied_buffer:_storage(ctx,record,copied_buffer,"exact native CString byte count")
    if (result.error or not result.entered or not result.left or batch is None or batch.error
        or batch.completed!=13 or len(batch.calls)!=13 or any(c.error for c in batch.calls)):
        raise RuntimeError("Original long-text prefix retained; constructors must not be replayed")
    _phase_completed(record,result)
    raw=ctx.process.read(storage,size)
    required,capacity=struct.unpack_from("<2Q",raw,OFF["metadata"]+16)
    received=struct.unpack_from("<10I",raw,OFF["received"])
    copied_count=struct.unpack_from("<Q",raw,OFF["received"]+40)[0]
    metadata_ok=all(batch.calls[i].rax&0xffffffff==0 and received[i]==n
        for i,n in ((3,32),(4,16),(5,8),(7,8),(8,8),(9,8)))
    identity_ok=batch.calls[0].rax==storage+OFF["context"] and batch.calls[2].rax==storage+OFF["string"]
    body=None;copy_error=None
    if capacity<=15:
        complete=bool(metadata_ok and identity_ok and required<=capacity<=15)
        if complete:body=raw[OFF["inline"]:OFF["inline"]+required]
    else:
        complete=bool(metadata_ok and identity_ok and required<=capacity and
            (required==0 or (copied_buffer and batch.calls[10].rax&0xffffffff and copied_count==required)))
        if complete:
            try:body=ctx.process.read(copied_buffer,required) if required else b""
            except (OSError,RuntimeError,MemoryError) as exc:complete=False;copy_error=str(exc)
    decode_error=None;rich=None
    if body is not None:
        try:rich=body.decode("utf-8","strict")
        except UnicodeDecodeError as exc:decode_error=str(exc)
    if not complete and copy_error is None:
        copy_error=("Could not allocate the native text copy" if required and not copied_buffer
            else "The native text copy did not return its complete byte count")
    answer={"expression":expression,"scope_type":type_name,"scope_type_id":type_id,"world":ctx.world,
        "text_bytes":body,"text":plain_text(rich) if rich is not None else None,"raw_text":rich,
        "text_complete":complete,"decode_error":decode_error,"copy_error":copy_error,
        "required_bytes":required,"capacity":capacity,"copy_capacity":required,
        "allocation_succeeded":bool(copied_buffer),"native_copy_succeeded":bool(batch.calls[10].rax&0xffffffff),
        "copied_bytes":copied_count,"reason":copy_error,
        "context_supplied":True,"native_temporaries_destroyed":True,"localization_success":None,
        "fresh_read_after_capacity":True,
        "diagnostic_boundary":"Complete byte copy is not a native localization success bool",
        "receipts":[{"index":i,"adapter_error":c.error,"native_return":c.rax} for i,c in enumerate(batch.calls)]}
    record["answer"]=answer
    # This is independent owned VirtualAlloc memory, not the destroyed CString.
    if copied_buffer:_free_storage(ctx,record,copied_buffer)
    _free_storage(ctx,record,storage)
    return answer
