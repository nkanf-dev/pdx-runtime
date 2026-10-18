"""CPU game-level economy composition tests; no process or native execution."""
import asyncio
from decimal import Decimal
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch, AsyncMock
from pdx_runtime.v3_game.economy import EconomyAPI
from pdx_runtime.v3_game._core import Diagnostics
from pdx_runtime import v3_data as D, v3_commands as C


def fixed(bits):return NS(error=0,kind=D.Kind.FIXED,value=D.FixedPoint(bits&((1<<64)-1)),
 value_handle=bits+1000 if bits>=0 else 999,snapshot=(bits&((1<<64)-1)).to_bytes(8,"little"),
 snapshot_bytes=8,total=8,raw_bits=bits&((1<<64)-1),native_type_name="CFixedPoint")
def string(text,handle=30):
 raw=text.encode();return NS(error=0,kind=D.Kind.STRING,value=text,value_handle=handle,
 snapshot=raw,snapshot_bytes=len(raw),total=len(raw),raw_bits=0,native_type_name="CString")
def boolean(value):return NS(error=0,kind=D.Kind.BOOL,value=value,value_handle=31,
 snapshot=bytes((value,)),snapshot_bytes=1,total=1,raw_bits=int(value),native_type_name="bool")
def q(result):return NS(result=result,request_id=1)

class Context:
 def __init__(self):
  self.native=NS(pid=7);self.process=NS(pid=7,resolve_export=lambda module,name,*,aliases=None:0x180010000)
  self.world=0x700000;self.image_base=0x140000000;self.interface_thread_id=77
  self.adapters={k:0x180000000 for k in ("data","objects","commands","access","call","runtime","console","script")}
  self.diagnostics=Diagnostics();self.supports={};self.calls=[];self.refreshes=0
 async def refresh(self):self.refreshes+=1
 async def blocking(self,function,*args,**kwargs):return function(*args,**kwargs)
 async def country_id(self):return 156
 async def type_id(self,name):return {"Country":83,"Market":92,"Goods":99}[name]
 async def perform(self,label,work):self.calls.append(label);return await work()
 async def query(self,label,factory,**kw):self.calls.append(label);return factory().result
 async def wait(self,query):return query.result
 def support(self,name):return self.supports[name]

