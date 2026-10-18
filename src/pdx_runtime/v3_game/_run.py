"""Shared retained-ownership run scope for the V3 game layer.

Every V3 domain operation that keeps original native values alive uses one
``Run`` scope: it claims the session's single owner pool, captures the exact
queries it submitted, and releases only what it actually adopted.

This module is the layer below the domain packages. ``politics`` is one user of
it, not its owner: military, diplomacy, economy, construction, setup and
state-management all create the same scope.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar

from .. import v3_access as access, v3_commands as commands, v3_data as data
from ._values import fixed_decimal


ACTIVE_CONTEXT = ContextVar("v3_game_context", default=None)


async def read(query):
    """Read the original query through the active game's retained executor."""
    context = ACTIVE_CONTEXT.get()
    return query.read() if context is None else await context.wait(query)


async def complete(owner):
    """Complete the original owner without replacing its pending query."""
    context = ACTIVE_CONTEXT.get()
    if context is None:
        return owner.complete()
    query = getattr(owner, "pending", None)
    return owner.complete() if query is None else await context.wait(query, owner=owner)


async def submit(factory):
    """Submit once through the active game's query slot, when one is active."""
    context = ACTIVE_CONTEXT.get()
    return factory() if context is None else await context.submit(factory)


async def types():
    """Copy the active game's discovered native type metadata."""
    context = ACTIVE_CONTEXT.get()
    if context is None:
        return None
    await context.all_types()
    return dict(context._types)


class SourceUnavailable(RuntimeError):
    """A completed native source has no usable value for the requested operation."""


class KnownResultError(RuntimeError):
    """A completed native call returned a known error that is kept unmodified."""

    def __init__(self, label, result):
        self.label, self.result = label, result
        super().__init__(f"{label}: native error {result.error}")


def rejected(action, reason, **values):
    return dict(action=action, status="rejected", accepted=False, reason=reason, **values)


def command_field(tree, token):
    values = []
    def visit(item):
        if isinstance(item, commands.Assignment):
            if item.key == commands.Token(token):
                values.append(item.value)
            visit(item.value)
        elif isinstance(item, tuple):
            for child in item:
                visit(child)
    visit(tree)
    if len(values) != 1:
        raise RuntimeError("Native command identity is incomplete")
    return values[0]


def scope_owner_pool(ctx):
    """Use the session's one core pool for every retained run."""
    from ..query_executor import RetainedOwners
    pool = getattr(ctx, "owner_pool", None)
    if pool is None:
        anchor = getattr(ctx, "session", ctx)
        pool = getattr(anchor, "owner_pool", None)
        if pool is None:
            anchor.owner_pool = pool = RetainedOwners()
        ctx.owner_pool = pool
    return pool


def retained_runs(ctx):
    """Inspection view over the same pool; never a second owner store."""
    return [owner for owner in scope_owner_pool(ctx).snapshot()
            if isinstance(owner, Run) and owner.ctx is ctx]


def owned_type_name(named_type):
    return {
        "MilitaryFormationUnitEntry": ".?AU?$SAnyType@V?$CPdxPtr@USDataCombatUnitEntry@@@@@PdxAnyImpl@@",
        "ReleaseCountryWindow": ".?AU?$SAnyType@V?$CPdxPtr@VCReleaseCountryWindow@@@@@PdxAnyImpl@@",
        "PowerBlocCustomizationPopup": ".?AU?$SAnyType@V?$CPdxPtr@VCPowerBlocCustomizationPopup@@@@@PdxAnyImpl@@",
        "MilitaryFormationChangeNamePopup": ".?AU?$SAnyType@V?$CPdxPtr@VCMilitaryFormationNameChangePopup@@@@@PdxAnyImpl@@",
        "ShipChangeNamePopup": ".?AU?$SAnyType@V?$CPdxPtr@VCShipChangeNamePopup@@@@@PdxAnyImpl@@",
    }.get(named_type, ".?AU?$SAnyType@V?$CPdxPtr@VCData" + named_type + "@@@@@PdxAnyImpl@@")


