"""Game-free sampling admission/progress/history and dependency boundary tests."""
import asyncio
from unittest import IsolatedAsyncioTestCase, TestCase
from pdx_runtime import _sampling as sampling


class BoundaryTests(TestCase):

    def test_two_unrelated_opaque_generations_and_sources_share_fair_progress(self):
        for generation,keys in ((b"storage-A",("north","south")),((9,"second"),(0,983))):
            progress=sampling.RoundRobinProgress(generation)
            first=progress.plan(keys);self.assertEqual(first,keys)
            progress.attempted(keys[0],keys,next_position=0)
            self.assertEqual(progress.plan(keys)[0],keys[1])
            self.assertEqual(progress.positions[keys[0]],0)
            self.assertEqual(progress.age(keys[0]),1)
            self.assertEqual(progress.copied()["generation"],generation)
            three=keys+("tail",)
            progress.next_source="tail"
            self.assertEqual(progress.plan(three,priority=(keys[0],)),(keys[0],"tail",keys[1]))
            cursor=sampling.PageCursor(generation)
            cursor.advance(128,complete=False)
            self.assertEqual(cursor.next_index,128)
            cursor.reset(("new",generation));self.assertEqual(cursor.next_index,0)

    def test_history_is_copied_bounded_and_generation_local(self):
        history=sampling.SampleHistory(("opaque",0),capacity=2)
        batch=[{"value":False,"id":0}]
        first=history.append(batch);batch[0]["value"]=True
        self.assertIs(first["changes"][0]["value"],False)
        history.append([]);history.append([])
        gap=history.append([],first["cursor"])
        self.assertEqual(gap["cursor_status"],"gap")
        changed=sampling.SampleHistory("unrelated").append([],first["cursor"])
        self.assertEqual(changed["cursor_status"],"generation_changed")


class AdmissionTests(IsolatedAsyncioTestCase):
    async def test_pending_original_settles_then_no_followup_dispatch(self):
        started,release=asyncio.Event(),asyncio.Event();stop=False;submissions=[]
        async def checkpoint():return {"opaque":0},None
        async def dispatch(label,factory,**kwargs):
            submissions.append(factory());started.set();await release.wait();return "copied"
        control=sampling.QuerySamplingControl(dispatch,checkpoint,lambda:"stop" if stop else None,
            query_limit=2,checkpoint_limit=3)
        task=asyncio.create_task(control.query("first",lambda:object()));await started.wait()
        stop=True
        self.assertFalse(task.done())
        with self.assertRaises(sampling.SamplingStopped):await control.query("second",lambda:object())
        release.set();self.assertEqual(await task,"copied")
        self.assertEqual(len(submissions),1);self.assertFalse(task.cancelled())

    async def test_stop_while_waiting_for_submit_lock_does_not_call_factory(self):
        waiting,lock=asyncio.Event(),asyncio.Event();stop=False;factories=[]
        async def checkpoint():return object(),None
        async def dispatch(label,factory,**kwargs):waiting.set();await lock.wait();return factory()
        control=sampling.QuerySamplingControl(dispatch,checkpoint,lambda:"stop" if stop else None,
            query_limit=1,checkpoint_limit=2)
        task=asyncio.create_task(control.query("queued",lambda:factories.append(1)))
        await waiting.wait();stop=True;lock.set()
        with self.assertRaises(sampling.SamplingStopped):await task
        self.assertEqual(factories,[])

    async def test_checkpoint_terminal_and_budget_are_injected_not_game_policy(self):
        checkpoints=[];submissions=[]
        async def checkpoint():checkpoints.append(1);return "copied","opaque-boundary"
        async def dispatch(label,factory,**kwargs):submissions.append(1);return factory()
        control=sampling.QuerySamplingControl(dispatch,checkpoint,lambda:None,query_limit=1,checkpoint_limit=2)
        with self.assertRaisesRegex(sampling.SamplingStopped,"opaque-boundary"):
            await control.query("anything",lambda:False)
        self.assertEqual(submissions,[]);self.assertEqual(len(checkpoints),1)