class Fixture:
 def __init__(self):
  self.ctx=Context();self.released=[];self.evaluated=[];self.projected=[];self.submits=0;self.destroyed=0
  self.taxed=False;self.allowed=True;self.fail_submit=False;self.controlled=156;self.action=""
 def eval(self,client,expression,bindings=(),**kwargs):
  self.evaluated.append((expression,bindings))
  if expression=="Market.GetNameNoFormatting":return q(string("中华市场",32))
  if expression=="Market.GetName":return q(string("中华市场",32))
  if expression=="Market.GetGoodsSortedByBalance":return q(NS(error=0,kind=D.Kind.LIST,value_handle=40))
  if expression=="Goods.GetKey":return q(string({17:"iron",18:"wood"}[bindings[0].object],33))
  if expression in ("Goods.GetName","Goods.GetNameNoFormatting"):
   text={17:"铁",18:"木材"}[bindings[0].object]
   return q(string("\x15tooltippable_name \x15tooltip:dw_x,GoodsTooltip \x16icon!\xa0"+text+"\x15!\x15!",34))
  if expression=="Goods.IsTaxed(Country.Self)":return q(boolean(self.taxed))
  if ".HasImport" in expression:return q(boolean("TariffsHigh" in expression))
  if ".HasExport" in expression:return q(boolean("TariffsNone" in expression))
  if "LevelDesc" in expression:return q(string("原生税制效果",35))
  if "Set" in expression or "ToggleTaxation" in expression:
   self.action=expression
   value=D.NativeDataCommand(0x5000,0x6000,0x5018,80,"CDataCommand",self.ctx.world)
   return q(NS(error=0,value=value,value_handle=80,kind=D.Kind.COMMAND))
  raise AssertionError(expression)
 def project(self,client,fields,binding_rows,**kwargs):
  self.projected.append((tuple(fields),tuple(binding_rows)))
  cells=[]
  for row,bindings in enumerate(binding_rows):
   for column,expression in enumerate(fields):
    observed=self.eval(client,expression,bindings).result
    kind=observed.kind if observed.kind!=D.Kind.RAW else D.Kind.EMPTY
    snapshot=getattr(observed,"snapshot",b"")
    cells.append(D.ProjectionCell(row,column,observed.error,kind,getattr(observed,"type_id",0),
     getattr(observed,"raw_bits",0),snapshot,getattr(observed,"total",len(snapshot))))
  return q(D.ProjectionPage(0,self.ctx.world,len(fields),0,77,len(binding_rows),True,
   len(binding_rows),tuple(cells)))
 def release(self,client,handle,**kw):self.released.append(handle);return q(NS(error=0,value_handle=0))
 def functions(self,client,typeid,**kw):
  # Market has the original plain getter. Goods does not; rich native fallback.
  rows=(NS(name="GetNameNoFormatting",argument_count=0,callbacks=(1,0,0,0)),) if typeid==92 else ()
  return q(NS(error=0,rows=rows,next=len(rows),total=len(rows)))
 async def relation(self,*args,**kwargs):return {"market_id":2,"owners_released":True}
 async def market(self,*args,**kwargs):
  def row(i):return {"goods_index":i,"fields":{n:{"status":"ok","raw_signed":raw,"native_error":0}
   for n,raw in (("price",200000),("buy_orders",120000),("sell_orders",100000),("imports",0),("exports",0))}}
  return {"rows":[row(0),row(1)],"complete":True}
 def element(self,client,h,index,**kw):
  return q(NS(error=0,kind=D.Kind.OBJECT,value_handle=17+index,object=0x2000+index,
   native_type_name=".?AU?$SAnyType@V?$CPdxPtr@VCDataGoods@@@@@PdxAnyImpl@@"))
 def command(self,*a,**kw):return q(NS(error=0,value=D.NativeDataCommand(0x5000,0x6000,0x5018,80,"CDataCommand",self.ctx.world)))
 def clone(self,*a,**kw):return q(NS(error=0,cloned_command=0x8000))
 def player(self,*a,**kw):return q(NS(error=0,controlled_country_error=0,observer_enabled=0,
  controlled_country_id=self.controlled,world=self.ctx.world))
 def owner(self,*a,**kw):return Owner(self)
 def patches(self):
  from contextlib import ExitStack
  stack=ExitStack()
  stack.enter_context(patch.object(D,"evaluate",side_effect=self.eval));stack.enter_context(patch.object(D,"release",side_effect=self.release))
  stack.enter_context(patch.object(D,"enumerate_functions",side_effect=self.functions))
  stack.enter_context(patch.object(D,"project_page",side_effect=self.project))
  stack.enter_context(patch.object(D,"list_count",return_value=q(NS(error=0,total=2))))
  stack.enter_context(patch.object(D,"list_element",side_effect=self.element))
  stack.enter_context(patch.object(D,"command_value",side_effect=self.command))
  stack.enter_context(patch.object(C,"request",side_effect=self.clone))
  stack.enter_context(patch.object(C.NativeCommand,"from_result",side_effect=self.owner))
  stack.enter_context(patch("pdx_runtime.v3_game.economy.player.observe_player",side_effect=self.player))
  stack.enter_context(patch("pdx_runtime.v3_game.economy.market_relation.observe_country_market",side_effect=self.relation))
  stack.enter_context(patch("pdx_runtime.v3_game.economy.market_goods.read_market_goods",side_effect=self.market))
  return stack

class Owner:
 def __init__(self,f):self.f=f;self.command=0x8000;self.pending=None;self.attempt=NS(outcome="owned")
 def serialize(self):return q(NS(error=0,data=b"native"))
 def validate(self,**kw):return q(NS(error=0,validated=self.f.allowed,reason="" if self.f.allowed else "游戏需要更多行政力"))
 def submit(self,flags):
  self.f.submits+=1
  if self.f.fail_submit:self.pending=object();self.attempt.outcome="unknown";raise OSError("original delivery interrupted")
  self.command=0;self.attempt.outcome="consumed"
  if "ToggleTaxation" in self.f.action:self.f.taxed=not self.f.taxed
  return q(NS(error=0,accepted=1,reason=""))
 def destroy(self):self.f.destroyed+=1;self.command=0;self.attempt.outcome="consumed";return q(NS(error=0))