def owned_binding(owner, result, named_type):
    """Bind an original owned Any without requiring a host pointer decoder.

    RAW is a copied payload classification, not a native ownership verdict.
    Keep that original kind/bytes/object untouched. The exact owned Any type
    and adopted handle supply the retained binding's native conversion.
    """
    native = getattr(result, "result", result)
    native_name = getattr(native, "native_type_name", None)
    actual_type = getattr(native, "type_id", None)
    handle = result.value_handle
    producer = getattr(owner, "value_adapters", {}).get(handle)
    if (native_name != owned_type_name(named_type)
            or not handle or handle not in owner.values or producer is None
            or type(actual_type) is not int or actual_type < 0):
        raise RuntimeError("Native " + named_type + " wrapper ownership is not established")
    return data.ObjectBinding(actual_type, handle, retained=True)


class Run:
    """Internal retained ownership. A pending coroutine is owned by ctx.perform."""
    def __init__(self, ctx, label, *, namespace="politics"):
        self.ctx, self.label, self.namespace = ctx, label, namespace
        self.native, self.process, self.adapters = ctx.native, ctx.process, dict(ctx.adapters)
        self.values, self.owners, self.buffers, self.operations = [], [], [], []
        self.value_adapters = {}
        self.world = ctx.world
        self.actor = None
        self.metadata = {}
        self.status = "working"
        self.retained_scope = scope_owner_pool(ctx)
        self.retained_scope.retain(self)
        try:
            self._scope_task = asyncio.current_task()
        except RuntimeError:
            self._scope_task = None
        if self._scope_task is not None:
            self._scope_task.add_done_callback(self._forget_if_empty)
        ctx.diagnostics.append(self)

    def _forget_if_empty(self, _task):
        # A local error before acquiring resources must not leave a dead scope.
        if (not getattr(self, "retain_unknown", False)
                and getattr(self, "uncertain_query", None) is None
                and not self.values and not self.buffers
                and not any(owner.command or self.owner_unsettled(owner) for owner in self.owners)
                and getattr(self, "unadopted_command", None) is None):
            self.status = "complete"
            self.retained_scope.discard(self)

    @property
    def blocking(self):
        return getattr(self.ctx, "blocking", None)

    @property
    def diagnostics(self):
        return self.ctx.diagnostics

    def _remember(self, query):
        return self.ctx._remember(query)

    async def query(self, label, factory, *, owner=None):
        """Existing executor with this scope's captured resources and raw results."""
        return await self.capture(label, factory, owner=owner, qualified=True)

    def _capture_values(self, result, query):
        """Adopt only returned source Anys, including real ordered partial steps."""
        payload = getattr(result, "callee_result", None)
        if isinstance(payload, data.ActionSequenceResult):
            originals = payload.native_results
            adapter = getattr(query, "data_adapter", None)
        else:
            originals = (result,)
            adapter = getattr(query, "adapter", self.adapters["data"])
        for original in originals:
            native = getattr(original, "result", original)
            if getattr(native, "action", None) not in (
                    data.Action.EVALUATE, data.Action.LIST_ELEMENT,
                    data.Action.INVOKE_ACTION, data.Action.CREATE_STATE_NAME_CONTEXT,
                    data.Action.CREATE_OWNED_CONTEXT):
                continue
            handle = getattr(native, "value_handle", 0)
            if not handle:
                continue
            if handle not in self.values:
                self.values.append(handle)
                self.value_adapters[handle] = adapter
            if adapter is None:
                # Preserve an observed Any even if its source adapter metadata is
                # missing. Never release it through the unrelated Access DLL.
                self.retain_unknown = True
                self.uncertain_query = query
        if isinstance(payload, data.ActionSequenceResult) and adapter is not None:
            self.adapters["data"] = adapter

    async def capture(self, label, factory, *, owner=None, qualified=False):
        """Keep the original result and returned Anys; never replay an action."""
        query = None
        def submit():
            nonlocal query
            query = factory()
            return query
        try:
            result = await self.ctx.query(label if qualified else self.namespace + "." + label,
                                          submit, owner=owner)
        except BaseException as error:
            original_query = getattr(error, "query", None) or query
            partial = getattr(error, "partial_result", None)
            if partial is None and original_query is not None:
                partial = getattr(original_query, "partial_result", None)
            if partial is not None:
                self.operations.append((label, partial))
                self._capture_values(partial, original_query)
            if original_query is not None or not isinstance(error, (ValueError, TypeError)):
                self.retain_unknown = True
                self.uncertain_query = original_query
            raise
        if isinstance(query, data.DataQuery):
            self.adapters["data"] = query.adapter
        self.operations.append((label, result))
        self._capture_values(result, query)
        return result

    @staticmethod
    def _snapshot_complete(result):
        native = getattr(result, "result", result)
        return (native.error == 0
                and len(native.snapshot) == native.snapshot_bytes
                and native.snapshot_bytes == native.total)

    async def complete_action_inputs(self, result, *, label="action_input_snapshot"):
        """Copy missing post-input bytes from the same owned handles once.

        Original action errors and acceptance are never rewritten. This method
        invokes SNAPSHOT only, not the original getter/setter/sequence. Its
        separate attempts retain the original query if a supplement is unknown.
        """
        payload = getattr(result, "callee_result", None)
        originals = payload.native_results if isinstance(payload, data.ActionSequenceResult) else (result,)
        completion = {"native_result": result, "snapshot_attempts": [], "complete": True}
        self.operations.append((label, completion))
        by_handle = {}
        for index, original in enumerate(originals):
            native = getattr(original, "result", original)
            if getattr(native, "action", None) != data.Action.INVOKE_ACTION or not native.value_handle:
                continue
            if self._snapshot_complete(native):
                continue
            handle = native.value_handle
            if handle not in self.values or self.value_adapters.get(handle) is None:
                raise ValueError("post-input Any is not owned by this source scope")
            attempt = by_handle.get(handle)
            if attempt is None:
                attempt = {"step_index": index, "original": original,
                           "step_indices": [], "originals": [], "query": None,
                           "native_result": None, "output_bytes": 4096}
                by_handle[handle] = attempt
                completion["snapshot_attempts"].append(attempt)
            attempt["step_indices"].append(index)
            attempt["originals"].append(original)
            attempt["output_bytes"] = max(attempt["output_bytes"], native.total, native.snapshot_bytes)
        for handle, attempt in by_handle.items():
            index = attempt["step_index"]
            def submit(handle=handle, attempt=attempt):
                query = data.snapshot(self.native, handle,
                    output_bytes=attempt["output_bytes"],
                    adapter=self.value_adapters[handle], process=self.process)
                attempt["query"] = query
                return query
            try:
                copied = await self.capture(label + "." + str(index), submit)
            except BaseException as error:
                attempt["error"] = error
                completion["complete"] = False
                error.snapshot_completion = completion
                raise
            attempt["native_result"] = copied
            attempt["complete"] = self._snapshot_complete(copied)
            if not attempt["complete"]:
                completion["complete"] = False
                self.retain_unknown = True
                self.retention_reason = "original post-input snapshot remains incomplete"
        return completion

    async def call(self, label, factory, *, owner=None, allow=()):
        result = await self.capture(label, factory, owner=owner)
        if getattr(result, "error", 0) not in (0, *allow):
            self.status = "native_error_retained"
            raise KnownResultError(label, result)
        if owner is not None and getattr(owner, "pending", None) is not None:
            self.status = "command_ownership_uncertain_retained"
            raise RuntimeError("Native command ownership is unresolved; original operation retained")
        return result

    async def country(self):
        if self.actor is None:
            self.actor = await self.ctx.country_id()
        return data.ObjectBinding.country(await self.ctx.type_id("Country"), self.actor)

    async def evaluate(self, expression, bindings):
        return await self.call(expression, lambda: data.evaluate(self.native, expression,
            tuple(bindings), output_bytes=65536, adapter=self.adapters["data"], process=self.process))

    async def functions(self, named_type):
        if named_type in self.metadata:
            return self.metadata[named_type]
        named_id = named_type if type(named_type) is int else await self.ctx.type_id(named_type)
        found, start = {}, 0
        while True:
            page = await self.call(str(named_type) + ".functions", lambda start=start: data.enumerate_functions(
                self.native, named_id, start=start, capacity=512,
                adapter=self.adapters["data"], process=self.process))
            for row in page.rows:
                found[row.name] = row
            if page.next >= page.total:
                break
            if page.next <= start:
                raise RuntimeError("Native function metadata did not advance")
            start = page.next
        self.metadata[named_type] = found
        return found

    async def scalar(self, named_type, getter, binding, *, optional=False):
        if optional:
            row = (await self.functions(named_type)).get(getter)
            if row is None or row.argument_count != 0 or not any(row.callbacks):
                return None
        result = await self.evaluate(named_type + "." + getter, (binding,))
        value = result.value
        if isinstance(value, data.FixedPoint):
            return fixed_decimal(value)
        if value is None and not optional:
            raise RuntimeError("Native " + named_type + "." + getter + " is unavailable")
        if not (value is None or isinstance(value, (bool, int, float, str))):
            raise RuntimeError("Native getter did not return a game-level scalar")
        return value

    async def elements(self, expression, bindings, named_type):
        source = await self.evaluate(expression, bindings)
        if not isinstance(source.value, data.NativeList) or not source.value_handle:
            raise RuntimeError("Native politics source did not return an owned list")
        count = await self.call(expression + ".count", lambda: data.list_count(self.native,
            source.value_handle, adapter=self.adapters["data"], process=self.process))
        if isinstance(count.total, bool) or not isinstance(count.total, int) or count.total < 0:
            raise RuntimeError("Native politics list count is unavailable")
        for index in range(count.total):
            element = await self.call(expression + ".element", lambda index=index: data.list_element(
                self.native, source.value_handle, index, adapter=self.adapters["data"], process=self.process))
            yield index, element, self.owned_binding(element, named_type)

    def owned_binding(self, result, named_type):
        """Use the original owned Any header/handle, not a host pointer decoder."""
        return owned_binding(self, result, named_type)

    def wrapper(self, result, named_type, *, borrowed=False):
        value = result.value
        owned = owned_type_name(named_type)
        raw = (".?AU?$SAnyType@PEAVCData" + named_type + "@@@PdxAnyImpl@@",
               ".?AU?$SAnyType@PEBVCData" + named_type + "@@@PdxAnyImpl@@")
        expected = raw if borrowed else (owned,)
        if (not isinstance(value, data.NativeObject) or value.native_type_name not in expected
                or not value.pointer or not result.value_handle or result.value_handle not in self.values):
            raise RuntimeError("Native " + named_type + " wrapper ownership is not established")
        return value

    async def origin(self, result, named_type, size=4, *, borrowed=False):
        # Only the immutable +0x10 origin of a genuine retained Data wrapper is
        # copied. An owned parent graph must remain held for proven borrowed
        # Decree.GetType output. No native object/array pointer is dereferenced.
        value = self.wrapper(result, named_type, borrowed=borrowed)
        process = self.process
        address = process.allocate(size)
        self.buffers.append(address)
        process.write(address, bytes(size))
        result = await self.call(named_type + ".origin", lambda: access.submit_call(self.native,
            process.resolve_export("ntdll.dll", "memcpy"), (address, value.pointer + 0x10, size),
            mode=1, flag=0, dispatch="interface", adapter=self.adapters["access"],
            call_adapter=self.adapters["call"], process=process))
        callee = result.callee_result
        if callee is None or callee.error or callee.rax != address:
            raise RuntimeError("Native definition/reference copy is incomplete")
        copied = process.read(address, size)
        if len(copied) != size:
            raise RuntimeError("Native definition/reference copy is incomplete")
        process.free(address)
        self.buffers.remove(address)
        return int.from_bytes(copied, "little")

    @staticmethod
    def owner_unsettled(owner):
        return (owner.pending is not None or
                (owner.attempt is not None and owner.attempt.outcome in ("pending", "unknown")))

    async def command_result(self, label, factory, *, owner):
        """Return the original result, including native errors and unknown ownership."""
        result = await self.ctx.query(self.namespace + "." + label, factory, owner=owner)
        self.operations.append((label, result))
        return result

    async def validate_submit(self, owner, result, *, validate_only=False):
        validation = await self.command_result("command.validate", lambda: owner.validate(capacity=65536), owner=owner)
        result.update(validation=validation, native_error=validation.error, reason=validation.reason or None,
                      eligible=True if validation.validated else
                               False if validation.error in (0, 122) else None)
        if self.owner_unsettled(owner) or validation.error not in (0, 122):
            result.update(status="native_result", native_result=validation,
                          native_outcome=owner.attempt.outcome if owner.attempt else None)
            return False
        if validate_only:
            result.update(status="available" if validation.validated else "rejected", accepted=None)
            return False
        if not validation.validated:
            result.update(status="rejected", accepted=False)
            return False
        submitted = await self.command_result("command.submit", lambda: owner.submit(flags=4), owner=owner)
        result.update(native_result=submitted, native_error=submitted.error,
                      native_outcome=owner.attempt.outcome if owner.attempt else None,
                      accepted=True if submitted.accepted else False if not submitted.error else None,
                      status="accepted" if submitted.accepted else
                             "rejected" if not submitted.error else "native_result")
        self.public_result = result
        return bool(result["accepted"]) and not self.owner_unsettled(owner)

    async def generated_owner(self, expression, bindings):
        """Own one clone of an original populated command; never serialize or submit."""
        made = await self.evaluate(expression, bindings)
        if not isinstance(made.value, data.NativeDataCommand):
            raise SourceUnavailable(expression + ": original producer did not return a native command")
        carrier_result = await self.call(expression + ".carrier", lambda: data.command_value(self.native,
            made.value_handle, adapter=self.adapters["data"], process=self.process))
        carrier = carrier_result.value
        if (not isinstance(carrier, data.NativeDataCommand) or not carrier.primary_pointer
                or not carrier.shared_control or not carrier.generation_world):
            raise SourceUnavailable(expression + ": original command carrier has no owned command/world")
        clone = await self.ctx.query(self.namespace + ".command.clone", lambda: commands.request(self.native,
            commands.Action.CLONE, command=carrier.primary_pointer, world=carrier.generation_world,
            capacity=8, adapter=self.adapters["commands"], process=self.process))
        self.operations.append(("clone", clone))
        if clone.error or not clone.cloned_command:
            if clone.cloned_command:
                self.unadopted_command = clone
            raise KnownResultError(expression + ".clone", clone)
        return self.adopt(clone, expected_world=carrier.generation_world)

    async def generated(self, expression, bindings, token):
        made = await self.evaluate(expression, bindings)
        if not isinstance(made.value, data.NativeDataCommand):
            raise RuntimeError("Native political action did not produce a command")
        extracted = await self.call(expression + ".carrier", lambda: data.command_value(self.native,
            made.value_handle, adapter=self.adapters["data"], process=self.process))
        carrier = extracted.value
        if (not isinstance(carrier, data.NativeDataCommand) or carrier.generation_world != self.world
                or not carrier.primary_pointer or not carrier.shared_control
                or carrier.game_command_pointer != carrier.primary_pointer + 0x18):
            raise RuntimeError("Native command source world is unavailable or changed")
        clone = await self.call(expression + ".clone", lambda: commands.request(self.native,
            commands.Action.CLONE, command=carrier.primary_pointer, world=carrier.generation_world,
            capacity=8, adapter=self.adapters["commands"], process=self.process))
        owner = self.adopt(clone)
        if owner.command == carrier.primary_pointer:
            raise RuntimeError("Native command clone aliases shared storage")
        tree = await self.serialize(owner, token)
        return owner, tree

    def adopt(self, result, *, expected_world=None):
        owner = commands.NativeCommand.from_result(self.native, result,
            adapter=self.adapters["commands"], process=self.process)
        self.owners.append(owner)
        expected = self.world if expected_world is None else expected_world
        if owner.world != expected:
            raise RuntimeError("Native command belongs to another source world")
        return owner

    async def create(self, token, parameters):
        await self.country()
        result = await self.call("create", lambda: commands.create(self.native, token,
            actor_id=self.actor, parameters=parameters, adapter=self.adapters["commands"], process=self.process))
        owner = self.adopt(result)
        return owner, await self.serialize(owner, token)

    async def serialize(self, owner, token):
        result = await self.call("serialize", owner.serialize, owner=owner)
        tree = commands.decode_command(result.data)
        if owner.type_token != token or tree.key != commands.Token(token) or commands.encode_tree(tree) != result.data:
            raise RuntimeError("Native political command type/wire differs")
        await self.country()
        if command_field(tree, 10304) != commands.UInt32(self.actor):
            raise RuntimeError("Native political command actor differs from selected country")
        return tree

    async def validate(self, owner):
        return await self.call("validate", lambda: owner.validate(capacity=65536), owner=owner, allow=(122,))

    async def destroy(self, owner):
        if owner.command:
            result = await self.call("destroy", owner.destroy, owner=owner)
            if owner.command or result.command:
                raise RuntimeError("Native political command cleanup is unresolved")

    async def submit(self, owner, action, **values):
        validation = await self.validate(owner)
        if not validation.validated:
            await self.destroy(owner)
            return rejected(action, validation.reason or "Native rules reject this action", **values)
        result = await self.call(action + ".submit", lambda: owner.submit(flags=4), owner=owner)
        if result.command or owner.command:
            raise RuntimeError("Native political action delivery is unresolved")
        if not result.accepted:
            return rejected(action, "Game receiver rejected this action", **values)
        return dict(action=action, status="accepted", accepted=True, reason=None, **values)

    async def finish(self, result):
        """Finish known ownership; cleanup diagnostics never erase the main result.

        This method does not register or discard a run. The caller keeps its
        existing scope until status is complete; unknown work retains this run.
        """
        self.public_result = result
        if (getattr(self, "unadopted_command", None) is not None or getattr(self, "retain_unknown", False)
                or any(self.owner_unsettled(owner) for owner in self.owners)):
            self.status = "retained_pending_or_unknown"
            result["cleanup"] = {"status": self.status}
            return result
        try:
            await self.cleanup()
        except KnownResultError as error:
            self.status = "native_error_retained"
            result["cleanup"] = {"status": self.status, "reason": str(error), "native_result": error.result}
        except Exception as error:
            self.status = "cleanup_unknown_retained"
            self.retain_unknown = True
            result["cleanup"] = {"status": self.status, "reason": str(error), "query": getattr(error, "query", None)}
        return result

    async def cleanup(self):
        if (getattr(self, "retain_unknown", False) or getattr(self, "unadopted_command", None) is not None
                or any(self.owner_unsettled(owner) for owner in self.owners)):
            self.status = "retained_pending_or_unknown"
            raise RuntimeError("Native scope has unresolved work; original graph retained")
        for owner in reversed(self.owners):
            await self.destroy(owner)
        for handle in reversed(tuple(self.values)):
            result = await self.call("release", lambda handle=handle: data.release(self.native,
                handle, adapter=self.value_adapters.get(handle, self.adapters["data"]), process=self.process))
            if result.value_handle:
                raise RuntimeError("Native value release is unresolved")
            self.values.remove(handle)
            self.value_adapters.pop(handle, None)
        if self.buffers:
            raise RuntimeError("Native politics buffers are unresolved")
        self.status = "complete"
        self.retained_scope.discard(self)
        if self._scope_task is not None:
            self._scope_task.remove_done_callback(self._forget_if_empty)
            self._scope_task = None
