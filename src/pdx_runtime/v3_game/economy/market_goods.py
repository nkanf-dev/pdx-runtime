"""Native Market goods pages with original field errors and pending-query continuation."""
from __future__ import annotations
from pdx_runtime.v3_wire import V3Wire
from pdx_runtime import v3_objects as objects

READS = []
FIELDS = (("price", "price_raw64", "price_error"),
    ("buy_orders", "buy_orders_raw64", "buy_orders_error"),
    ("sell_orders", "sell_orders_raw64", "sell_orders_error"),
    ("imports", "imports_raw64", "imports_error"),
    ("exports", "exports_raw64", "exports_error"))


def start_market_goods(client, process, *, market_id, world, adapter, page_size=64):
    if client.pid != process.pid or type(market_id) is not int or not 0 <= market_id < 0xffffffff:
        raise ValueError("same-process complete Market fullID required")
    if type(world) is not int or world <= 0 or type(page_size) is not int or page_size <= 0:
        raise ValueError("original world and positive caller-owned page capacity required")
    state = {"client":client,"process":process,"market_id":market_id,"world":world,
        "adapter":adapter,"page_size":page_size,"cursor":0,"pending":None,
        "operations":[],"pages":[],"rows":[],"complete":False,"status":"starting",
        "diagnostics":[],"atomic_snapshot":False,"mutations":[]}
    READS.append(state); return state


def _signed(bits):return bits - (1 << 64) if bits & (1 << 63) else bits


def _row(record, state, request_id):
    fields = {}
    for name, bits_field, error_field in FIELDS:
        bits = getattr(record,bits_field); error = getattr(record,error_field)
        usable = not (record.market_error or record.goods_error or error)
        fields[name] = {"native_error":error,"raw_bits":bits,
            "raw_signed":_signed(bits) if usable else None,
            "status":"ok" if usable else "native_error"}
    buy, sell = fields["buy_orders"],fields["sell_orders"]
    difference = buy["raw_signed"]-sell["raw_signed"] if buy["status"]==sell["status"]=="ok" else None
    return {"definition_index":record.definition_index,"goods_index":record.goods_index,
        "goods_pointer_observation":record.goods_pointer,
        "goods_label":None,"goods_label_status":"no original key/name leaf connected in this composition",
        "requested_market_id":record.requested_market_id,"resolved_market_id":record.resolved_market_id,
        "market_error":record.market_error,"goods_error":record.goods_error,"fields":fields,
        "derived_buy_minus_sell_raw":difference,
        "difference_boundary":"explicit arithmetic, not native shortage ratio; no imports/exports added twice",
        "source":{"world":state["world"],"request_id":request_id,
                  "operation":"enumerate_market_goods details=True"}}


async def continue_market_goods(state, *, await_result):
    """Settle exactly the retained query; known per-row errors remain independent.

    No field error is replaced with zero. No borrowed Goods pointer is reused as
    another callback's input. Every page has its own native observation time.
    A completed incomplete page is retained, not silently treated as empty.
    """
    try:
        while not state["complete"]:
            if state["pending"] is None:
                try:boundary=V3Wire(state["client"]).world_status()
                except Exception as error:
                    state["diagnostics"].append({"operation":"source observation","error":error})
                    state["status"]="waiting_for_source_observation";return state
                world=boundary.get("world",0);world=int(world,0) if isinstance(world,str) else world
                if boundary.get("status")!="ok" or boundary.get("transition_pending") or not world:
                    state["status"]="waiting_for_original_source";return state
                if world!=state["world"]:
                    state["status"]="source_changed_snapshot_incomplete";return state
                pending={"start":state["cursor"],"query":None};state["pending"]=pending
                try:
                    pending["query"]=objects.enumerate_market_goods(state["client"],state["market_id"],
                        start_index=state["cursor"],max_items=state["page_size"],details=True,
                        adapter=state["adapter"],process=state["process"])
                except BaseException as error:
                    pending["query"]=getattr(error,"query",None);pending["error"]=error;raise
            pending=state["pending"]
            if pending["query"] is None:raise RuntimeError("original unidentified query retained; no replay")
            result=await await_result("market.goods.page",pending["query"])
            if result is None:return state
            qid=getattr(pending["query"],"request_id",None)
            state["operations"].append({"request_id":qid,"start":pending["start"],"result":result})
            state["pages"].append(result);state["pending"]=None
            if result.error:
                state["status"]="native_page_error_snapshot_incomplete";return state
            if result.world != state["world"]:
                state["diagnostics"].append({"operation":"native source world changed",
                    "observed_world":result.world,"request_id":qid})
                state["status"]="source_changed_snapshot_incomplete";return state
            state["rows"].extend(_row(r,state,qid) for r in result.records)
            if result.next_index >= result.slot_bound:
                state["cursor"]=result.next_index;state["complete"]=True
                state["status"]="complete";return state
            if result.next_index <= pending["start"]:
                state["status"]="nonadvancing_page_snapshot_incomplete";return state
            state["cursor"]=result.next_index
        return state
    except BaseException as error:
        state["status"]="original_query_retained";error.query_state=state;raise


async def read_market_goods(client, process, *, market_id, world, adapter,
                            await_result, page_size=64):
    state=start_market_goods(client,process,market_id=market_id,world=world,
                             adapter=adapter,page_size=page_size)
    return await continue_market_goods(state,await_result=await_result)