class Tests(unittest.IsolatedAsyncioTestCase):
 async def test_goods_use_real_definition_index_join_not_provider_order(self):
  f=Fixture();api=EconomyAPI(f.ctx)
  api._goods_index=AsyncMock(side_effect=lambda wrapper:1 if wrapper==0x2000 else 0)
  with f.patches():r=await api.goods()
  self.assertEqual([(v["id"],v["key"],v["name"]) for v in r],[(0,"wood","木材"),(1,"iron","铁")])
  self.assertEqual(r[0]["price"],Decimal(2));self.assertEqual(r[0]["shortage"],Decimal("0.2"))
  self.assertNotIn("pointer",r[0]);self.assertTrue(f.released)
 async def test_goods_key_is_game_language_and_unknown_has_reason(self):
  f=Fixture();api=EconomyAPI(f.ctx);api._goods_index=AsyncMock(side_effect=(1,0))
  with f.patches():r=await api.goods("wood")
  self.assertEqual(r["key"],"wood");self.assertEqual(r["name"],"木材")
 async def test_name_prefers_actual_native_plain_getter_metadata(self):
  f=Fixture();api=EconomyAPI(f.ctx);api._goods_index=AsyncMock(side_effect=(1,0))
  with f.patches():r=await api.market()
  self.assertEqual(r["name"],"中华市场");self.assertIn("Market.GetNameNoFormatting",[e for e,_ in f.evaluated])
  self.assertIn("rich_name",f.ctx.diagnostics["economy_names"]["Goods:iron"])
 async def test_tax_setter_generates_clones_validates_submits_once_hidden(self):
  f=Fixture();api=EconomyAPI(f.ctx)
  with f.patches():r=await api.set_tax_level("high")
  self.assertTrue(r["accepted"]);self.assertEqual(f.submits,1);self.assertEqual(f.destroyed,0)
  self.assertIn("Country.SetTaxLevelHigh",[e for e,_ in f.evaluated]);self.assertEqual(f.released,[80])
 async def test_native_game_rejection_reason_and_auto_destroy(self):
  f=Fixture();f.allowed=False;api=EconomyAPI(f.ctx)
  with f.patches():r=await api.set_tax_level("high")
  self.assertFalse(r["accepted"]);self.assertEqual(r["reason"],"游戏需要更多行政力")
  self.assertEqual(f.submits,0);self.assertEqual(f.destroyed,1);self.assertEqual(f.released,[80])
 async def test_foreign_country_cannot_silently_mutate_current_actor(self):
  f=Fixture();f.controlled=4;api=EconomyAPI(f.ctx)
  with f.patches():r=await api.set_tax_level("high")
  self.assertFalse(r["accepted"]);self.assertFalse(f.evaluated)
 async def test_wages_already_current_is_idempotent(self):
  f=Fixture();api=EconomyAPI(f.ctx);api._wage_level=AsyncMock(return_value=("high",None))
  with f.patches():r=await api.set_wages("government","high")
  self.assertFalse(r["changed"]);self.assertFalse(f.evaluated);self.assertEqual(f.submits,0)
 async def test_wages_fresh_band_readback_after_native_submission(self):
  f=Fixture();api=EconomyAPI(f.ctx);api._wage_level=AsyncMock(side_effect=(("medium",None),("high",None)))
  with f.patches():r=await api.set_wages("military","high")
  self.assertTrue(r["effect_observed"]);self.assertEqual(r["observed_level"],"high")
  self.assertIn("Country.SetMilitaryWageLevelHigh",[e for e,_ in f.evaluated])
 async def test_unknown_submit_retains_native_owner_and_source_values(self):
  f=Fixture();f.fail_submit=True;api=EconomyAPI(f.ctx)
  with f.patches():
   with self.assertRaises(OSError):await api.set_tax_level("high")
  self.assertEqual(f.submits,1);self.assertEqual(f.destroyed,0);self.assertFalse(f.released)
  self.assertTrue(f.ctx._economy_retained)
  for index in range(100):f.ctx.diagnostics.append({"other":index})
  self.assertTrue(any("retained_native_owner" in r for r in f.ctx._economy_retained))
 async def test_consumption_tax_idempotent_and_fresh_effect_readback(self):
  f=Fixture();api=EconomyAPI(f.ctx)
  async def selected(*a,**kw):return {"key":"wood","name":"木材","binding":D.ObjectBinding(99,17,True),"handles":[17]},"市场"
  api._market_labels=selected
  with f.patches():
   r=await api.set_consumption_tax("wood",True);r2=await api.set_consumption_tax("wood",True)
  self.assertTrue(r["effect_observed"]);self.assertFalse(r2["changed"]);self.assertEqual(f.submits,1)
 async def test_tariff_friendly_policy_maps_original_native_method(self):
  f=Fixture();api=EconomyAPI(f.ctx)
  async def selected(*a,**kw):return {"key":"iron","name":"铁","binding":D.ObjectBinding(99,17,True),"handles":[17]},"市场"
  api._market_labels=selected
  with f.patches():r=await api.set_tariff("iron","import","high")
  self.assertTrue(r["accepted"]);self.assertTrue(r["effect_observed"])
  self.assertIn("Country.SetImportTariffsHigh(Goods.Self)",[e for e,_ in f.evaluated])
 async def test_control_parameters_are_game_terms_not_raw_ids(self):
  api=EconomyAPI(Context())
  with self.assertRaises(ValueError):await api.set_tax_level(3)
  with self.assertRaises(ValueError):await api.set_wages("army",0.5)
  with self.assertRaises(ValueError):await api.set_consumption_tax("wood",1)


 async def test_building_io_real_goods_values_plain_names_and_auto_owners(self):
  f=Fixture();api=EconomyAPI(f.ctx);created=[]
  async def all_goods(*a,**kw):
   def field(value):return {"status":"ok","value":value}
   rows=[] if kw["source"]=="produced" else [{"fields":{
    "GetKey":field("wood"),"GetName":field("\x15tooltippable_name \x15tooltip:x \x16wood!\xa0木材\x15!"),
    "GetConsumption":field(D.FixedPoint(120000)),"GetProduction":field(D.FixedPoint(0)),
    "GetConsumptionValue":field(D.FixedPoint(240000)),"GetProductionValue":field(D.FixedPoint(0))}}]
   state={"pending":None,"bulk_complete":True,"bulk_initial_count":len(rows),
    "rows":rows,"bulk_diagnostics":[],"phase":"done","owned_values":[9]};created.append(state);return state
  async def release(state,**kw):state["owned_values"].clear();state["owners_released"]=True
  from pdx_runtime.v3_game import economy as em
  from pdx_runtime.v3_game.economy import building_io
  with patch.object(building_io,"read_all_building_io",side_effect=all_goods), \
       patch.object(building_io,"start_release_building_io",side_effect=release), \
       patch.object(em.objects,"read_ids",return_value=q(NS(error=0,records=(NS(error=0),)))):
   result=await api.building_io(0x23000019)
  self.assertEqual(result["inputs"][0]["name"],"木材")
  self.assertEqual(result["inputs"][0]["consumption"],Decimal("1.2"))
  self.assertEqual(result["inputs"][0]["production"],Decimal(0));self.assertEqual(result["outputs"],[])
  self.assertTrue(all(not s["owned_values"] for s in created))
 async def test_industries_state_filter_and_incremental_real_rows(self):
  f=Fixture();api=EconomyAPI(f.ctx);requested=[]
  async def buildings(**kw):
   requested.append(kw);return [{"id":2,"name":"伐木场","state_id":1,"state_name":"四川"},
                                {"id":3,"name":"铁矿","state_id":1,"state_name":"四川"}]
  f.ctx.domain=lambda name:NS(buildings=buildings)
  api._building_io=AsyncMock(return_value={"inputs":[],"outputs":[],"reasons":{}})
  emitted=[];rows=await api.industries(state="四川",include_io=True,on_row=emitted.append)
  self.assertEqual(requested,[{"state":"四川"}]);self.assertEqual(len(rows),2);self.assertEqual(emitted,rows)
  self.assertTrue(api.industry_progress["complete"]);self.assertEqual(api._building_io.await_count,2)
 async def test_industries_lightweight_does_not_read_io(self):
  f=Fixture();api=EconomyAPI(f.ctx)
  async def buildings(**kw):return [{"id":2,"state_id":1,"state_name":"四川"}]
  f.ctx.domain=lambda name:NS(buildings=buildings);api._building_io=AsyncMock()
  rows=await api.industries(include_io=False)
  self.assertNotIn("io",rows[0]);api._building_io.assert_not_awaited()
 async def test_building_unavailable_is_not_empty_or_zero_io(self):
  f=Fixture();api=EconomyAPI(f.ctx)
  from pdx_runtime.v3_game import economy as em
  with patch.object(em.objects,"read_ids",return_value=q(NS(error=0,records=(NS(error=1168),)))):
   result=await api.building_io(5)
  self.assertIsNone(result["inputs"]);self.assertIsNone(result["outputs"]);self.assertIn("building",result["reasons"])


 async def test_wage_scope_and_pointer_scope_storage_are_inline_original_abi(self):
  f=Fixture();api=EconomyAPI(f.ctx)
  import struct
  from pdx_runtime import native_call as nc
  from pdx_runtime.v3_game import economy as em
  def access_submit(client,callback,initial,**kw):
   address=0x200000000;raw=bytearray(kw["prepare_payload"](address))
   h=nc.BATCH_REQUEST.unpack_from(raw);self.assertEqual(h[2],2)
   records=[nc.REQUEST.unpack_from(raw,32+i*56) for i in range(2)]
   args=[struct.unpack_from("<2Q",raw,r[4]-address) for r in records]
   local=32+2*56+4*8
   self.assertEqual(args[0],(address+local+8,address+local+24))
   self.assertEqual(args[1],(0,address+local))
   self.assertEqual(records[1][2],f.ctx.image_base+0x252A8F0)
   self.assertEqual(struct.unpack_from("<QH3HQ",raw,local),(address+local+8,5,0,0,0,156))
   struct.pack_into("<I",raw,local+24,1)
   batch=NS(error=0,completed=2,calls=(NS(error=0,rax=0),NS(error=0,rax=3)))
   return q(NS(error=0,entered=1,left=1,payload=bytes(raw),callee_result=batch))
  with patch.object(em.v3_access,"submit_callback",side_effect=access_submit):level,reason=await api._wage_level(156,"government")
  self.assertEqual(level,"high");self.assertIsNone(reason)
 async def test_tax_options_report_native_constraints_without_submit(self):
  f=Fixture();api=EconomyAPI(f.ctx)
  with f.patches(),patch.object(api,"_tax_level",AsyncMock(return_value=(None,"Current-band observer is outside this command fixture"))):
   rows=await api.tax_options()
  self.assertEqual(len(rows),5);self.assertTrue(all(r["allowed"] for r in rows))
  self.assertEqual(f.submits,0);self.assertEqual(f.destroyed,5)
 async def test_effective_tariffs_are_game_policy_words(self):
  f=Fixture();api=EconomyAPI(f.ctx)
  async def selected(*a,**kw):return {"key":"iron","name":"铁","binding":D.ObjectBinding(99,17,True),"handles":[17]},"市场"
  api._market_labels=selected
  with f.patches():r=await api.tariffs("iron")
  self.assertEqual((r["import"],r["export"]),("high","none"));self.assertEqual(f.submits,0)

