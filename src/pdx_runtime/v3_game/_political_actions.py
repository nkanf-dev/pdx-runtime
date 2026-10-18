"""One retained V3 operation graph composed from the existing native owner.

Reads and selected actions share Run/context ownership. This module chooses no
policy, never validates candidate lists, and never retries original mutations.
"""
from __future__ import annotations

import inspect
from ._run import KnownResultError, SourceUnavailable, rejected
from . import _run


def _known_owners(run):
    return (getattr(run,"unadopted_command",None) is None
            and not getattr(run,"retain_unknown",False)
            and not any(run.owner_unsettled(owner) for owner in run.owners))


async def owned_operation(api, label, operation, *, namespace="politics", actor_id=None,
                          result=None, perform=True):
    """Retain one source/command graph; report cleanup separately from native facts.

    operation(run, result) updates a result dictionary in place. Thus a completed
    later native error does not erase fields or accepted submission already read.
    Unknown transport work remains with the normal executor and original ledger.
    """
    async def work():
        run=_run.Run(api._ctx,label,namespace=namespace)
        run.actor=actor_id
        # Run joins the one context-owned retained scope. No feature ledger.
        answer={} if result is None else result
        run.public_result=answer
        try:
            await operation(run,answer)
        except KnownResultError as error:
            answer.update(status="native_error",native_error=error.result.error,
                          native_result=error.result,reason=str(error))
        except SourceUnavailable as error:
            answer.update(status="unavailable",reason=str(error))
        # Arbitrary dispatch/transport errors keep the entire graph. No finally
        # releases a parent while an original query may still consume it.
        answer["native_results"]=tuple(run.operations)
        answer["ownership_settled"]=_known_owners(run)
        await run.finish(answer)
        return answer
    return await api._ctx.perform(namespace+"."+label,work) if perform else await work()


async def selected_command(api, label, expression, bindings, *, namespace="politics",
                           actor_id=None, perform=True):
    """Generate one original command, validate once, then submit once if allowed.

    bindings may be a tuple or an async selector(run). A selector retains its
    original source in that same run. Acceptance is not final effect observation.
    """
    answer={"action":label,"expression":expression,"accepted":None,"completed":False,
            "status":"not_submitted","validation":None,"submission":None}
    async def selected(run,result):
        context=bindings(run) if callable(bindings) else bindings
        context=await context if inspect.isawaitable(context) else context
        owner=await run.generated_owner(expression,tuple(context))
        await run.validate_submit(owner,result)
        for stage,native in reversed(run.operations):
            if stage=="command.submit":
                result["submission"]=native
                break
    return await owned_operation(api,label,selected,namespace=namespace,actor_id=actor_id,
                                 result=answer,perform=perform)


async def selected_factory(api, label, type_token, parameters, *, namespace="politics",
                           actor_id=None, perform=True):
    """Fill one original registered factory with its proven complete native schema.

    ``parameters`` is a native parameter mapping or an async selector(run). The
    selector keeps source reads in this same operation graph. This function
    neither invents fields nor serializes a second identity receipt. Every call
    uses a fresh factory: readers which append vectors must not receive a
    previously populated command. Native creation errors and partial pointers
    remain available without guessing ownership.
    """
    from .. import v3_commands as commands
    answer={"action":label,"type_token":type_token,"accepted":None,"completed":False,
            "status":"not_submitted","creation":None,"validation":None,"submission":None}
    async def selected(run,result):
        arguments=parameters(run) if callable(parameters) else parameters
        arguments=await arguments if inspect.isawaitable(arguments) else arguments
        if run.actor is None:
            run.actor=await run.ctx.country_id()
        result["actor_country_id"]=run.actor
        created=await run.capture("command.create",lambda:commands.create(
            run.native,type_token,actor_id=run.actor,parameters=arguments,
            adapter=run.adapters["commands"],process=run.process))
        result.update(creation=created,native_result=created,native_error=created.error)
        if created.error or not created.command:
            if created.command:
                run.unadopted_command=created
            result["status"]="native_result"
            return
        # This is the original factory callback's generation world, not a later
        # observer stamp or the context's possibly cold/stale cached world.
        owner=run.adopt(created,expected_world=created.world)
        await run.validate_submit(owner,result)
        for stage,native in reversed(run.operations):
            if stage=="command.submit":
                result["submission"]=native
                break
    return await owned_operation(api,label,selected,namespace=namespace,actor_id=actor_id,
                                 result=answer,perform=perform)


async def selected_property_action(api,label,expression,bindings,*,condition,
                                   namespace="politics",actor_id=None,perform=True):
    """Invoke one selected original UI property action after its native predicate.

    Void property actions do not fabricate a GameCommand carrier. The native
    predicate and action are each called once. Callback invoked/accepted flags
    are returned as such, never reported as final gameplay effect completion.
    """
    from .. import v3_data as data
    if not condition:
        raise ValueError("a selected property action needs its original native condition")
    answer={"action":label,"expression":expression,"accepted":None,"completed":False,
            "status":"not_invoked","validation":None,"invocation":None,"invoked":False}
    async def selected(run,result):
        context=bindings(run) if callable(bindings) else bindings
        context=await context if inspect.isawaitable(context) else context
        context=tuple(context)
        validation=await run.capture("selected.condition",lambda:data.evaluate(
            run.native,condition,context,output_bytes=65536,
            adapter=run.adapters["data"],process=run.process))
        result["validation"]=validation
        if validation.error:
            result.update(status="native_result",native_result=validation,native_error=validation.error)
            return
        if validation.kind!=data.Kind.BOOL or type(validation.value) is not bool:
            result.update(status="native_result",native_result=validation,
                          reason="original selected predicate did not return a native bool")
            return
        result["eligible"]=validation.value
        if not validation.value:
            result.update(status="rejected",accepted=False)
            return
        invocation=await run.capture("selected.invoke",lambda:data.invoke_property_action(
            run.native,expression,context,output_bytes=65536,
            adapter=run.adapters["data"],process=run.process))
        result.update(invocation=invocation,native_result=invocation,native_error=invocation.error,
                      invoked=bool(invocation.invoked),accepted=bool(invocation.accepted),
                      status="invoked" if invocation.invoked and not invocation.error else "native_result",
                      acceptance_scope="original property action callback; not a final gameplay effect")
    return await owned_operation(api,label,selected,namespace=namespace,actor_id=actor_id,
                                 result=answer,perform=perform)
