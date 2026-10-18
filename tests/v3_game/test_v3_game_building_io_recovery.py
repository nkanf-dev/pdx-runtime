"""CPU-only original Building IO scheduling and continuation regressions."""
import asyncio
from decimal import Decimal
from threading import Event
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
from pdx_runtime import v3_data as data
from pdx_runtime.native_bridge import BridgeError
from pdx_runtime.v3_game import V3Game,PendingOperation
from pdx_runtime.v3_game._core import GameContext
from pdx_runtime.v3_game.economy import EconomyAPI
from pdx_runtime.v3_game.economy import building_io as helper
from tests.fixtures.building_io import Fixture

class Query:
 def __init__(self,result,gate=None):self.result=result;self.gate=gate;self._result=None;self._released=False;self.reads=0;self.request_id=17;self.on_read=None
 def read(self):
  self.reads+=1
  if self.on_read is not None:self.on_read()
  if self.gate is not None and not self.gate.is_set():return None
  self._result=self.result;self._released=True;return self.result

class Tests(unittest.IsolatedAsyncioTestCase):
 def context(self):
  native=NS(pid=7,v3_world_status=lambda:{"status":"ok","world":17})
  process=NS(pid=7)
  ctx=GameContext(NS(native=native,namespace={}),process,timeout=.005,poll_interval=.001)
  ctx.world=17
  async def refresh():return NS(world=17)
  ctx.refresh=refresh;ctx.support=lambda name:helper if name=="building_io" else None
  return ctx
 def submitter(self,fixture):
  def submit(*args,**kwargs):return Query(fixture.submit(*args,**kwargs).result)
  return submit
 async def test_all_factories_including_release_use_dispatch_hook(self):
  fixture=Fixture(counts=[1]);seen=[]
  async def dispatch(factory):
   query=factory();seen.append(query.result.action);return query
  async def complete(label,query):return query.read()
  with patch.object(data,"_submit",side_effect=self.submitter(fixture)):
   state=await helper.read_all_building_io(object(),building_full_id=3111,
    await_result=complete,submit_query=dispatch,page_capacity=2)
   await helper.start_release_building_io(state,await_result=complete)
  self.assertTrue(state["bulk_complete"]);self.assertEqual(state["owned_values"],[])
  self.assertEqual(len(seen),len(fixture.calls))
  for action in (data.Action.TYPES,data.Action.FUNCTIONS,data.Action.EVALUATE,data.Action.LIST_COUNT,data.Action.LIST_ELEMENT,data.Action.RELEASE):self.assertIn(action,seen)
 async def test_shared_core_retries_explicit_not_sent_without_restarting_whole_read(self):
  ctx=self.context();api=EconomyAPI(ctx);fixture=Fixture(counts=[1,0]);attempts=[]
  normal=self.submitter(fixture)
  def submit(*args,**kwargs):
   attempts.append(1)
   if len(attempts)==1:raise BridgeError("fixture before submit",170,delivery="not_sent")
   return normal(*args,**kwargs)
  with patch.object(data,"_submit",side_effect=submit),patch("pdx_runtime.v3_game.economy.objects.read_ids",return_value=Query(NS(error=0,records=(NS(error=0),)))):
   result=await api.building_io(3111)
   if isinstance(result,PendingOperation):result=await result.resume(timeout=.5)
  self.assertEqual(len(result["inputs"]),1);self.assertEqual(result["outputs"],[])
  self.assertEqual(fixture.expressions().count("Building.AccessConsumedGoods"),1)
  self.assertEqual(fixture.expressions().count("Building.AccessProducedGoods"),1)
  self.assertEqual(len(attempts),len(fixture.calls)+1)
  self.assertEqual(ctx.pending_queries,[]);self.assertFalse(ctx._query_lock.locked())
 async def test_original_types_zero_busy_resumes_same_state_and_keeps_consumed(self):
  ctx=self.context();api=EconomyAPI(ctx);fixture=Fixture(counts=[1,0]);normal=self.submitter(fixture)
  async def complete(label,query):return await ctx.wait(query)
  with patch.object(data,"_submit",side_effect=normal):
   consumed=await helper.read_all_building_io(ctx.native,building_full_id=3111,source="consumed",
    process=ctx.process,await_result=complete)
   inputs=api._building_io_rows(consumed)
   await helper.start_release_building_io(consumed,await_result=complete)
  rejected=BridgeError("V3 query submit",170,delivery="not_sent")
  with patch.object(data,"_submit",side_effect=rejected):
   original=await helper.read_all_building_io(ctx.native,building_full_id=3111,source="produced",
    process=ctx.process,await_result=complete)
  original.pop("submit_query",None)  # Shape of Root's installed legacy helper.
  original_pending=original["pending"]
  self.assertIsNone(original_pending["query"]);self.assertEqual(original["owned_values"],[])
  partial={"building_id":3111,"inputs":inputs,"outputs":None,"reasons":{},"units":{},"snapshot":"separate observations"}
  with patch.object(data,"_submit",side_effect=normal):
   result=await api._resume_building_io_state(original,partial)
   if isinstance(result,PendingOperation):result=await result.resume(timeout=.5)
  self.assertIs(result,partial);self.assertIs(result["inputs"],inputs);self.assertEqual(result["outputs"],[])
  self.assertIs(original["rejected_submissions"][0],original_pending)
  self.assertEqual(fixture.expressions().count("Building.AccessConsumedGoods"),1)
  self.assertEqual(fixture.expressions().count("Building.AccessProducedGoods"),1)
  self.assertTrue(original["owners_released"]);self.assertEqual(original["owned_values"],[])
 async def test_accepted_unknown_query_retains_coroutine_without_factory_replay(self):
  ctx=self.context();api=EconomyAPI(ctx);fixture=Fixture(counts=[0,0]);normal=self.submitter(fixture)
  gate=Event();read_started=asyncio.Event();loop=asyncio.get_running_loop();first=[]
  def submit(*args,**kwargs):
   query=normal(*args,**kwargs)
   if not first:
    query.gate=gate;query.on_read=lambda:loop.call_soon_threadsafe(read_started.set)
    first.append(query);error=OSError("original delivery uncertain");error.query=query;raise error
   return query
  with patch.object(data,"_submit",side_effect=submit),patch("pdx_runtime.v3_game.economy.objects.read_ids",return_value=Query(NS(error=0,records=(NS(error=0),)))):
   pending=None
   try:
    pending=await api.building_io(3111)
    self.assertIsInstance(pending,PendingOperation)
    # The observer deadline is not proof that this exact query was adopted.
    await asyncio.wait_for(read_started.wait(),.5)
    self.assertIs(ctx.pending_queries[0],first[0])
    self.assertEqual(len(fixture.calls),1);gate.set();result=await pending.resume(timeout=.5)
   finally:
    # Keep factories patched until the original task has completed its cleanup.
    gate.set()
    if isinstance(pending,PendingOperation):await pending.task
  self.assertEqual(result["inputs"],[]);self.assertEqual(result["outputs"],[])
  self.assertGreater(first[0].reads,1);self.assertEqual(ctx.pending_queries,[])
 async def test_unidentified_submission_is_retained_with_specific_cause(self):
  ctx=self.context();api=EconomyAPI(ctx);calls=[]
  def broken(*args,**kwargs):calls.append(1);raise OSError("precise submission failure")
  with patch.object(data,"_submit",side_effect=broken),patch("pdx_runtime.v3_game.economy.objects.read_ids",return_value=Query(NS(error=0,records=(NS(error=0),)))):
   with self.assertRaisesRegex(RuntimeError,"precise submission failure") as caught:
    result=await api.building_io(3111)
    if isinstance(result,PendingOperation):await result.resume(timeout=.5)
  self.assertEqual(calls,[1]);error=caught.exception
  self.assertIsNotNone(error.original_state);self.assertIsNone(error.original_query)
  self.assertIs(ctx._economy_retained[0]["building_io"],error.original_state)
  self.assertIsInstance(error.__cause__,OSError)

if __name__=="__main__":unittest.main(verbosity=2)