class GoodsReceiptFixture(Fixture):
 def __init__(self):
  super().__init__();self.receipts={};self.readback_value=True;self.readback_error=0
  self.readback_exception=None;self.release_exception=None;self.release_error=0
  self.validation_error=0;self.submit_accepted=1;self.submit_failure=OSError("same original submit interruption")
  self.readback_failure_query=NS(original="readback query")
  async def query(label,factory,**kw):
   self.ctx.calls.append(label)
   observed=factory().result
   self.receipts[label]=observed
   self.ctx.diagnostics.append({"label":label,"result":observed})
   operations=self.ctx.diagnostics["operations"]
   del operations[:-64]
   return observed
  self.ctx.query=query
 def eval(self,client,expression,bindings=(),**kwargs):
  if expression=="Country.HasImportTariffsNone(Goods.Self)":
   self.evaluated.append((expression,bindings))
   if self.readback_exception is not None:
    self.readback_exception.query=self.readback_failure_query
    raise self.readback_exception
   result=boolean(self.readback_value);result.error=self.readback_error
   return q(result)
  return super().eval(client,expression,bindings,**kwargs)
 def release(self,client,handle,**kwargs):
  if self.release_exception is not None:raise self.release_exception
  self.released.append(handle)
  return q(NS(error=self.release_error,value_handle=handle if self.release_error else 0))
 def owner(self,*args,**kwargs):
  self.command_owner=GoodsReceiptOwner(self)
  return self.command_owner

