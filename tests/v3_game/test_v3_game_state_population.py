"""State population CPU composition only; no connection or native execution."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from pdx_runtime.v3_game.country import CountryAPI
from pdx_runtime.v3_game.country import population as subject

COUNTRY=0xab000001
STATE=0xcd000039

class Context:
 def __init__(self):
  self.native=object();self.process=object();self.world=0x12345678
  self.adapters={"objects":"objects","data":"data"};self.performed=[];self.diagnostics=[]
 async def refresh(self):pass
 async def country_id(self):return COUNTRY
 async def type_id(self,name):
  if name!="State":raise AssertionError(name)
  return 500
 async def perform(self,label,factory):self.performed.append(label);return await factory()
 def support(self,name):
  if name!="state_population":raise AssertionError(name)
  return subject

class Read:
 def __init__(self,ctx):self.world=ctx.world;self.calls=[]
 async def query(self,label,factory):self.calls.append(label);return factory()

class API:
 def __init__(self,ctx):
  self._ctx=ctx;self.reads=[];self.values={"GetPopulationSize":10000,"GetNumRadicals":333,
   "GetNumLoyalists":77,"GetNumberOfPopsPoor":7000,"GetNumberOfPopsMiddle":2500,"GetNumberOfPopsRich":500}
 async def _name(self,read,receiver,binding):self.reads.append(("name",receiver,binding));return "吉林南部",None
 async def _scalar(self,read,binding,getter,return_name,*,receiver):
  assert receiver=="State" and return_name=="int64" and binding.domain=="State" and binding.object==STATE
  self.reads.append((getter,receiver,binding));value=self.values[getter]
  if isinstance(value,BaseException):raise value
  return value if isinstance(value,tuple) else (value,None)

class Tests(unittest.IsolatedAsyncioTestCase):
 def setup(self):
  self.ctx=Context();self.api=API(self.ctx);self.read=Read(self.ctx)
  self.lookup=NS(error=0,world=self.ctx.world,records=(NS(error=0,resolved_id=STATE,owner_id=COUNTRY),))
 async def run_read(self,**options):
  with patch.object(subject.objects,"read_ids",return_value=self.lookup) as lookup:
   result=await subject.read_state(self.api,self.read,COUNTRY,STATE,**options)
  args=lookup.call_args
  self.assertEqual(args.args[2],(STATE,));self.assertEqual(args.args[1],subject.objects.Domain.STATE)
  return result
 async def test_six_direct_counts_are_local_people_not_pop_records_or_ig_clout(self):
  self.setup();r=await self.run_read()
  self.assertEqual(r["state_id"],STATE);self.assertEqual(r["state_name"],"吉林南部")
  self.assertEqual(r["population"],10000);self.assertEqual(r["radicals"],333);self.assertEqual(r["loyalists"],77)
  self.assertEqual([r["strata"][s]["population"] for s in ("lower","middle","upper")],[7000,2500,500])
  self.assertTrue(all(r["strata"][s]["radicals"] is None and r["strata"][s]["loyalists"] is None for s in r["strata"]))
  self.assertEqual(len(self.api.reads),7);self.assertEqual(r["units"]["population"],"people")
 async def test_native_zero_is_not_missing_or_forecast(self):
  self.setup();self.api.values["GetNumRadicals"]=0;self.api.values["GetNumberOfPopsRich"]=0
  r=await self.run_read();self.assertEqual(r["radicals"],0);self.assertEqual(r["strata"]["upper"]["population"],0)
  self.assertNotIn("event",r);self.assertNotIn("new_radicals",r)
 async def test_known_missing_count_does_not_replace_with_zero_or_block_others(self):
  self.setup();self.api.values["GetNumberOfPopsMiddle"]=(None,"native getter unavailable")
  r=await self.run_read();self.assertIsNone(r["strata"]["middle"]["population"])
  self.assertEqual(r["strata"]["lower"]["population"],7000)
  self.assertEqual(r["strata"]["middle"]["reasons"]["population"],"native getter unavailable")
 async def test_foreign_state_no_fake_ownership_or_population_queries(self):
  self.setup();self.lookup.records[0].owner_id=COUNTRY+1;r=await self.run_read()
  self.assertEqual(self.api.reads,[]);self.assertIsNone(r["population"]);self.assertIn("not owned",r["reasons"]["state"])
 async def test_missing_state_is_not_false_empty_population(self):
  self.setup();self.lookup.records=();r=await self.run_read()
  self.assertIsNone(r["population"]);self.assertIn("not available",r["reasons"]["state"])
 async def test_cross_world_not_mixed_into_current_state(self):
  self.setup();self.lookup.world+=1
  with self.assertRaises(RuntimeError):await self.run_read()
  self.assertEqual(self.api.reads,[])
 async def test_unknown_scalar_never_replayed(self):
  self.setup();self.api.values["GetNumRadicals"]=TimeoutError("original query retained")
  with self.assertRaises(TimeoutError):await self.run_read()
  self.assertEqual(sum(name=="GetNumRadicals" for name,_,_ in self.api.reads),1)
 async def test_invalid_target_no_world_or_native_discovery(self):
  ctx=Context();api=CountryAPI(ctx)
  for invalid in (True,-1,0xffffffff,"吉林南部",None):
   with self.assertRaises(ValueError):await api.population(state=invalid)
  self.assertEqual(ctx.performed,[])


class DetailsRead:
 def __init__(self,api):
  self.api=api;self.world=api._ctx.world;self.uncertain=False;self.owners=[];self.closed=False
  api.read_scopes.append(self)
 async def check_world(self):
  if self.api._ctx.world!=self.world:raise RuntimeError("world changed")
 async def query(self,label,factory):
  try:r=factory()
  except BaseException:self.uncertain=True;raise
  if getattr(r,"value_handle",0):self.owners.append(r.value_handle)
  return r
 async def close(self):
  self.closed=True
  mode=getattr(self.api,"child_close_mode",None)
  if self is not self.api.read_scopes[0] and self.owners and mode:
   self.uncertain=True;self.api.retained.append(self)
   if mode=="unknown":raise TimeoutError("original child release is still unknown")
   if mode=="failed":raise RuntimeError("native child release failed")
   return
  if self.uncertain:self.api.retained.append(self)
  else:self.api.released.extend(self.owners);self.owners=[]

class DetailsAPI(API):
 def __init__(self,ctx,pops):
  super().__init__(ctx);self.pops=pops;self.read_scopes=[];self.retained=[];self.released=[];self.child_close_mode=None;self.getters=[];self.membership_calls=[]
  self.class_lists={"lower":["opaque-class-low"],"middle":["opaque-class-mid"],"upper":["opaque-class-high"]}
  self.fail_field=None;self.scalar_missing=None;self.borrowed=False;self.final_change=False;self.count_calls=0
  async def typeid(name):return {"State":500,"Pop":600,"SocialClass":700,"SocialHierarchy":800}[name]
  ctx.type_id=typeid
 async def _function(self,read,receiver,typeid,getter,**kw):
  self.getters.append((receiver,getter))
  result=0
  if getter=="GetSocialClass":result=701
  elif getter=="GetSocialHierarchy":result=801
  elif "StrataSocialClasses" in getter:result=70
  return NS(return_type_id=result),None
 @staticmethod
 def text(text):
  raw=text.encode();return NS(error=0,kind=subject.data.Kind.STRING,snapshot=raw,
    snapshot_bytes=len(raw),total=len(raw),type_id=0)
 async def _getter(self,read,receiver,binding,getter,*,chain="",return_type_id=None,**kw):
  self.getters.append((receiver,getter+chain))
  if receiver=="State":
   read.owners.append(100);return NS(error=0,value_handle=100),None
  p=self.pops[binding.object-201]
  if getter=="IsValid":return NS(kind=subject.data.Kind.BOOL,value=p.get("valid",True)),None
  if getter=="GetTooltipTag":return self.text(p["tag"]),None
  if getter=="GetSocialClass" and chain==".GetTooltipTag":return self.text(p["class"]),None
  if getter=="GetSocialClass" and chain==".GetSocialHierarchy.GetTooltipTag":return self.text("opaque-hierarchy"),None
  raise AssertionError((receiver,getter,chain))
 async def _scalar(self,read,binding,getter,return_name,*,receiver):
  if receiver=="State":return await super()._scalar(read,binding,getter,return_name,receiver=receiver)
  self.getters.append((receiver,getter));p=self.pops[binding.object-201]
  if self.fail_field==getter:read.uncertain=True;raise TimeoutError("original pending")
  if self.scalar_missing==(binding.object,getter):return None,"native scalar unavailable"
  return p[{"GetTotalSize":"population","GetNumRadicals":"radicals","GetNumLoyalists":"loyalists"}[getter]],None
 def list_count(self,client,handle,**kw):
  self.count_calls+=1;return NS(error=0,total=len(self.pops)+(1 if self.final_change and self.count_calls>1 else 0))
 def list_element(self,client,handle,index,**kw):
  return NS(error=50 if self.borrowed else 0,value_handle=0 if self.borrowed else 201+index,
    native_type_name="raw CDataPop *" if self.borrowed else "class?$CPdxPtr@VCDataPop@@")
 def class_element(self,client,source,index,expression,bindings,**kw):
  self.membership_calls.append((source,index,expression,bindings,kw))
  tier=next(tier for tier in ("lower","middle","upper") if tier.title()+"Strata" in source)
  tags=self.class_lists[tier]
  result=self.text(tags[index])
  return NS(error=0,source_count=len(tags),action=NS(result=result))

class DetailTests(unittest.IsolatedAsyncioTestCase):
 def setup_details(self):
  self.ctx=Context();self.pops=[
   {"tag":"opaque-pop-a","class":"opaque-class-low","population":700,"radicals":70,"loyalists":7},
   {"tag":"opaque-pop-b","class":"opaque-class-mid","population":250,"radicals":25,"loyalists":2},
   {"tag":"opaque-pop-c","class":"opaque-class-high","population":50,"radicals":5,"loyalists":1}]
  self.api=DetailsAPI(self.ctx,self.pops);self.read=DetailsRead(self.api)
  self.lookup=NS(error=0,world=self.ctx.world,records=(NS(error=0,resolved_id=STATE,owner_id=COUNTRY),))
 async def run_details(self):
  with patch.object(subject.objects,"read_ids",return_value=self.lookup),patch.object(subject.data,"list_count",side_effect=self.api.list_count),patch.object(subject.data,"list_element",side_effect=self.api.list_element),patch.object(subject.data,"evaluate_element",side_effect=self.api.class_element):
   return await subject.read_state(self.api,self.read,COUNTRY,STATE,details=True)
 async def test_class_membership_uses_real_social_hierarchy_not_profession_or_ordinal(self):
  self.setup_details();r=await self.run_details()
  self.assertEqual([r["strata"][s]["radicals"] for s in ("lower","middle","upper")],[70,25,5])
  self.assertEqual([r["strata"][s]["loyalists"] for s in ("lower","middle","upper")],[7,2,1])
  self.assertTrue(r["population_scan"]["complete"]);self.assertEqual(len(self.api.membership_calls),3)
  self.assertFalse(any("PopType" in receiver or "StrataConcept" in getter for receiver,getter in self.api.getters))
  self.assertTrue(all(expr=="SocialClass.GetTooltipTag" and bindings[0].retained and opts["world"]==self.ctx.world
    for _,_,expr,bindings,opts in self.api.membership_calls))
  self.assertEqual(r["strata"]["lower"]["population"],7000) # independent State value is not replaced
  self.assertEqual(r["strata"]["lower"]["observed_population"],700)
 async def test_repeated_pop_identity_marks_partial_not_double_count(self):
  self.setup_details();self.pops[1]["tag"]=self.pops[0]["tag"];r=await self.run_details()
  self.assertFalse(r["population_scan"]["complete"]);self.assertIsNone(r["strata"]["lower"]["radicals"])
  self.assertEqual(r["strata"]["lower"]["observed_radicals"],70)
 async def test_collection_count_change_not_claimed_complete_or_reconciled(self):
  self.setup_details();self.api.final_change=True;r=await self.run_details()
  self.assertFalse(r["population_scan"]["complete"])
  self.assertTrue(all(row["radicals"] is None for row in r["strata"].values()))
 async def test_borrowed_pop_not_retagged_or_used_after_callback(self):
  self.setup_details();self.api.borrowed=True;r=await self.run_details()
  self.assertFalse(r["population_scan"]["complete"]);self.assertEqual(self.api.membership_calls,[])
  self.assertTrue(all(row["radicals"] is None for row in r["strata"].values()))
 async def test_missing_one_political_field_preserves_other_complete_counts(self):
  self.setup_details();self.api.scalar_missing=(202,"GetNumLoyalists");r=await self.run_details()
  self.assertEqual(r["strata"]["lower"]["loyalists"],7);self.assertIsNone(r["strata"]["middle"]["loyalists"])
  self.assertEqual(r["strata"]["middle"]["radicals"],25);self.assertFalse(r["population_scan"]["complete"])
 async def test_invalidated_pop_does_not_become_false_zero(self):
  self.setup_details();self.pops[0]["valid"]=False;r=await self.run_details()
  self.assertFalse(r["population_scan"]["complete"]);self.assertIsNone(r["strata"]["lower"]["radicals"])
 async def test_unknown_pop_query_keeps_parent_source_graph_and_original_row(self):
  self.setup_details();self.api.fail_field="GetNumRadicals"
  with self.assertRaises(TimeoutError):await self.run_details()
  self.assertTrue(self.read.uncertain);self.assertTrue(self.api.retained)
  self.assertTrue(any(scope.owners for scope in self.api.retained))
  self.assertEqual(sum(getter=="GetNumRadicals" for receiver,getter in self.api.getters if receiver=="Pop"),1)
 async def test_ambiguous_class_identity_not_guessed_from_localized_name(self):
  self.setup_details();self.api.class_lists["middle"].append("opaque-class-low");r=await self.run_details()
  self.assertFalse(r["population_scan"]["complete"]);self.assertIsNone(r["strata"]["lower"]["radicals"])


 async def test_unknown_child_cleanup_keeps_parent_state_source_through_outer_finally(self):
  self.setup_details();self.api.child_close_mode="unknown"
  try:
   with self.assertRaises(TimeoutError):await self.run_details()
  finally:await self.read.close()
  self.assertTrue(self.read.uncertain);self.assertIn(100,self.read.owners)
  self.assertNotIn(100,self.api.released);self.assertIn(self.read,self.api.retained)
  self.assertTrue(any(201 in r.owners for r in self.api.retained))
 async def test_failed_child_cleanup_keeps_parent_state_source_through_outer_finally(self):
  self.setup_details();self.api.child_close_mode="failed"
  try:
   with self.assertRaises(RuntimeError):await self.run_details()
  finally:await self.read.close()
  self.assertTrue(self.read.uncertain);self.assertIn(100,self.read.owners)
  self.assertNotIn(100,self.api.released);self.assertIn(self.read,self.api.retained)
 async def test_retained_child_cleanup_without_raise_propagates_parent_uncertainty(self):
  self.setup_details();self.api.child_close_mode="retained"
  try:await self.run_details()
  finally:await self.read.close()
  self.assertTrue(self.read.uncertain);self.assertIn(100,self.read.owners)
  self.assertNotIn(100,self.api.released);self.assertIn(self.read,self.api.retained)

if __name__=="__main__":unittest.main(verbosity=2)
