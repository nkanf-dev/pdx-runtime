"""CPU-only high-level politics seams. No PID/DLL/game calls.

Native results are fixtures. The real domain methods, native command codec,
metadata pagination, key joins, cleanup, and core pending coroutine run here.
These tests do not claim gameplay acceptance or native law eligibility.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace as NS
from decimal import Decimal, localcontext
import unittest
from unittest.mock import patch

from pdx_runtime import v3_commands as commands, v3_data as data
from pdx_runtime.v3_game import politics
from pdx_runtime.v3_game import _run as run_mod
from pdx_runtime.v3_game._core import GameContext, PendingOperation


class Query:
    serial = 0
    def __init__(self, result, delay=0):
        Query.serial += 1
        self.request_id = Query.serial
        self.result, self.delay = result, delay
        self.reads = 0
    def read(self):
        self.reads += 1
        if self.delay:
            self.delay -= 1
            return None
        return self.result


class Memory:
    def __init__(self):
        self.next = 0x10000
        self.blocks = {}
    def allocate(self, size):
        value = self.next
        self.next += 0x100
        self.blocks[value] = bytearray(size)
        return value
    def write(self, address, value):
        self.blocks[address][:len(value)] = value
    def read(self, address, size):
        return bytes(self.blocks[address][:size])
    def free(self, address):
        del self.blocks[address]
    def resolve_export(self, module, name, **kwargs):
        if name == "PdxV3DataProjectNativeFieldsInterface":
            return 0x45
        assert name == "memcpy"
        return 0x44


class Fixture:
    world = 0x50000
    actor = 0x02000001
    def __init__(self):
        self.memory = Memory()
        self.handles, self.origins, self.bound = {}, {}, {}
        self.next_handle, self.next_pointer = 1, 0x80000
        self.events, self.released, self.owners, self.submitted = [], [], [], []
        self.enacting = None
        self.law_ids = [0x03000002, 0x04000003]
        self.active_law_ids = {self.law_ids[0]}
        self.law_names = dict(zip(self.law_ids, ("Serfdom", "Workers' Protections")))
        self.enactment_progress = 125000
        self.cannot_enact = {full_id: "" for full_id in self.law_ids}
        self.law_detail_calls=[]
        self.law_detail_fields={full_id:{
            "GetPercentPolStrApprove":data.FixedPoint(28000),"GetPercentPolStrDisapprove":data.FixedPoint(61000),
            "GetPercentPolStrUndecided":data.FixedPoint(11000),
            "GetIGsApproveList":"Original approving IG list", "GetIGsDisapproveList":"Original opposing IG list",
            "GetIGsUndecidedList":"Original neutral IG list", "GetCheckpointSuccessChance":data.FixedPoint(23000),
            "GetCheckpointAdvanceChance":data.FixedPoint(17000),"GetCheckpointDebateChance":data.FixedPoint(41000),
            "GetCheckpointStallChance":data.FixedPoint(19000), "GetCheckpointSuccessBreakdown":"Original success factors",
            "GetCheckpointAdvanceBreakdown":"Original advance factors", "GetCheckpointDebateBreakdown":"Original debate factors",
            "GetCheckpointStallBreakdown":"Original stall factors"} for full_id in self.law_ids}
        self.institution_id = 0  # Zero is a valid full ID, not missing.
        self.institution_level, self.institution_target = 1, 0
        self.movement = {"id":0x06000004, "bolstered":False, "suppressed":False,
            "support":data.FixedPoint(23000),"radicalism":data.FixedPoint(67000),"individuals":3500000000,"insurrectionary":False,"obstinance":False}
        self.character_id=0x09000008
        self.character_fields={"IsValid":True,"GetID":self.character_id,"GetFullNameNoFormatting":"Fixture Agitator",
            "GetPopularity":data.FixedPoint(1250000),"GetPopularityDesc":"Original popularity effects", "GetAllRoleNames":"Agitator",
            "IsAgitator":True,"IsIGLeader":False}
        self.character_bindings=[]
        self.active_decree = None
        self.ig_id = 0x07000005
        self.ig_fields = {self.ig_id:{"GetClout":data.FixedPoint(26500),"GetApprovalValue":-3,"IsInGovernment":True}}
        self.reject = set()
        self.delay_submit = 0
        self.bad_definition = False
        self.bad_actor = False
        self.lists = {
            "Country.AccessActivePowerStructureLaws":[("Law", self.law_ids[0])],
            "Country.AccessActiveEconomyLaws":[],
            "Country.AccessActiveHumanRightsLaws":[],
            "Country.AccessActiveOtherLaws":[],
            "Country.AccessLawsWithGovernmentOrMovementSupport":[("Law", self.law_ids[0])],
            "Country.AccessLawsWithoutGovernmentOrMovementSupport":[("Law", self.law_ids[1])],
            "Country.AccessInstitutions":[("Institution", self.institution_id)],
            "Country.AccessPoliticalMovements":[("PoliticalMovement", self.movement["id"])],
            "Country.AccessAllInterestGroups":[("InterestGroup", self.ig_id)],
            "PoliticalMovement.GetSupportingCharacters":[("Character", self.character_id)],
            "PoliticalMovement.GetAffectedLawsSuccess":[("Law", self.law_ids[1])],
            "PoliticalMovement.GetAffectedLawsStall":[("Law", self.law_ids[0])],
            "State.GetPotentialDecrees":[("DecreeType", "decree_road_maintenance")],
        }
        self.definitions = [NS(pointer=0xA0000+i*0x100, key=k, group_key="lawgroup_labor",
            error=0, key_error=0, group_error=0, group_key_error=0)
            for i,k in enumerate(("law_serfdom", "law_workers_protections"))]

    def result(self, value, *, action=data.Action.EVALUATE, handle=True, kind=None):
        h = self.next_handle if handle else 0
        if handle:
            self.next_handle += 1
            self.handles[h] = value
        return NS(error=0, action=action, value_handle=h, value=value, kind=kind)

    def scalar(self, value):
        kind = data.Kind.BOOL if type(value) is bool else data.Kind.FIXED if isinstance(value, data.FixedPoint) else data.Kind.I32 if type(value) is int else data.Kind.STRING if isinstance(value,str) else None
        result=self.result(value,kind=kind)
        if isinstance(value,str):
            result.snapshot=value.encode("utf-8");result.snapshot_bytes=len(result.snapshot);result.total=len(result.snapshot)
        return result

    def wrapper(self, name, origin, *, borrowed=False):
        ptr = self.next_pointer
        self.next_pointer += 0x100
        native_name = ((".?AU?$SAnyType@PEBVCData" + name + "@@@PdxAnyImpl@@") if borrowed else
            (".?AU?$SAnyType@V?$CPdxPtr@VCData" + name + "@@@@@PdxAnyImpl@@"))
        result = self.result(None, action=data.Action.LIST_ELEMENT)
        native_type_id = next(type_id for type_id, type_name in self.ctx.type_names.items() if type_name == name)
        result.native_type_name, result.type_id = native_name, native_type_id
        value = data.NativeObject(ptr, native_type_id, result.value_handle, native_name)
        result.value = value
        self.origins[ptr+0x10] = origin
        self.bound[result.value_handle] = (name, origin)
        return result

    def evaluate(self, native, expression, bindings, **kwargs):
        self.events.append(expression)
        if expression in self.lists:
            return self.list_result(self.lists[expression])
        if expression == "State.AccessDecrees":
            return self.list_result([] if self.active_decree is None else [("Decree", self.active_decree)])
        binding = bindings[0] if bindings else None
        origin = self.bound.get(getattr(binding, "object", None), (None, None))[1]
        if expression.startswith("InterestGroup."):
            return Query(self.scalar(self.ig_fields[origin][expression.split(".")[1]]))
        if expression == "Law.GetLawType":
            definition = self.definitions[self.law_ids.index(origin)]
            return Query(self.wrapper("LawType", 0xBAD if self.bad_definition else definition.pointer))
        if expression == "Law.GetName":
            return Query(self.scalar(self.law_names[origin]))
        if expression == "Law.IsActive":
            return Query(self.scalar(origin in self.active_law_ids))
        if expression == "Law.IsBeingEnacted":
            return Query(self.scalar(origin == self.enacting))
        if expression.startswith("Law.") and expression[len("Law."):] in self.law_detail_fields.get(origin,{}):
            self.law_detail_calls.append((origin,expression,binding))
            return Query(self.scalar(self.law_detail_fields[origin][expression[len("Law."):]]))
        if expression == "Law.StartEnacting":
            return self.producer(22275, {11018:commands.UInt32(origin)})
        if expression == "Country.GetLawBeingEnacted.StopEnacting":
            law_id = 0xffffffff if self.enacting is None else self.enacting
            return self.producer(22276, {11018:commands.UInt32(law_id)})
        if expression == "Country.GetLawBeingEnacted.GetLawType":
            return Query(self.wrapper("LawType", self.definitions[self.law_ids.index(self.enacting)].pointer))
        if expression == "Country.IsEnactingLaw":
            return Query(self.scalar(self.enacting is not None))
        if expression == "Country.GetLawEnactmentProgress":
            return Query(self.scalar(data.FixedPoint(self.enactment_progress)))
        if expression.startswith("Institution.StartChangingLevelTo(") and expression.endswith(")"):
            level = int(expression[len("Institution.StartChangingLevelTo("):-1])
            return self.producer(20288, {20286:commands.UInt32(origin),1875:level})
        institution = {"GetName":"Education", "GetInvestmentLevel":self.institution_level,
            "GetTargetInvestmentLevel":self.institution_target,
            "IsChangingLevel":self.institution_target not in (0,self.institution_level),
            "GetMaxLevel":5, "GetInvestmentCost":data.FixedPoint(2000000),
            "GetTimeToReachTargetLevelDesc":"12 months"}
        if expression.startswith("Institution."):
            return Query(self.scalar(institution[expression.split(".")[1]]))
        if expression in ("PoliticalMovement.ToggleBolstering", "PoliticalMovement.ToggleSuppression"):
            token = 24477 if expression.endswith("ToggleBolstering") else 24476
            return self.producer(token, {12458:commands.UInt32(origin)})
        movement = {"GetName":"Liberal Movement", "IsBolstered":self.movement["bolstered"],
            "IsSuppressed":self.movement["suppressed"],"GetSupport":self.movement["support"],
            "GetRadicalism":self.movement["radicalism"],"GetNumSupportingIndividuals":self.movement["individuals"],
            "IsInsurrectionary":self.movement["insurrectionary"],"IsCausingObstinance":self.movement["obstinance"],
            "GetCoreIdeology.GetName":"Land Reform", "GetCoreIdeology.GetDesc":"Original political ideology description",
            "GetCoreIdeology.GetLawStanceDesc":"Original endorsed and opposed laws"}
        if expression.startswith("PoliticalMovement."):
            result=self.scalar(movement[expression[len("PoliticalMovement."):]])
            if expression=="PoliticalMovement.GetNumSupportingIndividuals":result.kind=data.Kind.I64
            return Query(result)
        if expression.startswith("Character."):
            self.character_bindings.append(binding)
            suffix=expression[len("Character."):]
            if suffix=="GetIdeology.GetName":return Query(self.scalar("Fixture Reformist"))
            if suffix=="GetInterestGroup.GetName":return Query(self.scalar("Fixture Rural Folk"))
            return Query(self.scalar(self.character_fields[suffix]))
        if expression == "GetDecreeType('decree_road_maintenance').GetSetCommand(State.Self)":
            return self.producer(22673, {439:commands.UInt32(bindings[0].object),22671:"decree_road_maintenance"})
        if expression == "DecreeType.GetName":
            return Query(self.scalar("Road Maintenance"))
        if expression == "Decree.GetType":
            return Query(self.wrapper("DecreeType", self.decree_pointer, borrowed=True))
        if expression == "Decree.GetRemoveCommand":
            return self.producer(22672, {22671:commands.UInt32(origin)})
        raise AssertionError("unexpected native expression " + expression)

    decree_pointer = 0xC0000
    def list_result(self, rows):
        result = self.result(data.NativeList(0, 0, b"", "fixture"))
        self.handles[result.value_handle] = rows
        return Query(result)

    def list_count(self, native, handle, **kwargs):
        return Query(NS(error=0, total=len(self.handles[handle])))

    def list_element(self, native, handle, index, **kwargs):
        name, origin = self.handles[handle][index]
        if name == "DecreeType":
            result = self.wrapper(name, self.decree_pointer)
            self.bound[result.value_handle] = (name, origin)
        else:
            result = self.wrapper(name, origin)
        return Query(result)

    def release(self, native, handle, **kwargs):
        self.released.append(handle)
        self.handles.pop(handle)
        return Query(NS(error=0, value_handle=0, action=data.Action.RELEASE))

    def submit_call(self, native, target, args, **kwargs):
        output, source, size = args
        self.memory.write(output, int(self.origins[source]).to_bytes(size, "little"))
        return Query(NS(error=0, callee_result=NS(error=0, rax=output)))

    def enumerate_functions(self, native, type_id, start, **kwargs):
        name = self.ctx.type_names[type_id]
        names = {"Law":["GetName", "IsActive", "IsBeingEnacted",*self.law_detail_fields[self.law_ids[0]]], "Country":["GetLawEnactmentProgress"],
            "InterestGroup":["GetClout","GetApprovalValue","IsInGovernment","GetName"],
            "PoliticalMovement":["GetSupport","GetRadicalism","GetNumSupportingIndividuals","IsInsurrectionary","IsCausingObstinance",
                "GetCoreIdeology","GetSupportingCharacters","GetAffectedLawsSuccess","GetAffectedLawsStall"],
            "Character":["IsValid","GetID","GetFullNameNoFormatting","GetIdeology","GetInterestGroup","GetPopularity",
                "GetPopularityDesc","GetAllRoleNames","IsAgitator","IsIGLeader"],
            "Ideology":["GetName","GetDesc","GetLawStanceDesc"],
            "CharacterIdeologyAlias":["GetName"],"CharacterIGAlias":["GetName"],
            "Institution":["GetMaxLevel", "GetInvestmentCost"], "DecreeType":["GetName"]}.get(name, [])
        type_ids={value:key for key,value in self.ctx.type_names.items()}
        returns={"GetClout":type_ids["CFixedPoint"],"GetApprovalValue":type_ids["int32"],"IsInGovernment":type_ids["bool"],
            "GetSupport":type_ids["CFixedPoint"],"GetRadicalism":type_ids["CFixedPoint"],"GetNumSupportingIndividuals":type_ids["int64"],
            "IsInsurrectionary":type_ids["bool"],"IsCausingObstinance":type_ids["bool"],"GetCoreIdeology":type_ids["Ideology"],
            "IsValid":type_ids["bool"],"GetID":type_ids["int32"],"GetFullNameNoFormatting":type_ids["CString"],
            "GetName":type_ids["CString"],"GetDesc":type_ids["CString"],"GetLawStanceDesc":type_ids["CString"],
            "GetIdeology":type_ids["CharacterIdeologyAlias"],"GetInterestGroup":type_ids["CharacterIGAlias"],
            "GetPopularity":type_ids["CFixedPoint"],"GetPopularityDesc":type_ids["CString"],"GetAllRoleNames":type_ids["CString"],
            "IsAgitator":type_ids["bool"],"IsIGLeader":type_ids["bool"],
            "IsActive":type_ids["bool"],"IsBeingEnacted":type_ids["bool"]}
        for getter,value in self.law_detail_fields[self.law_ids[0]].items():
            returns[getter]=type_ids["CFixedPoint"] if isinstance(value,data.FixedPoint) else type_ids["CString"]
        rows = [NS(name=n, argument_count=0, callbacks=(1,0,0,0),return_type_id=returns.get(n,0)) for n in names]
        # Force automatic metadata pagination, including sparse page indices.
        split = 1
        page_rows = rows[:split] if start == 0 else rows[split:]
        next_index = 1 if start == 0 and len(rows)>split else 2
        return Query(NS(error=0, rows=page_rows, next=next_index, total=2))

    def enumerate_law_definitions(self, native, start_index, **kwargs):
        return Query(NS(error=0, world=self.world, records=(self.definitions[start_index],),
            next_index=start_index+1, slot_bound=2))

    def enumerate_interest_group_details(self, native, start_index, **kwargs):
        row = NS(error=0,country_error=0,resolved_id=0x07000005,country_id=self.actor,
                 name="Landowners")
        foreign = NS(error=0,country_error=0,resolved_id=0x07000006,country_id=99,name="Foreign")
        return Query(NS(error=0, world=self.world, records=(row if start_index==0 else foreign,),
                        next_index=start_index+1,slot_bound=2))

    def read_ids(self,native,domain,ids,**kwargs):
        assert domain==politics.objects.Domain.CHARACTER
        rows=[NS(error=0,requested_id=full_id,resolved_id=full_id) for full_id in ids]
        return Query(NS(error=0,world=self.world,records=rows))

    def producer(self, token, params):
        pointer = self.next_pointer
        self.next_pointer += 0x100
        self.bound[pointer] = (token, params)
        return Query(self.result(data.NativeDataCommand(pointer, pointer+0x10, pointer+0x18,
                                                       0, "CDataCommand", self.world)))

    def command_value(self, native, handle, **kwargs):
        return Query(NS(error=0, action=data.Action.COMMAND_VALUE, value=self.handles[handle]))

    def request(self, native, action, command, world, **kwargs):
        assert action == commands.Action.CLONE
        token, params = self.bound[command]
        query = self.created(token, params, command=command+0x80)
        query.result.action = commands.Action.CLONE
        query.result.cloned_command = command+0x80
        return query

    def create(self, native, token, actor_id, parameters, **kwargs):
        assert actor_id == self.actor
        return self.created(token, parameters)

    def created(self, token, params, command=0xD0000):
        owner = Command(self, token, params, command)
        self.owners.append(owner)
        return Query(NS(error=0, owner=owner, action=commands.Action.DESERIALIZE))

    def project_page(self, native, fields, binding_rows, **kwargs):
        self.events.extend(fields)
        source = kwargs.get("source_expression")
        if source in ("State.GetPotentialDecrees", "State.AccessDecrees"):
            rows = self.lists[source] if source == "State.GetPotentialDecrees" else (
                [] if self.active_decree is None else [("Decree", self.active_decree)])
            start, limit = kwargs.get("start", 0), kwargs.get("limit", 64)
            selected, cells = rows[start:start+limit], []
            for row_index, (_, origin) in enumerate(selected):
                for column, expression in enumerate(fields):
                    if isinstance(expression, data.NativeField):
                        if expression.kind == data.NativeFieldKind.DECREE_ID:
                            assert source == "State.AccessDecrees" and not expression.receiver_expression
                            value, kind = origin, data.Kind.U32
                        else:
                            assert expression.kind == data.NativeFieldKind.DECREE_TYPE_KEY
                            assert expression.receiver_expression == ("Decree.GetType" if source == "State.AccessDecrees" else "")
                            value, kind = (origin if source == "State.GetPotentialDecrees" else "decree_road_maintenance"), data.Kind.STRING
                    else:
                        assert expression == "DecreeType.GetName"
                        value, kind = "Road Maintenance", data.Kind.STRING
                    snapshot = value.encode("utf-8") if kind == data.Kind.STRING else b""
                    cells.append(data.ProjectionCell(row_index, column, 0, kind,
                        -1 if isinstance(expression, data.NativeField) else 12,
                        value if kind == data.Kind.U32 else 0, snapshot, len(snapshot)))
            query = Query(data.ProjectionPage(0, self.world, len(fields), start, 0x1000,
                len(rows), True, len(selected), tuple(cells)))
            query.cleanup_error = None
            return query
        cells = []
        for row_index, bindings in enumerate(binding_rows):
            law_id = self.bound[bindings[0].object][1]
            for column, expression in enumerate(fields):
                if expression != "Law.CannotEnactDesc":
                    raise AssertionError(expression)
                value = self.cannot_enact[law_id]
                cells.append(value if isinstance(value, data.ProjectionCell) else
                    data.ProjectionCell(row_index, column, 0, data.Kind.STRING, 9, 0,
                                        value.encode("utf-8"), len(value.encode("utf-8"))))
        result = data.ProjectionPage(0, 0x1000, len(fields), 0, self.world,
                                    len(binding_rows), True, len(binding_rows), tuple(cells))
        query = Query(result)
        query.cleanup_error = None
        return query

    def patches(self):
        return [patch.object(politics.data, name, getattr(self, name)) for name in
                ("evaluate", "list_count", "list_element", "release", "enumerate_functions", "command_value", "project_page")] + [
            patch.object(politics.access, "submit_call", self.submit_call),
            patch.object(politics.objects,"enumerate_law_definitions", self.enumerate_law_definitions),
            patch.object(politics.objects,"enumerate_interest_group_details", self.enumerate_interest_group_details),
            patch.object(politics.objects,"read_ids", self.read_ids),
            patch.object(politics.commands,"request",self.request),
            patch.object(politics.commands,"create",self.create),
            patch.object(politics.commands.NativeCommand,"from_result",lambda native,result,**kw:result.owner)]


class Command:
    def __init__(self, fixture, token, params, command):
        self.f, self.type_token, self.params = fixture, token, params
        self.command, self.world, self.pending = command, fixture.world, None
        self.attempt = None
        self.operation = None
    def enqueue(self, action, result, delay=0):
        assert self.pending is None
        self.operation = action
        self.pending = Query(result, delay)
        return self.pending
    def complete(self):
        value = self.pending.read()
        if value is not None:
            self.pending = None
            if self.operation in ("destroy","submit"):
                self.command = 0
        return value
    def serialize(self):
        actor = self.f.actor+1 if self.f.bad_actor else self.f.actor
        wire = commands.encode_command(self.type_token, {10304:commands.UInt32(actor),65:self.params})
        return self.enqueue("serialize", NS(error=0, action=commands.Action.SERIALIZE, data=wire))
    def validate(self, capacity):
        legal = self.type_token not in self.f.reject
        return self.enqueue("validate", NS(error=0, action=commands.Action.VALIDATE, validated=legal,
            reason="" if legal else "Not enough bureaucracy",reason_truncated=False))
    def destroy(self):
        self.f.events.append("destroy:"+str(self.type_token))
        return self.enqueue("destroy", NS(error=0,action=commands.Action.DESTROY, command=0))
    def submit(self, flags):
        assert flags == 4
        self.f.submitted.append((self.type_token,self.params))
        if self.type_token == 22275:
            self.f.enacting = self.params[11018].value
        elif self.type_token == 22276:
            self.f.enacting = None
        elif self.type_token == 20288:
            self.f.institution_target = self.params[1875]
        elif self.type_token in (24477,24476):
            flag = "bolstered" if self.type_token==24477 else "suppressed"
            self.f.movement[flag] = not self.f.movement[flag]
        elif self.type_token == 22673:
            self.f.active_decree = 0x08000007
        elif self.type_token == 22672:
            self.f.active_decree = None
        return self.enqueue("submit",NS(error=0,action=commands.Action.SUBMIT,command=0,accepted=True), self.f.delay_submit)


class Context(GameContext):
    def __init__(self, fixture):
        native = NS(pid=123)
        super().__init__(NS(native=native, namespace={}), fixture.memory, timeout=1,poll_interval=0)
        self.fixture, fixture.ctx = fixture, self
        self.world = fixture.world
        self.type_names = {i:name for i,name in enumerate(("Country","Law","LawType","Institution","PoliticalMovement","State","DecreeType","Decree","InterestGroup","CFixedPoint","int32","bool","CString","int64","Character","Ideology","CharacterIdeologyAlias","CharacterIGAlias"))}
        self.created_queries = []
    async def refresh(self):
        return NS(world=self.world)
    async def country_id(self):
        return self.fixture.actor
    async def type_id(self,name):
        return next(k for k,v in self.type_names.items() if v==name)
    async def query(self,label,factory,*,owner=None):
        q = factory()
        self.created_queries.append(q)
        return await self.wait(q, owner=owner)


class PoliticsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = Fixture()
        self.ctx = Context(self.fixture)
        self.api = politics.PoliticsAPI(self.ctx)
        self.patchers = self.fixture.patches()
        for patcher in self.patchers:
            patcher.start()
    async def asyncTearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
    def cleaned(self):
        self.assertFalse(self.fixture.handles)
        self.assertFalse(self.fixture.memory.blocks)
        self.assertTrue(all(not owner.command for owner in self.fixture.owners))
        self.assertFalse(self.api._retained)

    async def test_active_laws_native_key_join_and_auto_pages(self):
        row, = await self.api.laws()
        expected = dict(id=self.fixture.law_ids[0], key="law_serfdom", group="lawgroup_labor",name="Serfdom",name_raw="Serfdom",active=True,enacting=False,status="active")
        self.assertEqual({name: row[name] for name in expected}, expected)
        self.assertTrue(row["identity_complete"])
        self.assertIs(row["definition"]["record"], self.fixture.definitions[0])
        self.assertFalse(self.fixture.submitted)
        self.cleaned()

    async def test_rich_law_names_preserve_raw_and_do_not_format_keys(self):
        raw = "\x15tooltippable_name \x15tooltip:dw_law,AAAA,LawTooltip 农奴制\x15!\x15!"
        self.fixture.law_names[self.fixture.law_ids[0]] = raw
        self.fixture.definitions[0].key = "#BOLD law_serfdom#!"
        row, = await self.api.laws()
        self.assertEqual(row["name"], "农奴制")
        self.assertEqual(row["name_raw"], raw)
        self.assertEqual(self.fixture.law_names[row["id"]], raw)
        self.assertEqual(row["key"], "#BOLD law_serfdom#!")
        self.assertEqual(row["group"], "lawgroup_labor")
        self.assertTrue(politics._matches(row, "农奴制"))
        self.assertTrue(politics._matches(row, raw))
        self.assertTrue(row["active"])
        self.assertFalse(row["enacting"])
        self.cleaned()

    async def test_missing_law_name_keeps_none_and_reason(self):
        original = self.fixture.enumerate_functions
        def functions(*args, **kwargs):
            query = original(*args, **kwargs)
            query.result.rows = [row for row in query.result.rows if row.name != "GetName"]
            return query
        with patch.object(data, "enumerate_functions", functions):
            row, = await self.api.laws()
        self.assertIsNone(row["name"])
        self.assertIsNone(row["name_raw"])
        self.assertEqual(row["name_reason"], "Native law API does not provide a display name")
        self.assertTrue(row["active"])
        self.cleaned()

    async def test_nearby_political_names_and_details_keep_exact_original_text(self):
        original = self.fixture.evaluate
        captured = {}
        def evaluate(native, expression, bindings, **kwargs):
            query = original(native, expression, bindings, **kwargs)
            if isinstance(query.result.value, str) and any(word in expression for word in ("GetName", "GetFullName", "GetDesc", "GetLawStanceDesc")):
                raw = "\x15B " + query.result.value + "\x15!"
                captured[expression] = raw
                query.result.value = raw
                query.result.snapshot = raw.encode("utf-8")
                query.result.snapshot_bytes = query.result.total = len(query.result.snapshot)
            return query
        original_page = self.fixture.project_page
        def project_page(native, fields, binding_rows, **kwargs):
            query = original_page(native, fields, binding_rows, **kwargs)
            if kwargs.get("source_expression") == "State.GetPotentialDecrees":
                from dataclasses import replace
                cells = []
                for cell in query.result.cells:
                    expression = fields[cell.field]
                    if expression == "DecreeType.GetName":
                        raw = "\x15B " + cell.value + "\x15!"
                        captured[expression] = raw
                        cell = replace(cell, snapshot=raw.encode("utf-8"), total=len(raw.encode("utf-8")))
                    cells.append(cell)
                query.result = replace(query.result, cells=tuple(cells))
            return query
        with patch.object(data, "evaluate", evaluate), patch.object(data, "project_page", project_page):
            institution, = await self.api.institutions()
            movement, = await self.api.political_movements(details=True)
            decree, = await self.api.decree_options(8)
        for row, expression, expected in (
                (institution, "Institution.GetName", "Education"),
                (movement, "PoliticalMovement.GetName", "Liberal Movement"),
                (decree, "DecreeType.GetName", "Road Maintenance"),
                (movement["core_ideology"], "PoliticalMovement.GetCoreIdeology.GetName", "Land Reform"),
                (movement["supporting_characters"][0], "Character.GetFullNameNoFormatting", "Fixture Agitator")):
            with self.subTest(expression=expression):
                self.assertEqual(row["name"], expected)
                self.assertEqual(row["name_raw"], captured[expression])
        character = movement["supporting_characters"][0]
        self.assertEqual(character["ideology"], "Fixture Reformist")
        self.assertEqual(character["ideology_raw"], captured["Character.GetIdeology.GetName"])
        self.assertEqual(institution["id"], 0)
        self.assertEqual(institution["target_level"], 0)
        self.assertIsNone(institution["key"])
        self.assertEqual(decree["key"], "decree_road_maintenance")
        self.assertFalse(self.fixture.submitted)
        self.cleaned()

    async def test_interest_group_display_does_not_modify_native_snapshot(self):
        from pdx_runtime import v3_objects as objects
        raw = "#tooltippable_name #tooltip:dw_ig,AAAA 士大夫#!#!"
        native = NS(error=0, country_error=0, resolved_id=self.fixture.ig_id,
                    country_id=self.fixture.actor, name=raw)
        page = Query(NS(error=0, world=self.fixture.world, records=[native], next_index=1, slot_bound=1))
        with patch.object(objects, "enumerate_interest_group_details", return_value=page):
            row, = await self.api.interest_groups()
        self.assertEqual(row["name"], "士大夫")
        self.assertEqual(row["name_raw"], raw)
        self.assertEqual(native.name, raw)
        self.assertIsNone(row["key"])
        self.cleaned()

    def police_panel(self):
        old, proposed = self.fixture.law_ids
        self.fixture.definitions[0].key = "law_no_police"
        self.fixture.definitions[1].key = "law_local_police"
        for definition in self.fixture.definitions:
            definition.group_key = "lawgroup_policing"
        self.fixture.law_names = {old:"No Police", proposed:"Local Police Force"}
        # This is the actual native panel shape: same law group includes the
        # current in-force law AND a distinct proposed replacement reference.
        self.fixture.lists["Country.AccessActiveOtherLaws"] = [("Law",old),("Law",proposed)]
        self.fixture.active_law_ids = {old}
        self.fixture.enacting = proposed
        return old, proposed

    async def test_law_panel_current_and_enacting_same_group_are_not_both_active(self):
        old, proposed = self.police_panel()
        rows = await self.api.laws()
        self.assertEqual(len(rows),2)  # Duplicate full IDs across categories are still deduplicated.
        by_key = {row["key"]:row for row in rows}
        self.assertEqual((by_key["law_no_police"]["active"],by_key["law_no_police"]["enacting"],by_key["law_no_police"]["status"]),
                         (True,False,"active"))
        self.assertEqual((by_key["law_local_police"]["active"],by_key["law_local_police"]["enacting"],by_key["law_local_police"]["status"]),
                         (False,True,"enacting"))
        self.assertEqual(by_key["law_local_police"]["id"],proposed)
        self.assertFalse(self.fixture.submitted)
        self.assertFalse(self.fixture.owners)  # Classification generates no Start/Stop command.
        self.cleaned()

    async def test_law_progress_reset_or_completion_number_never_means_in_force(self):
        self.police_panel()
        for raw in (89520,32824,100000,0):
            self.fixture.enactment_progress = raw
            rows = await self.api.laws()
            proposed = next(row for row in rows if row["key"]=="law_local_police")
            self.assertFalse(proposed["active"])
            self.assertTrue(proposed["enacting"])
            self.assertEqual(proposed["status"],"enacting")
        self.assertNotIn("Country.GetLawEnactmentProgress",self.fixture.events)
        self.assertFalse(self.fixture.submitted)
        self.cleaned()

    async def test_law_current_switch_uses_native_flags_not_previous_source_membership(self):
        old, proposed = self.police_panel()
        self.fixture.active_law_ids = {proposed}
        self.fixture.enacting = None
        rows = await self.api.laws()
        by_id = {row["id"]:row for row in rows}
        self.assertEqual((by_id[old]["active"],by_id[old]["enacting"],by_id[old]["status"]),(False,False,"inactive"))
        self.assertEqual((by_id[proposed]["active"],by_id[proposed]["enacting"],by_id[proposed]["status"]),(True,False,"active"))
        self.cleaned()

    async def test_available_law_choice_is_not_marked_current_or_enacting(self):
        rows = await self.api.law_options()
        proposed = next(row for row in rows if row["id"]==self.fixture.law_ids[1])
        self.assertIsNone(proposed["enactable"])  # A read does not validate candidate commands.
        self.assertFalse(proposed["eligibility_checked"])
        self.assertFalse(proposed["active"])
        self.assertFalse(proposed["enacting"])
        self.assertEqual(proposed["status"],"inactive")
        self.assertFalse(self.fixture.submitted)
        self.cleaned()

    async def test_law_unknown_or_nonboolean_state_never_defaults_to_active(self):
        self.police_panel()
        original = self.fixture.evaluate
        def read(native,expression,bindings,**kwargs):
            if expression == "Law.IsActive":
                result = self.fixture.scalar(1)
                result.kind = data.Kind.I32
                return Query(result)
            return original(native,expression,bindings,**kwargs)
        with patch.object(politics.data,"evaluate",read):
            rows = await self.api.laws()
        self.assertTrue(all(row["active"] is None for row in rows))
        self.assertTrue(all("active" in row["state_reasons"] for row in rows))
        self.assertEqual(next(row for row in rows if row["key"]=="law_no_police")["status"],"unknown")
        self.cleaned()

    async def test_law_state_getter_native_error_is_unknown_not_active_or_candidate(self):
        self.police_panel()
        original = self.fixture.evaluate
        def read(native,expression,bindings,**kwargs):
            if expression == "Law.IsActive":
                result = self.fixture.scalar(None)
                result.error = 1168
                return Query(result)
            return original(native,expression,bindings,**kwargs)
        with patch.object(politics.data,"evaluate",read):
            rows = await self.api.laws()
        self.assertTrue(all(row["active"] is None for row in rows))
        self.assertTrue(all("1168" in row["state_reasons"]["active"] for row in rows))
        self.assertTrue(next(row for row in rows if row["key"]=="law_local_police")["enacting"])
        self.assertFalse(self.fixture.submitted)
        self.cleaned()

    async def test_action_metadata_cannot_supply_a_law_read_state(self):
        original = self.fixture.enumerate_functions
        def functions(*args,**kwargs):
            query = original(*args,**kwargs)
            for row in query.result.rows:
                if row.name == "IsActive":row.callbacks = (0,0,1,0)
            return query
        with patch.object(politics.data,"enumerate_functions",functions):
            rows = await self.api.laws()
        self.assertIsNone(rows[0]["active"])
        self.assertEqual(rows[0]["status"],"unknown")
        self.assertNotIn("Law.IsActive",self.fixture.events)
        self.cleaned()

    async def test_law_native_reason_survives_unrelated_localization_warning_without_validation(self):
        warning = ("culture_cu_equal: Scope dependent values in localization inside any trigger "
                   "at common/laws/01_trade_policy.txt:182")
        self.ctx.diagnostics.append({"native_log": warning})
        self.fixture.cannot_enact = {full_id: "Original native reason text" for full_id in self.fixture.law_ids}
        with patch.object(Command, "validate", side_effect=AssertionError("candidate validation")):
            rows = await self.api.law_options()
        self.assertTrue(all(row["enactable"] is None for row in rows))
        self.assertTrue(all(row["reason"] == "Original native reason text" for row in rows))
        self.assertFalse(self.fixture.submitted)
        self.cleaned()

    async def test_law_options_default_copies_context_without_candidate_commands(self):
        rows=await self.api.law_options()
        self.assertTrue(all("national_clout" not in row and "checkpoint" not in row for row in rows))
        self.assertEqual(self.fixture.events.count("Law.StartEnacting"),0)
        self.assertFalse(self.fixture.law_detail_calls)
        self.assertFalse(self.fixture.submitted)
        self.cleaned()

    async def test_law_options_details_read_original_candidate_clout_and_first_checkpoint(self):
        with localcontext() as context:
            context.prec=2
            rows=await self.api.law_options(details=True)
        row=next(row for row in rows if row["id"]==self.fixture.law_ids[1])
        national=row["national_clout"];checkpoint=row["checkpoint"]
        self.assertEqual(national["scope"],"national_interest_group_clout")
        self.assertEqual(national["approve_fraction"],Decimal("0.28"))
        self.assertEqual(national["disapprove_fraction"],Decimal("0.61"))
        self.assertEqual(national["undecided_fraction"],Decimal("0.11"))
        self.assertEqual(national["approve_igs_description"],"Original approving IG list")
        self.assertEqual(national["disapprove_igs_description"],"Original opposing IG list")
        self.assertEqual(national["undecided_igs_description"],"Original neutral IG list")
        self.assertEqual(checkpoint["scope"],"first_checkpoint_candidate")
        for field,value in (("success",Decimal("0.23")),("advance",Decimal("0.17")),("debate",Decimal("0.41")),("stall",Decimal("0.19"))):
            self.assertEqual(checkpoint[field+"_fraction"],value)
            self.assertEqual(checkpoint[field+"_breakdown"],"Original "+field+" factors")
        self.assertNotEqual(checkpoint["success_fraction"],national["approve_fraction"])
        self.assertEqual(national["units"],"fraction");self.assertEqual(checkpoint["units"],"fraction")
        self.assertEqual(self.fixture.events.count("Law.StartEnacting"),0)  # Copied reads only.
        self.assertNotIn("Law.Select",self.fixture.events)
        self.assertFalse(self.fixture.submitted);self.assertIsNone(self.fixture.enacting)
        self.assertTrue(all(binding.retained for _,_,binding in self.fixture.law_detail_calls))
        self.assertTrue(all(origin==self.fixture.law_ids[1] for origin,expression,_ in self.fixture.law_detail_calls if "GetCheckpoint" in expression))
        self.cleaned()

    async def test_law_options_details_zero_fractions_and_empty_native_text_are_real(self):
        self.fixture.law_detail_fields[self.fixture.law_ids[1]].update(
            GetPercentPolStrApprove=data.FixedPoint(0),GetIGsApproveList="",
            GetCheckpointSuccessChance=data.FixedPoint(0),GetCheckpointSuccessBreakdown="")
        row=(await self.api.law_options(details=True))[1]
        self.assertEqual(row["national_clout"]["approve_fraction"],Decimal(0))
        self.assertEqual(row["national_clout"]["approve_igs_description"],"")
        self.assertEqual(row["checkpoint"]["success_fraction"],Decimal(0))
        self.assertEqual(row["checkpoint"]["success_breakdown"],"")
        self.assertNotIn("success_fraction",row["checkpoint"]["reasons"])
        self.cleaned()

    async def test_law_options_details_native_clout_is_not_normalized_or_substituted_for_chance(self):
        self.fixture.law_detail_fields[self.fixture.law_ids[1]].update(
            GetPercentPolStrApprove=data.FixedPoint(14000),GetPercentPolStrDisapprove=data.FixedPoint(3000),GetPercentPolStrUndecided=data.FixedPoint(7000))
        row=(await self.api.law_options(details=True))[1]
        self.assertEqual(row["national_clout"]["approve_fraction"],Decimal("0.14"))
        self.assertEqual(row["national_clout"]["disapprove_fraction"],Decimal("0.03"))
        self.assertEqual(row["national_clout"]["undecided_fraction"],Decimal("0.07"))
        self.assertEqual(row["checkpoint"]["success_fraction"],Decimal("0.23"))
        self.cleaned()

    async def test_law_options_details_active_and_enacting_laws_are_not_first_candidates(self):
        self.fixture.enacting=self.fixture.law_ids[1]
        rows=await self.api.law_options(details=True)
        for row in rows:
            self.assertIsNone(row["checkpoint"]["success_fraction"])
            self.assertIn("success_fraction",row["checkpoint"]["reasons"])
            self.assertEqual(row["national_clout"]["approve_fraction"],Decimal("0.28"))
        self.assertFalse(any("GetCheckpoint" in expression for _,expression,_ in self.fixture.law_detail_calls))
        self.assertEqual(self.fixture.enacting,self.fixture.law_ids[1])
        self.cleaned()

    async def test_law_options_details_unknown_state_does_not_assume_unstarted(self):
        original=self.fixture.evaluate
        def evaluate(native,expression,bindings,**kwargs):
            query=original(native,expression,bindings,**kwargs)
            if expression=="Law.IsBeingEnacted":query.result.kind=data.Kind.I32
            return query
        with patch.object(politics.data,"evaluate",evaluate):
            row=(await self.api.law_options(details=True))[1]
        self.assertIsNone(row["checkpoint"]["success_fraction"])
        self.assertIn("unproven",row["checkpoint"]["reasons"]["success_fraction"])
        self.assertEqual(row["national_clout"]["approve_fraction"],Decimal("0.28"))
        self.cleaned()

    async def test_law_options_details_missing_wrong_metadata_fields_stay_unknown_independently(self):
        original=self.fixture.enumerate_functions
        def functions(*args,**kwargs):
            query=original(*args,**kwargs)
            query.result.rows=[row for row in query.result.rows if row.name!="GetPercentPolStrApprove"]
            for row in query.result.rows:
                if row.name=="GetIGsDisapproveList":row.argument_count=1
                if row.name=="GetCheckpointSuccessChance":row.callbacks=(0,0,1,0)
                if row.name=="GetCheckpointDebateBreakdown":row.return_type_id=-9
            return query
        with patch.object(politics.data,"enumerate_functions",functions):
            row=(await self.api.law_options(details=True))[1]
        self.assertIsNone(row["national_clout"]["approve_fraction"])
        self.assertIsNone(row["national_clout"]["disapprove_igs_description"])
        self.assertIsNone(row["checkpoint"]["success_fraction"])
        self.assertEqual(row["checkpoint"]["debate_breakdown"],"Original debate factors")
        self.assertEqual(row["checkpoint"]["advance_fraction"],Decimal("0.17"))
        self.assertIn("Law.GetCheckpointDebateBreakdown",self.fixture.events)
        for expression in ("Law.GetPercentPolStrApprove","Law.GetIGsDisapproveList","Law.GetCheckpointSuccessChance"):
            self.assertNotIn(expression,self.fixture.events)
        self.cleaned()

    async def test_law_options_details_wrong_value_kind_and_native_error_do_not_poison_other_fields(self):
        original=self.fixture.evaluate
        def evaluate(native,expression,bindings,**kwargs):
            query=original(native,expression,bindings,**kwargs)
            if expression=="Law.GetPercentPolStrApprove":query.result.kind=data.Kind.BOOL
            if expression=="Law.GetIGsDisapproveList":query.result.kind=data.Kind.OBJECT
            if expression=="Law.GetCheckpointSuccessChance":query.result.kind=data.Kind.I32
            if expression=="Law.GetCheckpointDebateBreakdown":query.result.error=1168
            return query
        with patch.object(politics.data,"evaluate",evaluate):
            row=(await self.api.law_options(details=True))[1]
        self.assertEqual(row["national_clout"]["approve_fraction"],Decimal("0.28"))
        self.assertEqual(row["national_clout"]["disapprove_igs_description"],"Original opposing IG list")
        self.assertEqual(row["checkpoint"]["success_fraction"],Decimal("0.23"))
        self.assertEqual(row["checkpoint"]["debate_breakdown"],"Original debate factors")
        self.assertIn("approve_fraction",row["national_clout"]["reasons"])
        self.assertIn("1168",row["checkpoint"]["reasons"]["debate_breakdown"])
        self.assertEqual(row["checkpoint"]["stall_fraction"],Decimal("0.19"))
        self.assertEqual(row["national_clout"]["undecided_igs_description"],"Original neutral IG list")
        self.cleaned()

    async def test_law_options_details_incomplete_native_string_keeps_copied_prefix(self):
        original=self.fixture.evaluate
        def evaluate(native,expression,bindings,**kwargs):
            query=original(native,expression,bindings,**kwargs)
            if expression=="Law.GetCheckpointSuccessBreakdown":query.result.total+=10
            return query
        with patch.object(politics.data,"evaluate",evaluate):
            row=(await self.api.law_options(details=True))[1]
        self.assertEqual(row["checkpoint"]["success_breakdown"],"Original success factors")
        self.assertIn("incomplete",row["checkpoint"]["reasons"]["success_breakdown"])
        self.assertEqual(row["checkpoint"]["success_fraction"],Decimal("0.23"))
        self.cleaned()

    async def test_law_options_details_deduplicates_candidates_before_any_new_getters(self):
        self.fixture.lists["Country.AccessLawsWithoutGovernmentOrMovementSupport"].append(("Law",self.fixture.law_ids[0]))
        rows=await self.api.law_options(details=True)
        self.assertEqual(len(rows),2)
        self.assertEqual(self.fixture.events.count("Law.StartEnacting"),0)
        self.assertEqual(self.fixture.events.count("Law.GetPercentPolStrApprove"),2)
        self.assertEqual(self.fixture.events.count("Law.GetCheckpointSuccessChance"),1)
        self.cleaned()

    async def test_law_options_details_rechecks_state_after_pending_or_running_clock(self):
        original=self.fixture.evaluate
        def evaluate(native,expression,bindings,**kwargs):
            query=original(native,expression,bindings,**kwargs)
            if expression=="Law.GetCheckpointDebateBreakdown":query.result.error=1168
            if expression=="Law.GetCheckpointStallBreakdown":self.fixture.enacting=self.fixture.law_ids[1]
            return query
        with patch.object(politics.data,"evaluate",evaluate):
            row=(await self.api.law_options(details=True))[1]
        self.assertEqual(row["checkpoint"]["success_fraction"],Decimal("0.23"))
        self.assertEqual(row["checkpoint"]["stall_breakdown"],"Original stall factors")
        self.assertIn("changed",row["checkpoint"]["reasons"]["success_fraction"])
        self.assertIn("1168",row["checkpoint"]["reasons"]["debate_breakdown"])
        self.assertIn("changed",row["checkpoint"]["reasons"]["debate_breakdown"])
        self.assertEqual(row["national_clout"]["approve_fraction"],Decimal("0.28"))
        self.cleaned()

    async def test_law_options_details_pending_resumes_original_getter_and_never_reproduces_command(self):
        original=self.fixture.evaluate
        def evaluate(native,expression,bindings,**kwargs):
            query=original(native,expression,bindings,**kwargs)
            if expression=="Law.GetCheckpointSuccessChance":query.delay=4
            return query
        self.ctx.timeout=0
        with patch.object(politics.data,"evaluate",evaluate):
            pending=await self.api.law_options(details=True)
            self.assertIsInstance(pending,PendingOperation)
            rows=await pending.resume(timeout=1)
        self.assertEqual(rows[1]["checkpoint"]["success_fraction"],Decimal("0.23"))
        self.assertEqual(self.fixture.events.count("Law.GetCheckpointSuccessChance"),1)
        self.assertEqual(self.fixture.events.count("Law.StartEnacting"),0)
        self.assertFalse(self.fixture.submitted)
        self.cleaned()

    async def test_law_options_details_rejects_nonboolean_flag_before_any_query(self):
        with self.assertRaises(ValueError):await self.api.law_options(details=1)
        self.assertFalse(self.ctx.created_queries)

    async def test_options_both_support_lists_native_reasons_no_submit(self):
        self.fixture.reject.add(22275)
        self.fixture.cannot_enact = {full_id: "Not enough bureaucracy" for full_id in self.fixture.law_ids}
        rows = await self.api.law_options()
        self.assertEqual([row["key"] for row in rows],["law_serfdom","law_workers_protections"])
        self.assertTrue(all(row["reason"] == "Not enough bureaucracy" for row in rows))
        self.assertTrue(all(row["enactable"] is None for row in rows))
        self.assertNotIn("Law.StartEnacting", self.fixture.events)
        self.assertFalse(self.fixture.submitted)
        self.cleaned()

    async def test_law_context_partial_copy_keeps_text_and_native_error(self):
        law_id = self.fixture.law_ids[1]
        self.fixture.cannot_enact[law_id] = data.ProjectionCell(
            0, 0, 122, data.Kind.STRING, 9, 0, b"partial original reason", 100)
        row = (await self.api.law_options())[1]
        self.assertEqual(row["reason"], "partial original reason")
        self.assertFalse(row["native_context"]["complete"])
        self.assertIn("122", row["native_context"]["reasons"]["fields"]["reason"])
        self.assertIsNone(row["enactable"])
        self.assertFalse(self.fixture.owners)
        self.cleaned()

    async def test_validate_enactment_only_selected_original_once_no_serialize_submit(self):
        original = Command.validate
        called = []
        def validate(owner, capacity):
            called.append(owner)
            return original(owner, capacity)
        with patch.object(Command, "validate", validate), \
             patch.object(Command, "serialize", side_effect=AssertionError("receipt serialization")):
            result = await self.api.validate_enactment("law_workers_protections")
        self.assertTrue(result["eligible"])
        self.assertIsNone(result["accepted"])
        self.assertEqual(result["law"]["id"], self.fixture.law_ids[1])
        self.assertEqual(len(called), 1)
        self.assertEqual(self.fixture.events.count("Law.StartEnacting"), 1)
        self.assertFalse(self.fixture.submitted)
        self.cleaned()

    async def test_validate_enactment_native_rejection_keeps_reason(self):
        self.fixture.reject.add(22275)
        result = await self.api.validate_enactment("law_workers_protections")
        self.assertFalse(result["eligible"])
        self.assertIsNone(result["accepted"])
        self.assertEqual(result["reason"], "Not enough bureaucracy")
        self.assertFalse(self.fixture.submitted)
        self.cleaned()

    async def test_accepted_enactment_is_not_masked_by_cleanup_native_error(self):
        original = self.fixture.release
        failed = False
        def release(native, handle, **kwargs):
            nonlocal failed
            if not failed:
                failed = True
                return Query(NS(error=5, value_handle=handle, action=data.Action.RELEASE))
            return original(native, handle, **kwargs)
        with patch.object(politics.data, "release", release):
            result = await self.api.enact("law_workers_protections")
        self.assertTrue(result["accepted"])
        self.assertEqual(len(self.fixture.submitted), 1)
        self.assertEqual(result["cleanup"]["status"], "native_error_retained")
        self.assertEqual(result["cleanup"]["native_result"].error, 5)
        self.assertTrue(self.api._retained)
        await self.api._retained[0].finish(result)
        self.cleaned()

    async def test_accepted_enactment_unknown_readback_keeps_original_query_and_no_replay(self):
        original = self.fixture.evaluate
        unknown = Query(NS(error=0))
        failure = OSError("original readback pending")
        failure.query = unknown
        def evaluate(native, expression, bindings, **kwargs):
            if expression == "Country.IsEnactingLaw" and self.fixture.submitted:
                raise failure
            return original(native, expression, bindings, **kwargs)
        with patch.object(politics.data, "evaluate", evaluate):
            result = await self.api.enact("law_workers_protections")
        self.assertTrue(result["accepted"])
        self.assertIs(result["readback_error"], failure)
        self.assertIs(self.api._retained[0].uncertain_query, unknown)
        self.assertEqual(result["cleanup"]["status"], "retained_pending_or_unknown")
        self.assertEqual(len(self.fixture.submitted), 1)
        self.assertFalse(self.fixture.released)

    async def test_copied_law_list_is_not_masked_by_unknown_cleanup(self):
        original = self.fixture.release
        def release(native, handle, **kwargs):
            raise OSError("original cleanup delivery unknown")
        with patch.object(politics.data, "release", release):
            rows = await self.api.laws()
        self.assertEqual(rows[0]["id"], self.fixture.law_ids[0])
        self.assertTrue(self.api._retained)
        run = self.api._retained[0]
        self.assertIs(run.public_result["result"], rows)
        self.assertEqual(run.public_result["cleanup"]["status"], "cleanup_unknown_retained")
        self.assertTrue(any(isinstance(row, dict) and row.get("result") is rows
                            for row in self.ctx.diagnostics.get("operations", [])))
        # The failed dispatch has no settled native proof. Keep the graph and
        # do not retry it merely to make this fixture clean.
        self.assertFalse(any(row.startswith("destroy") for row in self.fixture.events))

    async def test_enact_chosen_key_not_first_candidate(self):
        receipt = await self.api.enact("law_workers_protections")
        self.assertEqual(receipt["status"],"accepted")
        self.assertIsNone(receipt["completed"])
        self.assertEqual(receipt["current"]["id"], self.fixture.law_ids[1])
        self.assertEqual(self.fixture.submitted,[(22275,{11018:commands.UInt32(self.fixture.law_ids[1])})])
        self.cleaned()

    async def test_enact_native_rejection_is_game_reason(self):
        self.fixture.reject.add(22275)
        receipt = await self.api.enact("law_workers_protections")
        self.assertFalse(receipt["accepted"])
        self.assertEqual(receipt["reason"],"Not enough bureaucracy")
        self.assertFalse(self.fixture.submitted)
        self.cleaned()

    async def test_enact_unknown_key_is_concise_no_default_selection(self):
        receipt = await self.api.enact("law_does_not_exist")
        self.assertEqual(receipt["status"],"unavailable")
        self.assertIsNone(receipt["accepted"])
        self.assertFalse(self.fixture.submitted)
        self.cleaned()

    async def test_existing_enactment_never_cancels_as_preparation(self):
        self.fixture.enacting = self.fixture.law_ids[0]
        self.fixture.reject.add(22275)
        receipt = await self.api.enact("law_workers_protections")
        self.assertFalse(receipt["validation"].validated)
        self.assertEqual(receipt["reason"], "Not enough bureaucracy")
        self.assertEqual(self.fixture.enacting, self.fixture.law_ids[0])
        self.assertNotIn("Country.GetLawBeingEnacted.StopEnacting", self.fixture.events)
        self.assertFalse(self.fixture.submitted)
        self.cleaned()

    async def test_enactment_reads_owned_current_law_without_stop_command(self):
        self.fixture.enacting = self.fixture.law_ids[1]
        row = await self.api.enactment()
        self.assertEqual(row["id"],self.fixture.law_ids[1])
        self.assertEqual(row["progress"],Decimal("1.25"))
        self.assertEqual(row["key"], "law_workers_protections")
        self.assertNotIn("Country.GetLawBeingEnacted.StopEnacting", self.fixture.events)
        self.assertFalse(self.fixture.owners)
        self.assertFalse(self.fixture.submitted)
        self.cleaned()

    async def test_stop_current_enactment_only(self):
        self.fixture.enacting = self.fixture.law_ids[1]
        receipt = await self.api.stop_enactment()
        self.assertEqual(receipt["status"],"accepted")
        self.assertFalse(receipt["enacting_after_observed"])
        self.assertIsNone(receipt["completed"])
        self.assertEqual([token for token,_ in self.fixture.submitted],[22276])
        self.cleaned()

    async def test_stop_when_none_is_unchanged(self):
        receipt = await self.api.stop_enactment()
        self.assertEqual(receipt["status"],"unchanged")
        self.assertIsNone(receipt["accepted"])
        self.assertFalse(receipt["enacting_observed"])
        self.assertFalse(self.fixture.submitted)
        self.cleaned()

    async def test_institutions_names_levels_costs_zero_id(self):
        rows = await self.api.institutions()
        self.assertEqual(rows[0]["id"],0)
        self.assertEqual(rows[0]["name"],"Education")
        self.assertIsNone(rows[0]["key"])
        self.assertEqual(rows[0]["cost"],Decimal("20"))
        self.assertEqual(rows[0]["max_level"],5)
        self.cleaned()

    async def test_set_institution_name_is_native_target_not_final_completion(self):
        receipt = await self.api.set_institution("Education",3)
        self.assertEqual(receipt["status"],"accepted")
        self.assertEqual(receipt["observed_target_level"],3)
        self.assertEqual(receipt["observed_level"],1)
        self.assertFalse(receipt["completed"])
        self.assertEqual(self.fixture.submitted,[(20288,{20286:commands.UInt32(0),1875:3})])
        self.cleaned()

    async def test_set_institution_noop_and_rejection(self):
        noop = await self.api.set_institution(0,1)
        self.assertEqual(noop["status"],"unchanged")
        self.assertIsNone(noop["accepted"])
        self.assertTrue(noop["target_satisfied_observed"])
        self.fixture.reject.add(20288)
        rejected = await self.api.set_institution(0,3)
        self.assertEqual(rejected["reason"],"Not enough bureaucracy")
        self.assertFalse(self.fixture.submitted)
        self.cleaned()

    async def test_bad_level_rejected_before_queries(self):
        for value in [True,-1,2.5]:
            with self.assertRaises(ValueError):
                await self.api.set_institution("Education",value)
        self.assertFalse(self.ctx.created_queries)

    async def test_interest_groups_owned_country_pagination(self):
        rows = await self.api.interest_groups()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["name"],"Landowners")
        self.assertIsNone(rows[0]["key"])
        self.assertNotIn("clout",rows[0])
        self.assertNotIn("approval",rows[0])
        self.assertNotIn("in_government",rows[0])
        self.assertFalse(self.fixture.events)  # Default stays the Objects-only fast read.
        self.cleaned()

    async def test_interest_group_details_real_fraction_points_and_native_government_bool(self):
        with localcontext() as context:
            context.prec=2
            rows=await self.api.interest_groups(details=True)
        self.assertEqual(len(rows),1)
        row=rows[0]
        self.assertEqual(row["clout"],Decimal("0.265"))
        self.assertEqual(row["approval"],-3)
        self.assertIs(row["in_government"],True)
        self.assertEqual(row["units"],{"clout":"fraction","approval":"points"})
        self.assertEqual(row["reasons"],{})
        self.assertFalse(self.fixture.submitted);self.assertFalse(self.fixture.owners)
        self.cleaned()

    async def test_interest_group_details_join_full_ids_not_names_or_provider_order(self):
        first=self.fixture.ig_id;second=0x08000006
        self.fixture.ig_fields[second]={"GetClout":data.FixedPoint(10500),"GetApprovalValue":7,"IsInGovernment":False}
        self.fixture.lists["Country.AccessAllInterestGroups"]=[("InterestGroup",second),("InterestGroup",first)]
        def objects_page(native,start_index,**kwargs):
            row=NS(error=0,country_error=0,resolved_id=(first,second)[start_index],country_id=self.fixture.actor,name="Same display name")
            return Query(NS(error=0,world=self.fixture.world,records=(row,),next_index=start_index+1,slot_bound=2))
        with patch.object(politics.objects,"enumerate_interest_group_details",objects_page):
            rows=await self.api.interest_groups(details=True)
        by_id={row["id"]:row for row in rows}
        self.assertEqual(by_id[first]["clout"],Decimal("0.265"))
        self.assertEqual(by_id[first]["approval"],-3)
        self.assertEqual(by_id[second]["clout"],Decimal("0.105"))
        self.assertEqual(by_id[second]["approval"],7)
        self.assertIs(by_id[second]["in_government"],False)
        self.cleaned()

    async def test_interest_group_details_missing_or_action_metadata_is_none_without_guess(self):
        original=self.fixture.enumerate_functions
        def functions(*args,**kwargs):
            query=original(*args,**kwargs)
            for row in query.result.rows:
                if row.name=="GetApprovalValue":row.callbacks=(0,0,1,0)
                if row.name=="IsInGovernment":row.argument_count=1
            return query
        with patch.object(politics.data,"enumerate_functions",functions):
            row=(await self.api.interest_groups(details=True))[0]
        self.assertEqual(row["clout"],Decimal("0.265"))
        self.assertIsNone(row["approval"]);self.assertIsNone(row["in_government"])
        self.assertIn("approval",row["reasons"]);self.assertIn("in_government",row["reasons"])
        self.assertNotIn("InterestGroup.GetApprovalValue",self.fixture.events)
        self.assertNotIn("InterestGroup.IsInGovernment",self.fixture.events)
        self.cleaned()

    async def test_interest_group_details_wrong_declared_return_type_is_not_invoked(self):
        original=self.fixture.enumerate_functions
        def functions(*args,**kwargs):
            query=original(*args,**kwargs)
            for row in query.result.rows:
                if row.name=="GetApprovalValue":row.return_type_id=-99
            return query
        with patch.object(politics.data,"enumerate_functions",functions):
            row=(await self.api.interest_groups(details=True))[0]
        self.assertIsNone(row["approval"])
        self.assertNotIn("InterestGroup.GetApprovalValue",self.fixture.events)
        self.assertIs(row["in_government"],True)
        self.cleaned()

    async def test_interest_group_details_native_error_preserves_other_real_fields(self):
        original=self.fixture.evaluate
        def evaluate(native,expression,bindings,**kwargs):
            query=original(native,expression,bindings,**kwargs)
            if expression=="InterestGroup.GetApprovalValue":query.result.error=1168
            return query
        with patch.object(politics.data,"evaluate",evaluate):
            row=(await self.api.interest_groups(details=True))[0]
        self.assertIsNone(row["approval"])
        self.assertIn("1168",row["reasons"]["approval"])
        self.assertEqual(row["clout"],Decimal("0.265"))
        self.assertIs(row["in_government"],True)
        self.cleaned()

    async def test_interest_group_details_wrong_value_kinds_never_turn_into_points_or_fraction(self):
        self.fixture.ig_fields[self.fixture.ig_id]={"GetClout":1,"GetApprovalValue":data.FixedPoint(700000),"IsInGovernment":1}
        row=(await self.api.interest_groups(details=True))[0]
        self.assertIsNone(row["clout"]);self.assertIsNone(row["approval"]);self.assertIsNone(row["in_government"])
        self.assertEqual(set(row["reasons"]),{"clout","approval","in_government"})
        self.cleaned()

    async def test_interest_group_details_zero_and_false_are_real_observations(self):
        self.fixture.ig_fields[self.fixture.ig_id]={"GetClout":data.FixedPoint(0),"GetApprovalValue":0,"IsInGovernment":False}
        row=(await self.api.interest_groups(details=True))[0]
        self.assertEqual(row["clout"],Decimal(0));self.assertEqual(row["approval"],0)
        self.assertIs(row["in_government"],False)
        self.assertFalse(row["reasons"])
        self.cleaned()

    async def test_interest_group_details_stale_generation_never_joins_by_low24(self):
        stale=self.fixture.ig_id^0x01000000
        self.fixture.lists["Country.AccessAllInterestGroups"]=[("InterestGroup",stale)]
        row=(await self.api.interest_groups(details=True))[0]
        self.assertEqual(row["id"],self.fixture.ig_id)
        self.assertIsNone(row["clout"]);self.assertIsNone(row["approval"]);self.assertIsNone(row["in_government"])
        self.assertNotIn("InterestGroup.GetClout",self.fixture.events)
        self.assertEqual(set(row["reasons"]),{"clout","approval","in_government"})
        self.cleaned()

    async def test_interest_group_details_empty_data_source_is_unknown_not_zero(self):
        self.fixture.lists["Country.AccessAllInterestGroups"]=[]
        row=(await self.api.interest_groups(details=True))[0]
        self.assertEqual(row["name"],"Landowners")
        self.assertIsNone(row["clout"]);self.assertIsNone(row["approval"]);self.assertIsNone(row["in_government"])
        self.cleaned()

    async def test_interest_group_details_pending_resumes_exact_original_getter(self):
        original=self.fixture.evaluate
        def evaluate(native,expression,bindings,**kwargs):
            query=original(native,expression,bindings,**kwargs)
            if expression=="InterestGroup.GetClout":query.delay=4
            return query
        self.ctx.timeout=0
        with patch.object(politics.data,"evaluate",evaluate):
            pending=await self.api.interest_groups(details=True)
            self.assertIsInstance(pending,PendingOperation)
            rows=await pending.resume(timeout=1)
        self.assertEqual(rows[0]["clout"],Decimal("0.265"))
        self.assertEqual(self.fixture.events.count("InterestGroup.GetClout"),1)
        self.assertFalse(self.fixture.submitted)
        self.cleaned()

    async def test_interest_group_details_flag_rejects_nonboolean_before_native_queries(self):
        with self.assertRaises(ValueError):await self.api.interest_groups(details=1)
        self.assertFalse(self.ctx.created_queries)

    async def test_movement_default_shape_and_cost_remain_the_existing_light_read(self):
        rows=await self.api.political_movements()
        self.assertEqual(rows,[{"id":self.fixture.movement["id"],"name":"Liberal Movement","name_raw":"Liberal Movement","bolstered":False,"suppressed":False}])
        self.assertEqual(self.fixture.events,["Country.AccessPoliticalMovements","PoliticalMovement.GetName","PoliticalMovement.IsBolstered","PoliticalMovement.IsSuppressed"])
        self.cleaned()

    async def test_movement_details_native_metrics_ideology_and_affected_laws_are_distinct(self):
        with localcontext() as context:
            context.prec=2
            row=(await self.api.political_movements(details=True))[0]
        self.assertEqual(row["support_fraction"],Decimal("0.23"))
        self.assertEqual(row["radicalism_fraction"],Decimal("0.67"))
        self.assertEqual(row["supporting_individuals"],3500000000)
        self.assertIs(row["insurrectionary"],False)  # Not inferred from activism being over50%.
        self.assertIs(row["causing_obstinance"],False)
        self.assertIn("activism",row["radicalism_label"])
        self.assertEqual(row["core_ideology"],{"name":"Land Reform","name_raw":"Land Reform",
            "description":"Original political ideology description","description_raw":"Original political ideology description",
            "law_stances":"Original endorsed and opposed laws","law_stances_raw":"Original endorsed and opposed laws"})
        self.assertEqual(row["affected_laws_success"][0]["key"],"law_workers_protections")
        self.assertFalse(row["affected_laws_success"][0]["active"])
        self.assertEqual(row["affected_laws_stall"][0]["key"],"law_serfdom")
        self.assertNotIn("PoliticalMovement.GetTargetRadicalism",self.fixture.events)
        self.assertNotIn("PoliticalMovement.GetPopularSupport",self.fixture.events)
        self.assertFalse(self.fixture.submitted);self.assertFalse(self.fixture.owners)
        self.cleaned()

    async def test_movement_supporting_character_is_full_id_checked_not_called_a_leader(self):
        row=(await self.api.political_movements(details=True))[0]
        character=row["supporting_characters"][0]
        self.assertEqual(character["id"],self.fixture.character_id)
        self.assertEqual(character["name"],"Fixture Agitator")
        self.assertEqual(character["ideology"],"Fixture Reformist")
        self.assertEqual(character["interest_group"],"Fixture Rural Folk")
        self.assertEqual(character["popularity"],Decimal("12.5"))
        self.assertEqual(character["units"]["popularity"],"points")
        self.assertEqual(character["roles"],"Agitator")
        self.assertIs(character["is_agitator"],True);self.assertIs(character["is_ig_leader"],False)
        self.assertTrue(all(binding.retained for binding in self.fixture.character_bindings))
        self.assertNotIn("Character.GetIdeology",self.fixture.events)
        self.assertNotIn("Character.GetInterestGroup",self.fixture.events)
        self.assertNotIn("PoliticalMovement.GetCoreIdeology",self.fixture.events)
        self.cleaned()

    async def test_movement_missing_or_action_getter_is_unknown_not_another_metric(self):
        original=self.fixture.enumerate_functions
        def functions(*args,**kwargs):
            query=original(*args,**kwargs)
            for row in query.result.rows:
                if row.name=="GetRadicalism":row.callbacks=(0,0,1,0)
                if row.name=="GetSupport":row.argument_count=1
            return query
        with patch.object(politics.data,"enumerate_functions",functions):
            row=(await self.api.political_movements(details=True))[0]
        self.assertIsNone(row["support_fraction"]);self.assertIsNone(row["radicalism_fraction"])
        self.assertIn("support_fraction",row["reasons"]);self.assertIn("radicalism_fraction",row["reasons"])
        self.assertNotIn("PoliticalMovement.GetSupport",self.fixture.events)
        self.assertNotIn("PoliticalMovement.GetRadicalism",self.fixture.events)
        self.assertEqual(row["supporting_individuals"],3500000000)
        self.cleaned()

    async def test_movement_native_field_error_preserves_other_actual_fields(self):
        original=self.fixture.evaluate
        def evaluate(native,expression,bindings,**kwargs):
            query=original(native,expression,bindings,**kwargs)
            if expression=="PoliticalMovement.GetRadicalism":query.result.error=1168
            return query
        with patch.object(politics.data,"evaluate",evaluate):
            row=(await self.api.political_movements(details=True))[0]
        self.assertEqual(row["radicalism_fraction"],Decimal("0.67"))
        self.assertIn("1168",row["reasons"]["radicalism_fraction"])
        self.assertEqual(row["support_fraction"],Decimal("0.23"))
        self.assertTrue(row["supporting_characters"])
        self.cleaned()

    async def test_movement_wrong_count_kind_is_not_truncated_or_relabelled(self):
        original=self.fixture.evaluate
        def evaluate(native,expression,bindings,**kwargs):
            query=original(native,expression,bindings,**kwargs)
            if expression=="PoliticalMovement.GetNumSupportingIndividuals":query.result.kind=data.Kind.I32
            return query
        with patch.object(politics.data,"evaluate",evaluate):
            row=(await self.api.political_movements(details=True))[0]
        self.assertEqual(row["supporting_individuals"],3500000000)
        self.assertIn("supporting_individuals",row["reasons"])
        self.cleaned()

    async def test_movement_zero_metrics_false_state_and_empty_character_source_are_real(self):
        self.fixture.movement.update(support=data.FixedPoint(0),radicalism=data.FixedPoint(0),individuals=0,insurrectionary=False)
        self.fixture.lists["PoliticalMovement.GetSupportingCharacters"]=[]
        row=(await self.api.political_movements(details=True))[0]
        self.assertEqual(row["support_fraction"],Decimal(0));self.assertEqual(row["radicalism_fraction"],Decimal(0))
        self.assertEqual(row["supporting_individuals"],0)
        self.assertIs(row["insurrectionary"],False)
        self.assertEqual(row["supporting_characters"],[])
        self.cleaned()

    async def test_movement_borrowed_character_is_not_rebound_to_an_owned_receiver(self):
        original=self.fixture.list_element
        def element(native,handle,index,**kwargs):
            if self.fixture.handles[handle][index][0]=="Character":
                return Query(self.fixture.wrapper("Character",self.fixture.character_id,borrowed=True))
            return original(native,handle,index,**kwargs)
        with patch.object(politics.data,"list_element",element):
            row=(await self.api.political_movements(details=True))[0]
        self.assertIsNone(row["supporting_characters"])
        self.assertIn("borrowed",row["reasons"]["supporting_characters"])
        self.assertFalse(self.fixture.character_bindings)
        self.assertEqual(row["support_fraction"],Decimal("0.23"))
        self.cleaned()

    async def test_movement_character_generation_mismatch_does_not_join_background_by_name(self):
        def stale(native,domain,ids,**kwargs):
            full_id=tuple(ids)[0]
            return Query(NS(error=0,world=self.fixture.world,records=[NS(error=0,requested_id=full_id,resolved_id=full_id^0x01000000)]))
        with patch.object(politics.objects,"read_ids",stale):
            row=(await self.api.political_movements(details=True))[0]
        character=row["supporting_characters"][0]
        self.assertIsNone(character["id"]);self.assertIsNone(character["name"])
        self.assertIsNone(character["ideology"]);self.assertIsNone(character["interest_group"])
        self.assertIn("identity",character["reasons"])
        self.cleaned()

    async def test_movement_chain_uses_actual_return_alias_metadata_in_same_callback(self):
        row=(await self.api.political_movements(details=True))[0]
        self.assertEqual(row["supporting_characters"][0]["ideology"],"Fixture Reformist")
        self.assertIn("Character.GetIdeology.GetName",self.fixture.events)
        self.assertIn("PoliticalMovement.GetCoreIdeology.GetLawStanceDesc",self.fixture.events)
        self.assertNotIn("Character.GetIdeology",self.fixture.events)
        self.cleaned()

    async def test_character_empty_return_alias_catalog_uses_named_metadata_only_for_original_chain(self):
        original=self.fixture.enumerate_functions
        alias_names={"CharacterIdeologyAlias","CharacterIGAlias"}
        def functions(native,type_id,start,**kwargs):
            if self.ctx.type_names[type_id] in alias_names:
                return Query(NS(error=0,rows=[],next=0,total=0))
            return original(native,type_id,start,**kwargs)
        with patch.object(politics.data,"enumerate_functions",functions):
            row=(await self.api.political_movements(details=True))[0]
        character=row["supporting_characters"][0]
        self.assertEqual(character["id"],self.fixture.character_id)
        self.assertEqual(character["ideology"],"Fixture Reformist")
        self.assertEqual(character["interest_group"],"Fixture Rural Folk")
        self.assertNotIn("ideology",character["reasons"])
        self.assertNotIn("interest_group",character["reasons"])
        self.assertTrue(all(binding.retained and binding.type_id==next(k for k,v in self.ctx.type_names.items() if v=="Character") for binding in self.fixture.character_bindings))
        self.assertNotIn("Ideology.GetName",self.fixture.events)
        self.assertNotIn("InterestGroup.GetName",self.fixture.events)
        self.assertNotIn("Character.GetIdeology",self.fixture.events)
        self.assertNotIn("Character.GetInterestGroup",self.fixture.events)
        self.cleaned()

    async def test_character_alias_catalog_error_can_use_proven_named_getter_without_rebinding(self):
        original=self.fixture.enumerate_functions
        def functions(native,type_id,start,**kwargs):
            if self.ctx.type_names[type_id] in {"CharacterIdeologyAlias","CharacterIGAlias"}:
                return Query(NS(error=1168,rows=[],next=0,total=0))
            return original(native,type_id,start,**kwargs)
        with patch.object(politics.data,"enumerate_functions",functions):
            character=(await self.api.political_movements(details=True))[0]["supporting_characters"][0]
        self.assertEqual(character["ideology"],"Fixture Reformist")
        self.assertEqual(character["interest_group"],"Fixture Rural Folk")
        self.assertFalse(self.fixture.submitted)
        self.cleaned()

    async def test_character_named_fallback_blocks_action_but_keeps_observed_value_despite_metadata(self):
        original=self.fixture.enumerate_functions
        def functions(native,type_id,start,**kwargs):
            name=self.ctx.type_names[type_id]
            if name in {"CharacterIdeologyAlias","CharacterIGAlias"}:
                return Query(NS(error=0,rows=[],next=0,total=0))
            query=original(native,type_id,start,**kwargs)
            for row in query.result.rows:
                if row.name=="GetName" and name=="Ideology":row.callbacks=(0,0,1,0)
                if row.name=="GetName" and name=="InterestGroup":row.return_type_id=-99
            return query
        with patch.object(politics.data,"enumerate_functions",functions):
            character=(await self.api.political_movements(details=True))[0]["supporting_characters"][0]
        self.assertIsNone(character["ideology"])
        self.assertEqual(character["interest_group"],"Fixture Rural Folk")
        self.assertIn("ideology",character["reasons"]);self.assertIn("interest_group",character["reasons"])
        self.assertNotIn("Character.GetIdeology.GetName",self.fixture.events)
        self.assertIn("Character.GetInterestGroup.GetName",self.fixture.events)
        self.assertEqual(character["popularity"],Decimal("12.5"))
        self.cleaned()

    async def test_character_declared_alias_action_leaf_is_not_overridden_by_named_catalog(self):
        original=self.fixture.enumerate_functions
        def functions(native,type_id,start,**kwargs):
            query=original(native,type_id,start,**kwargs)
            if self.ctx.type_names[type_id]=="CharacterIdeologyAlias":
                for row in query.result.rows:
                    if row.name=="GetName":row.callbacks=(0,0,1,0)
            return query
        with patch.object(politics.data,"enumerate_functions",functions):
            character=(await self.api.political_movements(details=True))[0]["supporting_characters"][0]
        self.assertIsNone(character["ideology"])
        self.assertNotIn("Character.GetIdeology.GetName",self.fixture.events)
        self.assertEqual(character["interest_group"],"Fixture Rural Folk")
        self.cleaned()

    async def test_character_named_fallback_native_chain_error_keeps_observed_value(self):
        original_functions=self.fixture.enumerate_functions
        original_evaluate=self.fixture.evaluate
        def functions(native,type_id,start,**kwargs):
            if self.ctx.type_names[type_id] in {"CharacterIdeologyAlias","CharacterIGAlias"}:
                return Query(NS(error=0,rows=[],next=0,total=0))
            return original_functions(native,type_id,start,**kwargs)
        def evaluate(native,expression,bindings,**kwargs):
            query=original_evaluate(native,expression,bindings,**kwargs)
            if expression=="Character.GetIdeology.GetName":query.result.error=11
            return query
        with patch.object(politics.data,"enumerate_functions",functions),patch.object(politics.data,"evaluate",evaluate):
            character=(await self.api.political_movements(details=True))[0]["supporting_characters"][0]
        self.assertEqual(character["ideology"],"Fixture Reformist")
        self.assertIn("11",character["reasons"]["ideology"])
        self.assertEqual(character["interest_group"],"Fixture Rural Folk")
        self.cleaned()

    async def test_movement_pending_detail_resumes_original_getter_without_actions(self):
        original=self.fixture.evaluate
        def evaluate(native,expression,bindings,**kwargs):
            query=original(native,expression,bindings,**kwargs)
            if expression=="PoliticalMovement.GetRadicalism":query.delay=4
            return query
        self.ctx.timeout=0
        with patch.object(politics.data,"evaluate",evaluate):
            pending=await self.api.political_movements(details=True)
            self.assertIsInstance(pending,PendingOperation)
            rows=await pending.resume(timeout=1)
        self.assertEqual(rows[0]["radicalism_fraction"],Decimal("0.67"))
        self.assertEqual(self.fixture.events.count("PoliticalMovement.GetRadicalism"),1)
        self.assertFalse(self.fixture.submitted)
        self.cleaned()

    async def test_movement_details_rejects_nonboolean_flag_before_queries(self):
        with self.assertRaises(ValueError):await self.api.political_movements(details=1)
        self.assertFalse(self.ctx.created_queries)

    async def test_movement_toggle_is_idempotent_and_not_an_ig_join(self):
        first = await self.api.bolster("Liberal Movement")
        second = await self.api.bolster(self.fixture.movement["id"])
        self.assertEqual(first["status"],"observed")
        self.assertEqual(second["status"],"unchanged")
        self.assertIsNone(second["accepted"])
        self.assertTrue(second["desired_state_observed"])
        self.assertTrue(second["observed"])
        self.assertEqual(len(self.fixture.submitted),1)
        absent = await self.api.suppress("Landowners")
        self.assertIn("do not target interest groups",absent["reason"])
        self.cleaned()

    async def test_movement_release_separate_toggle(self):
        self.fixture.movement["suppressed"] = True
        receipt = await self.api.suppress("Liberal Movement",False)
        self.assertEqual(receipt["status"],"observed")
        self.assertFalse(receipt["observed"])
        self.assertEqual(self.fixture.submitted[0][0],24476)
        self.cleaned()

    async def test_decree_options_native_serialized_key_no_submit(self):
        rows = await self.api.decree_options(8)
        self.assertEqual(rows[0]["key"],"decree_road_maintenance")
        self.assertIsNone(rows[0]["eligible"])
        self.assertFalse(rows[0]["eligibility_checked"])
        self.assertFalse(any(isinstance(event, str) and "GetSetCommand" in event for event in self.fixture.events))
        self.assertFalse(self.fixture.owners)
        self.assertFalse(self.fixture.submitted)
        self.cleaned()

    async def test_decree_set_active_key_and_remove_instance(self):
        receipt = await self.api.set_decree(8,"decree_road_maintenance")
        self.assertTrue(receipt["accepted"])
        rows = await self.api.decrees(8)
        self.assertEqual(rows[0]["key"],"decree_road_maintenance")
        receipt = await self.api.remove_decree(8,"decree_road_maintenance")
        self.assertTrue(receipt["accepted"])
        self.assertEqual([token for token,_ in self.fixture.submitted],[22673,22672])
        self.cleaned()

    async def test_decree_already_active_never_submits_duplicate(self):
        self.fixture.active_decree = 0x08000007
        receipt = await self.api.set_decree(8,"decree_road_maintenance")
        self.assertEqual(receipt["status"], "unchanged")
        self.assertIsNone(receipt["accepted"])
        self.assertTrue(receipt["active_decree_observed"])
        self.assertFalse(self.fixture.owners)
        self.assertFalse(self.fixture.submitted)
        self.cleaned()

    async def test_concurrent_movement_intents_do_not_double_toggle(self):
        first, second = await asyncio.gather(self.api.bolster("Liberal Movement"),
                                             self.api.bolster("Liberal Movement"))
        self.assertEqual(first["status"], "observed")
        self.assertEqual(second["status"], "unchanged")
        self.assertEqual(len(self.fixture.submitted), 1)
        self.cleaned()

    async def test_fixed_scalar_native_scale_sign_and_exact_low_precision(self):
        run = run_mod.Run(self.ctx, "fixed_fixture")
        binding = await run.country()
        for signed, expected in ((100000,"1"), (-100000,"-1"),
                (-1234567890123456789,"-12345678901234.56789")):
            with patch.object(politics.data, "evaluate", lambda *a, signed=signed, **kw:
                    Query(self.fixture.scalar(data.FixedPoint(signed & 0xffffffffffffffff)))):
                with localcontext() as context:
                    context.prec = 2
                    actual = await run.scalar("Country", "FixtureNativeFixed", binding)
                self.assertEqual(actual, Decimal(expected))
                self.assertIsInstance(actual, Decimal)
        await run.cleanup()
        self.cleaned()


    async def test_missing_required_native_value_is_not_zero_or_false(self):
        original = self.fixture.evaluate
        original_missing_result = self.fixture.scalar(None)
        self.assertIsNone(self.fixture.enacting)
        # This test independently configures NativeValidate rejection. Whether
        # the real game rejects invalid Law ID FFFFFFFF remains UNPROVED.
        self.fixture.reject.add(22276)
        def missing(native, expression, bindings, **kwargs):
            if expression == "Country.IsEnactingLaw":
                return Query(original_missing_result)
            return original(native, expression, bindings, **kwargs)
        with patch.object(politics.data, "evaluate", missing):
            receipt = await self.api.stop_enactment()
        self.assertIs(receipt["before"], original_missing_result)
        self.assertIsNone(receipt["before"].value)
        self.assertIsNone(receipt["enacting_observed"])
        self.assertEqual(receipt["status"], "rejected")
        self.assertIs(receipt["accepted"], False)
        self.assertEqual(receipt["validation"].error, 0)
        self.assertIs(receipt["validation"].validated, False)
        self.assertEqual(receipt["reason"], receipt["validation"].reason)
        validation_queries = [query for query in self.ctx.created_queries
                              if query.result is receipt["validation"]]
        self.assertEqual(len(validation_queries), 1)
        self.assertEqual(len(self.fixture.owners), 1)
        self.assertEqual(self.fixture.owners[0].type_token, 22276)
        self.assertEqual(self.fixture.owners[0].params[11018], commands.UInt32(0xffffffff))
        self.assertEqual(self.fixture.events.count("Country.GetLawBeingEnacted.StopEnacting"), 1)
        self.assertTrue(all(self.ctx.created_queries.count(q)==1 for q in self.ctx.created_queries))
        self.assertIsNone(receipt["submission"])
        self.assertFalse(self.fixture.submitted)
        self.assertIsNone(self.fixture.enacting)
        self.cleaned()

    async def test_accepted_legislation_observer_error_is_not_replayed_or_lost(self):
        original = self.fixture.evaluate
        def read(native, expression, bindings, **kwargs):
            if self.fixture.submitted and expression == "Country.IsEnactingLaw":
                result = self.fixture.scalar(None)
                result.error = 1168
                return Query(result)
            return original(native, expression, bindings, **kwargs)
        with patch.object(politics.data, "evaluate", read):
            receipt = await self.api.enact("law_workers_protections")
        self.assertTrue(receipt["accepted"])
        self.assertEqual(receipt["status"], "accepted")
        self.assertIsNone(receipt["current"])
        self.assertIn("1168", receipt["observation_reason"])
        self.assertEqual(len(self.fixture.submitted), 1)
        self.cleaned()

    async def test_accepted_movement_observer_error_preserves_acceptance(self):
        original = self.fixture.evaluate
        def read(native, expression, bindings, **kwargs):
            if self.fixture.submitted and expression == "PoliticalMovement.IsBolstered":
                result = self.fixture.scalar(None)
                result.error = 1168
                return Query(result)
            return original(native, expression, bindings, **kwargs)
        with patch.object(politics.data, "evaluate", read):
            receipt = await self.api.bolster("Liberal Movement")
        self.assertTrue(receipt["accepted"])
        self.assertEqual(receipt["status"], "accepted")
        self.assertIsNone(receipt["observed"])
        self.assertEqual(len(self.fixture.submitted), 1)
        self.cleaned()

    async def test_real_core_pending_resume_keeps_original_submit_once(self):
        self.ctx.timeout = 0
        self.fixture.delay_submit = 5
        pending = await self.api.enact("law_workers_protections")
        self.assertIsInstance(pending,PendingOperation)
        original_task = pending.task
        receipt = await pending.resume(timeout=1)
        self.assertIs(pending.task, original_task)
        self.assertTrue(original_task.done())
        self.assertEqual(receipt["status"], "accepted")
        self.assertTrue(receipt["accepted"])
        self.assertIs(receipt["submission"], receipt["native_result"])
        self.assertEqual(receipt["current"]["status"], "enacting")
        self.assertEqual(receipt["current"]["id"], self.fixture.law_ids[1])
        self.assertEqual(receipt["current"]["key"], self.fixture.definitions[1].key)
        self.assertTrue(receipt["current"]["enacting"])
        self.assertEqual(receipt["current"]["country_id"], self.fixture.actor)
        self.assertEqual(receipt["source_country_id"], self.fixture.actor)
        self.assertIsNone(receipt["completed"])
        submit_queries = [query for query in self.ctx.created_queries
                          if query.result is receipt["submission"]]
        self.assertEqual(len(submit_queries), 1)
        self.assertIs(receipt["submission"], submit_queries[0].result)
        self.assertEqual(len(self.fixture.submitted),1)
        self.assertTrue(all(self.ctx.created_queries.count(q)==1 for q in self.ctx.created_queries))
        self.cleaned()

    async def test_definition_join_failure_preserves_owners_no_fabricated_key(self):
        self.fixture.bad_definition = True
        row, = await self.api.laws()
        self.assertEqual(row["id"], self.fixture.law_ids[0])
        self.assertIsNone(row["key"])
        self.assertFalse(row["identity_complete"])
        self.assertIn("matching key", row["identity_reasons"]["key"])
        self.assertEqual(self.api._retained, [])
        self.assertEqual(self.fixture.handles, {})
        self.assertEqual(self.fixture.memory.blocks, {})
        self.assertFalse(self.fixture.submitted)

    async def test_actor_mismatch_never_submits(self):
        # Native actor is an independent fixture fact on the original cloned
        # command. A foreign source context must not become an actor receipt gate.
        native_actor = self.fixture.actor + 1
        original_created = self.fixture.created
        def created(token, params, command=0xD0000):
            query = original_created(token, params, command)
            query.result.owner.native_actor_id = native_actor
            return query
        self.fixture.bad_actor = True
        with patch.object(self.fixture, "created", created), \
             patch.object(Command, "serialize", side_effect=AssertionError("law selected action serialized")):
            self.fixture.reject.add(22275)
            rejected = await self.api.enact("law_workers_protections")
            self.assertFalse(rejected["validation"].validated)
            self.assertFalse(rejected["accepted"])
            self.assertFalse(self.fixture.submitted)
            self.assertEqual(self.fixture.owners[-1].native_actor_id, native_actor)
            self.fixture.reject.remove(22275)
            accepted = await self.api.enact("law_workers_protections")
        self.assertTrue(accepted["validation"].validated)
        self.assertTrue(accepted["accepted"])
        self.assertEqual(len(self.fixture.submitted), 1)
        self.assertEqual(self.fixture.owners[-1].native_actor_id, native_actor)
        self.assertEqual(accepted["source_country_id"], self.fixture.actor)
        self.assertIsNone(accepted["native_actor_id"])
        self.cleaned()


if __name__ == "__main__":
    unittest.main()