class GoodsReceiptOwner(Owner):
 def validate(self,**kwargs):
  result=super().validate(**kwargs).result
  result.error=self.f.validation_error
  return q(result)
 def submit(self,flags):
  if self.f.fail_submit:
   self.f.submits+=1;self.pending=self.f.readback_failure_query;self.attempt.outcome="unknown"
   raise self.f.submit_failure
  result=super().submit(flags).result
  result.accepted=self.f.submit_accepted
  result.reason="" if result.accepted else "原生拒绝理由"
  return q(result)

class GoodsPrimaryReceiptRetention(unittest.IsolatedAsyncioTestCase):
 def setup_action(self,*,many_handles=False):
  f=GoodsReceiptFixture();api=EconomyAPI(f.ctx)
  handles=list(range(1000,1080)) if many_handles else [17]
  async def selected(*args,**kwargs):
   return {"key":"small_arms","name":"小型武器","binding":D.ObjectBinding(99,17,True),"handles":handles},"市场"
  api._market_labels=selected
  return f,api,handles
 def assert_command_receipts(self,result,f,*,submitted=True):
  phase_labels={"generated":"Country.SetImportTariffsNone(Goods.Self)","command":"economy.command",
   "clone":"economy.clone","serialize":"economy.command_terms","validation":"economy.validate"}
  if submitted:phase_labels["submit"]="economy.submit"
  self.assertEqual(set(result["native_results"]),set(phase_labels))
  for phase,label in phase_labels.items():self.assertIs(result["native_results"][phase],f.receipts[label])
 async def test_tariff_original_receipts_survive_64_release_ring_eviction_no_extra_submit(self):
  f,api,handles=self.setup_action(many_handles=True)
  f.validation_error=122
  with f.patches():result=await api.set_tariff("small_arms","import","tariffs_none")
  self.assertTrue(result["accepted"]);self.assertTrue(result["observed"]);self.assertTrue(result["effect_observed"])
  self.assert_command_receipts(result,f)
  self.assertEqual(result["native_results"]["validation"].error,122)
  self.assertTrue(result["native_results"]["validation"].validated)
  self.assertEqual(result["native_results"]["submit"].error,0)
  self.assertEqual(result["native_results"]["submit"].accepted,1)
  self.assertIs(result["readback_result"],f.receipts["Country.HasImportTariffsNone(Goods.Self)"])
  self.assertEqual(result["readback_expression"],"Country.HasImportTariffsNone(Goods.Self)")
  self.assertEqual(len(f.ctx.diagnostics["operations"]),64)
  self.assertTrue(all(item["label"]=="economy.release" for item in f.ctx.diagnostics["operations"]))
  self.assertEqual(f.submits,1);self.assertEqual(f.destroyed,0);self.assertEqual(handles,[])
  self.assertEqual(len(f.ctx._economy_retained) if hasattr(f.ctx,"_economy_retained") else 0,0)
 async def test_native_validation_rejection_keeps_original_receipt_and_destroy_separate(self):
  f,api,_=self.setup_action(many_handles=True);f.allowed=False
  with f.patches():result=await api.set_tariff("small_arms","import","tariffs_none")
  self.assertFalse(result["accepted"]);self.assertEqual(result["reason"],"游戏需要更多行政力")
  self.assert_command_receipts(result,f,submitted=False)
  self.assertIs(result["cleanup_results"]["destroy"],f.receipts["economy.destroy"])
  self.assertNotIn("destroy",result["native_results"])
  self.assertNotIn("submit",result["native_results"])
  self.assertEqual(f.submits,0);self.assertEqual(f.destroyed,1)
 async def test_native_submit_rejection_original_bool_and_reason_unchanged(self):
  f,api,_=self.setup_action();f.submit_accepted=0
  with f.patches():result=await api.set_tariff("small_arms","import","tariffs_none")
  self.assertFalse(result["accepted"]);self.assertEqual(result["native_accepted"],0)
  self.assertEqual(result["reason"],"原生拒绝理由")
  self.assert_command_receipts(result,f)
  self.assertNotIn("readback_result",result)
  self.assertEqual(f.submits,1);self.assertEqual(f.destroyed,0)
 async def test_native_readback_error_preserves_accepted_command_and_raw_readback(self):
  f,api,_=self.setup_action();f.readback_error=13;f.readback_value=False
  with f.patches():result=await api.set_tariff("small_arms","import","tariffs_none")
  self.assertTrue(result["accepted"]);self.assertIsNone(result["observed"])
  self.assertFalse(result["effect_observed"])
  self.assertEqual(result["readback_result"].error,13)
  self.assertIs(result["readback_result"].value,False)
  self.assert_command_receipts(result,f)
  self.assertEqual(f.submits,1)
 async def test_readback_transport_error_carries_accepted_primary_same_error_query_no_replay(self):
  f,api,handles=self.setup_action();failure=OSError("same original readback interruption")
  f.readback_exception=failure
  with f.patches():
   with self.assertRaises(OSError) as caught:await api.set_tariff("small_arms","import","tariffs_none")
  self.assertIs(caught.exception,failure);self.assertIs(failure.query,f.readback_failure_query)
  self.assertTrue(failure.goods_action_result["accepted"])
  self.assert_command_receipts(failure.goods_action_result,f)
  self.assertNotIn("readback_result",failure.goods_action_result)
  self.assertEqual(f.submits,1);self.assertEqual(f.destroyed,0);self.assertFalse(f.released)
  self.assertTrue(any(record.get("retained_goods_control_values") is handles for record in f.ctx._economy_retained))
 async def test_unknown_submit_keeps_original_error_query_prefix_owner_no_cleanup_replay(self):
  f,api,handles=self.setup_action();f.fail_submit=True
  with f.patches():
   with self.assertRaises(OSError) as caught:await api.set_tariff("small_arms","import","tariffs_none")
  self.assertIs(caught.exception,f.submit_failure)
  prefix=caught.exception.economy_native_results
  self.assertEqual(set(prefix),{"generated","command","clone","serialize","validation"})
  self.assertIs(prefix["validation"],f.receipts["economy.validate"])
  self.assertIs(f.command_owner.pending,f.readback_failure_query)
  self.assertEqual(f.submits,1);self.assertEqual(f.destroyed,0);self.assertFalse(f.released)
  self.assertTrue(handles)
 async def test_nonfatal_release_error_does_not_replace_accepted_primary(self):
  f,api,handles=self.setup_action();failure=OSError("original release failed")
  f.release_exception=failure
  with f.patches():result=await api.set_tariff("small_arms","import","tariffs_none")
  self.assertTrue(result["accepted"]);self.assertTrue(result["effect_observed"])
  self.assert_command_receipts(result,f)
  self.assertIs(result["cleanup_errors"][0],failure)
  self.assertIn("original release failed",result["cleanup_warnings"][0])
  self.assertTrue(any(record.get("cleanup_error") is failure for record in f.ctx._economy_retained))
  self.assertTrue(handles);self.assertEqual(f.submits,1)
 async def test_native_release_warning_preserved_without_vetoing_submit_or_readback(self):
  f,api,handles=self.setup_action();f.release_error=5
  with f.patches():result=await api.set_tariff("small_arms","import","tariffs_none")
  self.assertTrue(result["accepted"]);self.assertTrue(result["effect_observed"])
  self.assertTrue(result["cleanup_warnings"])
  self.assertTrue(all("native value cleanup error 5" in item for item in result["cleanup_warnings"]))
  self.assertTrue(handles);self.assertEqual(f.submits,1)
  self.assertTrue(any("release_error" in record for record in f.ctx._economy_retained))
 async def test_consumption_tax_idempotent_keeps_original_before_no_command_or_false_submit_receipt(self):
  f,api,_=self.setup_action(many_handles=True);f.taxed=True
  with f.patches():result=await api.set_consumption_tax("small_arms",True)
  self.assertTrue(result["accepted"]);self.assertFalse(result["changed"])
  self.assertIs(result["before_result"],f.receipts["Goods.IsTaxed(Country.Self)"])
  self.assertNotIn("native_results",result)
  self.assertEqual(f.submits,0);self.assertEqual(f.destroyed,0)

