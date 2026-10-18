"""Country read work-count regressions. No live process, bridge or DLL."""
from types import SimpleNamespace as NS
import unittest

from pdx_runtime import v3_data as data, v3_objects as objects
from pdx_runtime.v3_game.country import CountryAPI, _Read
from tests.v3_game import test_v3_game_country as country_fixture
from tests.v3_game.test_v3_game_country import COUNTRY, TYPE_IDS, function, value, Query


class CountName:
    def __init__(self, row, count):
        self.row, self.count = row, count
    @property
    def name(self):
        self.count[0] += 1
        return self.row.name
    def __getattr__(self, name):
        return getattr(self.row, name)


class LinearMetadataAPI(CountryAPI):
    """Pre-index metadata lookup, independent of native query scheduling."""
    async def _function(self, read, receiver, type_id, getter, *, argument_count=0):
        rows = await self._functions(read, type_id)
        matches = [row for row in rows if row.name == getter]
        expression = f"{receiver}.{getter}"
        if len(matches) != 1:
            return None, f"{expression}: current native getter missing or ambiguous"
        result = matches[0]
        direct, scoped, action_direct, action_scoped = result.callbacks
        if (result.type_id != type_id or result.argument_count != argument_count
                or action_direct or action_scoped or bool(direct) == bool(scoped)):
            signature = "zero-argument" if argument_count == 0 else f"{argument_count}-argument"
            return None, f"{expression}: current metadata is not a {signature} read getter"
        return result, None


class CountryComplexityTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self):
        fixture = country_fixture.CountryTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    async def sample(self, states, buildings, metadata_rows, api_class=CountryAPI):
        fixture = self.fixture()
        fixture.api = api_class(fixture.ctx)
        fixture.pages = {
            objects.Domain.STATE: [[NS(error=0,resolved_id=13+i,owner_id=COUNTRY) for i in range(states)]],
            objects.Domain.BUILDING: [[NS(error=0,resolved_id=100+i,state_id=13+i%states,levels=i)
                                      for i in range(buildings)]]}
        fixture.metadata_page_size = 256
        count = [0]
        for kind in ("State", "Building", "BuildingType"):
            rows = fixture.metadata[TYPE_IDS[kind]]
            rows.extend(function(kind,f"Unused_{i}") for i in range(metadata_rows-len(rows)))
            fixture.metadata[TYPE_IDS[kind]] = [CountName(row,count) for row in rows]
        result = await fixture.api.buildings()
        fixture.assert_clean()
        return result, count[0], fixture

    async def test_metadata_index_is_linear_once_per_receiver_not_per_building(self):
        for states,buildings,metadata in ((2,8,20),(4,16,20),(2,8,200),(20,80,200)):
            with self.subTest(states=states,buildings=buildings,metadata=metadata):
                old,old_count,_ = await self.sample(states,buildings,metadata,LinearMetadataAPI)
                rows,count,fixture = await self.sample(states,buildings,metadata)
                self.assertEqual(rows,old)
                self.assertEqual(old_count,(2*states+3*buildings)*metadata)
                self.assertEqual(count,2*metadata)
                self.assertEqual(len(fixture.ctx.type_names),2)
                self.assertEqual(fixture.ctx.type_names.count("BuildingType"),0)

    async def test_native_reads_and_releases_remain_but_refresh_is_not_per_field(self):
        for states,buildings in ((2,8),(4,16),(20,80)):
            with self.subTest(states=states,buildings=buildings):
                rows,_,fixture = await self.sample(states,buildings,20)
                self.assertEqual(len(rows),buildings)
                self.assertEqual(len(fixture.evaluated),states+2*buildings)
                self.assertEqual(len(fixture.ctx.queries),4+2*(states+2*buildings))
                self.assertEqual(fixture.ctx.refresh_count,1)
                self.assertEqual(len(fixture.released),states+2*buildings)
                self.assertEqual(rows[0]["levels"],0)

    async def test_duplicate_missing_and_wrong_signature_name_metadata_keep_reasons(self):
        for metadata in (
                [function("State","GetName"),function("State","GetName")],
                [function("State","Other")],
                [function("State","GetName",arguments=1)]):
            fixture=self.fixture()
            fixture.metadata[TYPE_IDS["State"]]=metadata
            rows=await fixture.api.buildings()
            self.assertTrue(all(row["state_name"] is None for row in rows))
            self.assertTrue(all("GetName" in row["reasons"]["state_name"] for row in rows))
            self.assertFalse(any(expr=="State.GetName" for expr,*_ in fixture.evaluated))
            fixture.assert_clean()

    async def test_world_observation_invalidates_only_metadata_and_keeps_native_owners(self):
        fixture=self.fixture()
        read=_Read(fixture.api)
        read.functions[117]=["old"]
        read.function_names[117]={"old":["old"]}
        read.type_ids["Building"]=117
        observed=NS(error=0,world=read.world+1,value_handle=777,value="copied")
        result=await read.query("objects",lambda:Query(observed))
        self.assertIs(result,observed)
        self.assertEqual(read.owners,[777])
        self.assertEqual((read.functions,read.function_names,read.type_ids),({},{},{}))
        self.assertEqual(fixture.ctx.refresh_count,0)
        await read.check_world()
        self.assertEqual(read.world,observed.world)
        self.assertEqual(len(fixture.ctx.diagnostics),1)
        self.assertEqual(fixture.ctx.diagnostics[0]["event"],"world_changed")
        await read.close()
        self.assertEqual(fixture.released,[777])

    async def test_observed_context_change_does_not_discard_completed_name(self):
        fixture=self.fixture()
        def changed(handle,binding):
            fixture.ctx.world += 1
            return value(data.Kind.STRING,handle=handle,text="actual copied name")
        fixture.handlers["Building.GetName"]=changed
        rows=await fixture.api.buildings()
        self.assertTrue(all(row["name"]=="actual copied name" for row in rows))
        self.assertTrue(all(row["key"]=="building_iron_mine" for row in rows))
        self.assertEqual(fixture.ctx.refresh_count,1)
        self.assertEqual(set(fixture.released),{item[2] for item in fixture.evaluated})
        self.assertTrue(any(row.get("event")=="world_changed" for row in fixture.ctx.diagnostics))

    async def test_long_text_still_resnapshots_the_original_any_not_the_getter(self):
        fixture=self.fixture()
        fixture.handlers["Building.GetName"]=lambda handle,binding:value(
            data.Kind.STRING,handle=handle,text="partial",error=data.ERROR_MORE_DATA)
        original=fixture.handlers["Building.GetName"]
        from dataclasses import replace
        fixture.handlers["Building.GetName"]=lambda handle,binding:replace(
            original(handle,binding),total=9000)
        rows=await fixture.api.buildings()
        self.assertTrue(all(row["name"]=="完整原生名称" for row in rows))
        names=[entry for entry in fixture.evaluated if entry[0]=="Building.GetName"]
        self.assertEqual(len(names),2)
        self.assertEqual(fixture.snapshots,[(entry[2],9000) for entry in names])
        fixture.assert_clean()

    async def test_copied_native_values_are_not_rejected_by_redundant_metadata_receipts(self):
        fixture=self.fixture()
        fixture.handlers["Country.GetName"]=lambda handle,binding:value(
            data.Kind.STRING,handle=handle,type_id=4242,text="actual native name")
        fixture.handlers["Country.GetGDP"]=lambda handle,binding:value(
            data.Kind.FIXED,handle=handle,type_id=4243,signed=12345678,native_type_name="native alias")
        row=await fixture.api.summary()
        from decimal import Decimal
        self.assertEqual(row["name"],"actual native name")
        self.assertEqual(row["gdp"],Decimal("123.45678"))
        self.assertNotIn("CFixedPoint",fixture.ctx.type_names)
        fixture.assert_clean()

    async def test_new_read_refreshes_name_metadata_instead_of_global_stale_index(self):
        fixture=self.fixture()
        first=await fixture.api.buildings()
        self.assertTrue(all(row["state_name"] for row in first))
        fixture.metadata[TYPE_IDS["State"]]=[function("State","Other")]
        second=await fixture.api.buildings()
        self.assertTrue(all(row["state_name"] is None for row in second))
        self.assertTrue(all(row["key"] for row in second))
        self.assertEqual(fixture.ctx.type_names.count("State"),2)
        self.assertEqual(fixture.ctx.refresh_count,2)


if __name__ == "__main__":
    unittest.main()