class GoodsPrimaryReceiptCancellation(unittest.IsolatedAsyncioTestCase):
 def setup_action(self):
  return GoodsPrimaryReceiptRetention().setup_action()
 def cancel_destroy(self,f,failure):
  original_factory=f.owner;original_query=NS(original="same pending destroy query")
  f.destroy_attempts=0
  def factory(*args,**kwargs):
   owner=original_factory(*args,**kwargs)
   def destroy():
    f.destroy_attempts+=1;owner.pending=original_query;owner.attempt.outcome="unknown"
    failure.query=original_query
    raise failure
   owner.destroy=destroy
   return owner
  f.owner=factory
  return original_query
 async def test_goods_rejection_destroy_cancellation_carries_same_settled_answer_and_owner(self):
  f,api,handles=self.setup_action();f.allowed=False
  failure=asyncio.CancelledError("original destroy cancellation")
  original_query=self.cancel_destroy(f,failure)
  with f.patches():
   with self.assertRaises(asyncio.CancelledError) as caught:
    await api.set_tariff("small_arms","import","tariffs_none")
  self.assertIs(caught.exception,failure);self.assertIs(failure.query,original_query)
  result=failure.goods_action_result
  self.assertIs(result,failure.economy_action_result)
  self.assertFalse(result["accepted"]);self.assertFalse(result["allowed"])
  self.assertEqual(result["reason"],"游戏需要更多行政力")
  self.assertIs(result["native_results"],failure.economy_native_results)
  self.assertIs(result["native_results"]["validation"],f.receipts["economy.validate"])
  self.assertIs(result["cleanup_errors"][0],failure)
  self.assertNotIn("submit",result["native_results"])
  self.assertNotIn("cleanup_results",result)
  self.assertIs(f.command_owner.pending,original_query)
  self.assertTrue(any(record.get("retained_native_owner") is f.command_owner for record in f.ctx._economy_retained))
  self.assertTrue(handles);self.assertFalse(f.released)
  self.assertEqual(f.submits,0);self.assertEqual(f.destroy_attempts,1)
 async def test_direct_preview_destroy_cancellation_keeps_allowed_result_without_fake_submit(self):
  f,api,handles=self.setup_action()
  failure=asyncio.CancelledError("preview destroy cancellation")
  original_query=self.cancel_destroy(f,failure)
  bindings=(D.ObjectBinding.country(83,156),D.ObjectBinding(99,17,True))
  with f.patches():
   generated=await api._evaluate("Country.SetImportTariffsNone(Goods.Self)",bindings,handles)
   with self.assertRaises(asyncio.CancelledError) as caught:
    await api._submit_generated(generated,handles,{"country_id":156},submit=False)
  self.assertIs(caught.exception,failure);self.assertIs(failure.query,original_query)
  result=failure.economy_action_result
  self.assertTrue(result["allowed"]);self.assertNotIn("accepted",result)
  self.assertIs(result["native_results"]["generated"],generated)
  self.assertIs(result["native_results"]["validation"],f.receipts["economy.validate"])
  self.assertIs(result["native_results"],failure.economy_native_results)
  self.assertNotIn("submit",result["native_results"])
  self.assertIs(f.command_owner.pending,original_query)
  self.assertEqual(f.submits,0);self.assertEqual(f.destroy_attempts,1)
  self.assertFalse(f.released)
 async def test_accepted_goods_release_cancellation_keeps_primary_and_original_exception(self):
  f,api,handles=self.setup_action()
  failure=asyncio.CancelledError("original value release cancellation")
  original_query=NS(original="same release query");failure.query=original_query
  f.release_exception=failure
  with f.patches():
   with self.assertRaises(asyncio.CancelledError) as caught:
    await api.set_tariff("small_arms","import","tariffs_none")
  self.assertIs(caught.exception,failure);self.assertIs(failure.query,original_query)
  result=failure.goods_action_result
  self.assertTrue(result["accepted"]);self.assertTrue(result["effect_observed"])
  self.assertIs(result["native_results"]["submit"],f.receipts["economy.submit"])
  self.assertIs(result["cleanup_errors"][0],failure)
  self.assertTrue(handles);self.assertEqual(f.submits,1);self.assertEqual(f.destroyed,0)
  self.assertTrue(any(record.get("cleanup_error") is failure for record in f.ctx._economy_retained))

if __name__=="__main__":unittest.main(verbosity=2)
