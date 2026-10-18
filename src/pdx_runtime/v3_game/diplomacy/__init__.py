"""Game-facing diplomacy on the caller's existing V3 session.

IDs below are complete native instance IDs, not slots. Native command receipts
and independently observed effects stay distinct. No method changes the player,
connects a process, installs a DLL, or chooses whether a proposal is desirable.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy

from ... import v3_commands as commands, v3_data as data, v3_objects as objects
from ... import v3_player_control as player, v3_script as script
from ..country import _plain_name
from .._values import fixed_decimal
from . import _treaty_proposals as _treaty_proposals
from .relations import DiplomacyReadMixin
from .plays import DiplomaticPlayMixin
from .bloc_actions import BlocActionsMixin
from .play_options import PlayOptionsMixin
from .actions import DiplomaticActionsMixin
from .country_details import CountryDiplomacyMixin
from .._run import rejected, scope_owner_pool
from .. import _run


# Resource-model metadata, not a claim about draft/in-force/rejection timing.
_TREATY_CAPACITY_COST = {
    "cost_model": "capacity_allocation",
    "cost_period": None,
    "cost_period_status": "not_applicable",
    "cost_period_reason": "Influence is capacity, not a stored balance or a periodic charge.",
    "cost_unit_label": "influence points",
}


class TreatyProposal(dict):
    """A normal proposal result with a fresh, read-only effect observation."""
    def __init__(self, api, terms, result):
        super().__init__(result)
        self._api, self._terms = api, terms

    async def observe(self):
        return await self._api.treaty_status(self._terms)


class PreparedTreaty(dict):
    """Copied, game-level treaty terms. No native command owner is kept here.

    ``await terms.propose()`` is the explicit sending intent. Previewing or
    abandoning these terms never sends a proposal and needs no player cleanup.
    """
    def __init__(self, api, actor, target, world, keys, binding_days, public, *, image_base=None):
        super().__init__(public)
        self._api, self._actor, self._target, self._world = api, actor, target, world
        self._keys, self._binding_days, self._task = tuple(keys), binding_days, None
        self._before, self._result, self._failure = None, None, None
        self._image = image_base
        self._receipt, self._proposal_state = None, None
        self._delivery, self._observation = None, {}
        self._observation_task, self._observation_state = None, None

    def _articles(self):
        return [{"key": key, "kind": "mutual", "parties": [self._actor, self._target],
            "obligation_scope": "both_parties"} for key in self._keys]

    def _check_semantics(self):
        expected = {"target_id": self._target, "parties": [self._actor, self._target],
            "articles": self._articles(), "binding_days": self._binding_days}
        if any(self.get(name) != value for name, value in expected.items()):
            raise ValueError("prepared treaty terms were edited; prepare the intended country, articles and commitment again")

    async def propose(self):
        return await self._api.propose_treaty(self)

    async def status(self):
        return await self._api.treaty_status(self)


class DiplomacyAPI(DiplomacyReadMixin, DiplomaticPlayMixin, BlocActionsMixin, PlayOptionsMixin, DiplomaticActionsMixin, CountryDiplomacyMixin):
    def __init__(self, ctx):
        self._ctx = ctx
        self._responses = {}
        self._country_function_epoch = None
        self._country_function_rows = ()

    def _adapter(self, role):
        defaults = {"data": "pdx_v3_data.dll", "objects": "pdx_v3_objects.dll",
            "commands": "pdx_v3_commands.dll", "access": "pdx_v3_access.dll",
            "call": "pdx_native_call.dll", "script": "pdx_v3_script.dll",
            "console": "pdx_v3_console.dll"}
        return self._ctx.adapters.get(role, defaults[role])

    def _remember(self, label, state):
        diagnostics = self._ctx.diagnostics
        record = {"label": label, "state": state}
        if hasattr(diagnostics, "append"):
            diagnostics.append(record)
        else:  # Older injected contexts expose a plain diagnostic mapping.
            history = diagnostics.setdefault("diplomacy", [])
            history.append(record)
            del history[:-64]
        return state

    def _retain_state(self, state, kind, label):
        # Both kinds share the session's one identity pool. Diagnostics can be
        # bounded or cleared without abandoning the original native graph.
        scope_owner_pool(self._ctx).retain(state)
        self._remember(label, state)
        return state

    @staticmethod
    def _state_can_retire(state):
        if (state.get("pending") is not None or state.get("owned_buffers") or state.get("owned_values")
                or state.get("unadopted_clone") is not None or state.get("unsettled_clone") is not None
                or state.get("handoff_uncertain") or state.get("cleanup_uncertain")):
            return False
        queries = list(state.get("queries", ()))
        if state.get("last_command_query") is not None:
            queries.append(state["last_command_query"])
        for name in ("unique_owner", "staging_owner", "owner", "clone_owner"):
            owner = state.get(name)
            if owner is None:
                continue
            attempt = getattr(owner, "attempt", None)
            if (getattr(owner, "command", 0) or getattr(owner, "pending", None) is not None
                    or getattr(attempt, "outcome", None) in ("pending", "unknown")):
                return False
            query = getattr(attempt, "query", None)
            if query is not None:
                queries.append(query)
        # A copied/cached result proves completion, not successful storage release.
        return all(getattr(query, "_released", False) is True for query in queries)

    def _retire_state(self, state):
        if not self._state_can_retire(state):
            return False
        scope_owner_pool(self._ctx).discard(state)
        # Mature recipes still use their guards. Remove only this known-cleaned
        # graph, never another attempt or an uncertain owner.
        for module in (_treaty_proposals.mature, _treaty_proposals.mature.accept):
            attempts = getattr(module, "ATTEMPTS", None)
            if attempts is not None:
                attempts[:] = [item for item in attempts if item is not state]
        return True

    def _read_state(self, backend, actor, label, *, world=None, image=None):
        state = backend.state_for(self._ctx, actor)
        if world is not None:
            state["world"] = world
        if image is not None:
            state["image_base"] = image
        return self._retain_state(state, "read", label)

    async def _cleanup_proposal_state(self, backend, state, label):
        try:
            settled = await backend.cleanup(state)
        except Exception as error:
            state["cleanup_failure"] = error
            settled = False
        if not settled or not self._retire_state(state):
            self._retain_state(state, "proposal", label)
            return False
        return True

    async def _run(self, label, operation):
        return await self._ctx.perform("diplomacy." + label, operation)

    async def _resolve(self, target):
        # An explicit full ID is an input, not a request for extra World/Objects
        # confidence receipts. The original selected native validator decides
        # presence/legality. Tags reuse the shared actual native resolver.
        from .._political_data import resolve_country_id
        if target is None:
            raise ValueError("choose an explicit diplomatic target country")
        self._ctx.adapters.setdefault("console", self._adapter("console"))
        return await resolve_country_id(self._ctx, target)

    async def _copied_read(self, label, factory, copy):
        """Publish copied data before cleanup, using the existing context scope."""
        run = _run.Run(self._ctx, label, namespace="diplomacy")
        report = {}
        run.public_result = report
        query, finishing = None, False
        def submit():
            nonlocal query
            query = factory(run)
            return query
        try:
            native = await run.capture(label, submit, qualified=True)
            report.update(native_result=native, native_error=native.error)
            report["value"] = copy(native)
            finishing = True
            await run.finish(report)
        except BaseException as error:
            if "native_result" not in report:
                original_query = getattr(error, "query", None) or query or getattr(run, "uncertain_query", None)
                partial = getattr(error, "partial_result", None)
                if partial is None:
                    partial = getattr(original_query, "partial_result", None)
                if partial is None:
                    partial = getattr(original_query, "_result", None)
                if partial is not None:
                    report.update(native_result=partial, native_error=partial.error)
                    run._capture_values(partial, original_query)
                    try:
                        report["value"] = copy(partial)
                    except BaseException as copy_error:
                        report["copy_error"] = copy_error
            elif not finishing and not getattr(run, "retain_unknown", False):
                try:
                    await run.finish(report)
                except BaseException as cleanup_error:
                    report["cleanup_error"] = cleanup_error
            report["native_results"] = tuple(run.operations)
            try:
                if not hasattr(error, "observed_result"):
                    error.observed_result = report
                error.diplomacy_read_result = report
                error.diplomacy_read_scope = run
            except BaseException:
                pass  # A diagnostic attachment cannot replace the original error/cancel.
            raise
        report["native_results"] = tuple(run.operations)
        self._remember(label + ".copied_result", report)
        return report["value"]

    async def _scalar(self, expression, binding):
        """Copy native scalar/string/raw bytes; cleanup warnings stay separate."""
        def copied(result):
            self._remember(expression, result)
            if result.error:
                return None
            if result.kind not in (data.Kind.RAW, data.Kind.STRING, data.Kind.BOOL, data.Kind.I32,
                    data.Kind.U32, data.Kind.I64, data.Kind.U64, data.Kind.FLOAT, data.Kind.DOUBLE,
                    data.Kind.FIXED, data.Kind.EMPTY):
                raise RuntimeError("expected a native copied scalar, not a borrowed object")
            return result.value
        return await self._copied_read("diplomacy." + expression, lambda run: data.evaluate(
            run.native, expression, (binding,), output_bytes=65536,
            adapter=run.adapters["data"], process=run.process), copied)

    async def _country_label(self, full_id):
        binding = data.ObjectBinding.country(await self._ctx.type_id("Country"), full_id)
        # Both are real registered Country getters; failed values remain absent.
        tag = await self._scalar("Country.GetTagName", binding)
        name = await self._scalar("Country.GetName", binding)
        self._remember("country_display_original", {"country_id": full_id, "raw_tag": tag, "raw_name": name})
        return {"id": full_id, "tag": tag, "name": _plain_name(name) if isinstance(name, str) else name,
            "unavailable": {field: "original native getter unavailable or rejected; native receipt retained in diagnostics"
                for field, value in (("tag", tag), ("name", name)) if value is None}}

    def _treaty_backend(self):
        # Same native/process/session. Fill role aliases only; never connect.
        for role in ("data", "objects", "commands", "access", "call", "script", "console"):
            self._ctx.adapters.setdefault(role, self._adapter(role))
        return _treaty_proposals

    async def _article_catalog(self):
        epoch = (self._ctx.world, self._ctx.image_base)
        if getattr(self, "_article_catalog_epoch", None) == epoch:
            return self._article_catalog_rows
        backend = self._treaty_backend()
        state = self._read_state(backend, await self._ctx.country_id(), "article_type_catalog")
        try:
            rows = await backend.article_types(self._ctx, state)
        finally:
            self._retire_state(state)
        self._article_catalog_epoch, self._article_catalog_rows = epoch, tuple(rows)
        return self._article_catalog_rows

    @staticmethod
    def _article_keys(articles):
        if isinstance(articles, str):
            articles = (articles,)
        if not isinstance(articles, (list, tuple)) or not articles:
            raise ValueError("choose one or more treaty article keys")
        result = []
        for item in articles:
            if isinstance(item, dict):
                key = item.get("key", item.get("article"))
                extra = set(item) - {"key", "article"}
                if extra:
                    raise ValueError("this mutual terms path has no directed or input fields: " + ", ".join(sorted(extra)))
            else:
                key = item
            if not isinstance(key, str) or not key or "\0" in key:
                raise ValueError("an article must have a genuine nonempty treaty article key")
            result.append(key)  # Native per-type usage limits decide duplicates.
        return tuple(result)

    async def treaty_articles(self):
        """List the current native treaty ArticleType keys and mutual/directed kind.

        This is the loaded typed registry, not the old diplomatic-pact action
        table. A type's existence does not imply eligibility with every country.
        """
        async def run():
            await self._ctx.refresh()
            rows = await self._article_catalog()
            return [{"key": row["key"], "kind": row["kind"],
                "input_requirements": None,
                "input_requirements_unavailable_reason": "the catalog copies keys and kind; selected terms are checked by native treaty validation",
                "cost": None, "cost_unavailable_reason": "select a country and articles to calculate the native treaty cost"}
                for row in rows]
        return await self._run("treaty_articles", run)

    async def _treaty_prepare(self, target, articles, binding_days):
        await self._ctx.refresh()
        source_world, source_image = self._ctx.world, self._ctx.image_base
        actor, other = await self._ctx.country_id(), await self._resolve(target)
        if actor == other:
            raise ValueError("a treaty needs a different country")
        keys = self._article_keys(articles)
        # The original typed Reader resolves these definition keys. Exact native
        # Serialize must return the same keys/refs, and the actual definition's
        # kind is checked before its cost is used. A separate catalog is not a
        # prerequisite or an allowlist for a caller's explicit game terms.
        backend, state = self._treaty_backend(), None
        def retain(created):
            nonlocal state
            state = self._retain_state(created, "proposal", "preparing_treaty_preview")
        try:
            state = await backend.prepare(self._ctx, actor, actor, other, keys, binding_days=binding_days,
                expected_world=source_world, expected_image=source_image, on_state=retain)
            self._retain_state(state, "proposal", "prepared_treaty_preview")
            self._remember("prepared_treaty_preview", state)
            validation = state["validation"]
            public = {"status": "ready" if validation.validated else "unavailable", "target_id": other,
                "parties": [actor, other], "articles": [{"key": key, "kind": "mutual", "parties": [actor, other],
                    "obligation_scope": "both_parties"} for key in keys],
                "binding_days": state["binding_days"], "binding_days_source": state.get("binding_days_source", "explicit"),
                "binding_period_is_commitment": True,
                "can_propose": bool(validation.validated),
                "reason": None if validation.validated else _plain_name(validation.reason) or "the game rejected these treaty terms",
                "maintenance_cost": fixed_decimal(state["cost_source"]["value"]), "cost_unit": "influence",
                "party_costs": [{"country_id": party, "maintenance_cost": fixed_decimal(value), "unit": "influence"}
                    for party, value in state["cost_source"].get("party_totals", {actor: state["cost_source"]["value"]}).items()],
                "ai_acceptance": None,
                "ai_acceptance_unavailable_reason": "the original AI score requires an initialized TreatyDraft model; the headless command path does not expose that model",
                "submitted": False, "in_force": False}
            public.update(_TREATY_CAPACITY_COST)
            return PreparedTreaty(self, actor, other, state["world"], keys, state["binding_days"], public, image_base=source_image)
        except BaseException as error:
            state = state or getattr(error, "query_state", None)
            if state is not None:
                self._remember("treaty_preview_retained", state)
            raise
        finally:
            if state is not None:
                await self._cleanup_proposal_state(backend, state, "treaty_preview_unsettled")

    async def prepare_treaty(self, target, *, articles, binding_days=None):
        """Preview mutual terms, native maintenance cost and Validate reason.

        Article keys are game terms, e.g. ``articles=['defensive_pact']``. Omitted
        binding_days uses the game's native default commitment, never a journal
        countdown. The returned source distinguishes that default from an
        explicit period. No proposal is sent. Known
        native command storage is released before returning copied terms.
        """
        keys = self._article_keys(articles)
        return await self._run("prepare_treaty", lambda: self._treaty_prepare(target, keys, binding_days))

    async def treaty_options(self, target, *, articles=None, binding_days=None):
        """Discover real article keys, or evaluate explicitly selected mutual terms.

        No article is selected as a national strategy. Pass article keys to get
        the current native cost/constraints for that exact country and treaty.
        Native validation is not a forecast of AI acceptance.
        """
        if articles is not None:
            return await self.prepare_treaty(target, articles=articles, binding_days=binding_days)
        async def run():
            await self._ctx.refresh()
            actor, other = await self._ctx.country_id(), await self._resolve(target)
            if actor == other:
                raise ValueError("treaty options require a different country")
            rows = await self._article_catalog()
            return {"target_id": other, "article_types": [{"key": row["key"], "kind": row["kind"]} for row in rows],
                "catalog_complete": True, "catalog_source": "current native TreatyArticleType registry",
                "can_propose": None, "reason": "choose article terms to check current native conditions and cost",
                "ai_acceptance": None, "ai_acceptance_unavailable_reason": "no initialized TreatyDraft model was created by this catalog read"}
        return await self._run("treaty_options", run)

    async def _treaty_snapshot(self, state, actor):
        draft = self._ctx.support('diplomacy.treaty.draft')
        observation = await draft.discover_drafts(_treaty_proposals.mature, state,
            country_full_id=actor, runtime_adapter=self._ctx.adapters.get("runtime", "pdx_runtime.dll"))
        if not observation["complete"]:
            raise RuntimeError("the original country's complete treaty references could not be observed")
        return observation

    @staticmethod
    def _trigger_key(key):
        if not key or any(char.isspace() or char in '{}=#"\\\0' for char in key):
            raise ValueError("the real article key needs native script quoting not provided by this terms query")
        return key

    async def _has_articles(self, treaty_id, keys, *, read_state=None):
        # Exact journal semantics, evaluated on this actual full-ID Treaty.
        body = "\n".join("any_scope_article = { has_type = " + self._trigger_key(key) + " }" for key in keys)
        def make():
            try:
                query = script.trigger(self._ctx.native, body, script.Scope.id(0x65, treaty_id),
                    adapter=self._adapter("script"), process=self._ctx.process)
            except BaseException as error:
                if read_state is not None:
                    read_state["pending"] = {"label": "verify_treaty_articles", "query": getattr(error, "query", None)}
                raise
            if read_state is not None:
                read_state["pending"] = {"label": "verify_treaty_articles", "query": query}
            return query
        result = await self._ctx.query("diplomacy.verify_treaty_articles", make)
        if read_state is not None:
            read_state["last_command_query"] = read_state["pending"]["query"]
            read_state["pending"] = None
        self._remember("treaty_article_membership", result)
        if result.error or not result.scope_valid or not result.parsed or not result.executed or result.boolean is None:
            raise RuntimeError("native treaty/article membership could not be evaluated")
        return result.boolean

    async def _observe_treaty_proposal(self, terms, state, before):
        after = await self._treaty_snapshot(state, terms._actor)
        known_id = getattr(terms, "_observed_treaty_id", None)
        if known_id is not None:
            # A full ref once uniquely observed belongs to this history. Never
            # substitute a later same-terms treaty if that ref becomes unlinked.
            rows = [row for row in after["all_treaties"] if row["full_ref"] == known_id]
            row = rows[0] if len(rows) == 1 else None
            member = row is not None
            parties_match = bool(row and set(row["party_full_refs"]) == {terms._actor, terms._target})
            matches = await self._has_articles(known_id, terms._keys, read_state=state) if parties_match else None
            current_match = bool(member and parties_match and matches)
            return {"id": known_id, "proposal_created": True,
                "current_country_membership": member, "currently_observed": member,
                "raw_is_draft": row["is_draft"] if row else None,
                "requested_terms_match": matches if parties_match else None,
                "in_force": not row["is_draft"] if current_match else None,
                "status": ("proposed" if row["is_draft"] else "in_force") if current_match else
                    "known_treaty_terms_changed" if member else "not_in_current_country_treaties",
                "observation_status": "complete", "attribution_unique": True,
                "matching_treaty_ids": [known_id] if current_match else [],
                "matching_treaties": [{"id": known_id, "in_force": not row["is_draft"]}] if current_match else [],
                "response": None,
                "response_unavailable_reason": "Current membership does not expose the other country's response.",
                "atomic_snapshot": False}
        prior = {row["full_ref"] for row in before["all_treaties"]}
        candidates = []
        for row in after["all_treaties"]:
            if row["full_ref"] in prior or set(row["party_full_refs"]) != {terms._actor, terms._target}:
                continue
            if await self._has_articles(row["full_ref"], terms._keys, read_state=state):
                candidates.append(row)
        known = candidates[0] if len(candidates) == 1 else None
        if known is not None:
            # Lazy for existing PreparedTreaty instances; no class/handle rebuild.
            terms._observed_treaty_id = known["full_ref"]
        ambiguous = len(candidates) > 1
        in_force = None if ambiguous else bool(known and not known["is_draft"])
        return {"id": known["full_ref"] if known else None,
            "proposal_created": None if ambiguous else known is not None, "in_force": in_force,
            "current_country_membership": True if known else None,
            "currently_observed": True if known else None,
            "raw_is_draft": known["is_draft"] if known else None,
            "status": "ambiguous_matching_treaties" if ambiguous else "in_force" if in_force else "proposed" if known else "accepted_not_observed",
            "observation_status": "ambiguous" if ambiguous else "complete", "attribution_unique": False if ambiguous else True if known else None,
            "id_unavailable_reason": "multiple newly observed matching treaties; this proposal is not uniquely attributed" if ambiguous else None,
            "matching_treaty_ids": [row["full_ref"] for row in candidates],
            "matching_treaties": [{"id": row["full_ref"], "in_force": not row["is_draft"]} for row in candidates],
            "atomic_snapshot": False}

    def _proposal_result(self, terms):
        """Restore the public copy from private delivery/observation facts."""
        facts = deepcopy({**(terms._delivery or {}), **terms._observation})
        known_id = getattr(terms, "_observed_treaty_id", None)
        if known_id is not None:
            # Historical creation survives absence, read failures and pending
            # settlement. Current membership/effect is reported separately.
            facts.update(id=known_id, proposal_created=True)
            facts.setdefault("current_country_membership", None)
            facts.setdefault("currently_observed", None)
        if facts.get("cost_unit") == "influence" and "maintenance_cost" in facts:
            facts.update(_TREATY_CAPACITY_COST)
        if terms._result is None:
            terms._result = TreatyProposal(self, terms, facts)
        else:
            dict.clear(terms._result)
            dict.update(terms._result, facts)
        return terms._result

    def _delivery_facts(self, terms, state, *, attempted, submitted, accepted, status, reason=None):
        facts = {"submission_attempted": attempted, "submitted": submitted, "accepted": accepted,
            "target_id": terms._target, "parties": [terms._actor, terms._target],
            "articles": terms._articles(), "binding_days": terms._binding_days,
            "reason": reason, "status": status, "proposal_created": False if submitted is False else None,
            "in_force": False if submitted is False else None, "ai_acceptance": None,
            "observation_status": "not_started"}
        cost = state.get("cost_source") if state is not None else None
        if cost is not None:
            facts.update(maintenance_cost=fixed_decimal(cost["value"]), cost_unit="influence",
                party_costs=[{"country_id": party, "maintenance_cost": fixed_decimal(value), "unit": "influence"}
                    for party, value in cost.get("party_totals", {terms._actor: cost["value"]}).items()])
        terms._delivery = facts
        return self._proposal_result(terms)

    async def _read_treaty_effect(self, terms):
        backend = self._treaty_backend()
        state = self._read_state(backend, terms._actor, "treaty_effect_observation",
            world=terms._world, image=terms._image)
        state["read_task"] = asyncio.current_task()
        terms._observation_state = state
        try:
            terms._observation = await self._observe_treaty_proposal(terms, state, terms._before)
        except Exception as error:
            state["observation_failure"] = error
            terms._observation = {"status": "accepted_observation_unknown", "observation_status": "unknown",
                "observation_error": str(error), "id": None, "proposal_created": None, "in_force": None,
                "attribution_unique": None}
            self._remember("treaty_effect_unknown", state)
        finally:
            self._retire_state(state)
        return self._proposal_result(terms)

    async def _settle_prior_effect_read(self, terms):
        state = terms._observation_state
        if state is None or state.get("pending") is None:
            return True
        state["continuation_task"] = asyncio.current_task()
        pending = state["pending"]
        query = pending.get("query")
        if query is None:
            return False
        if getattr(query, "_result", None) is None:
            if getattr(query, "_released", False):
                return False
            # Resume the exact read, never create another submit or replay it.
            await self._ctx.wait(query)
        if not getattr(query, "_released", False):
            # The callback may have completed, but its original storage has not
            # settled. Release only Core's queue slot, never SDK storage/dtors.
            release_slot = getattr(self._ctx, "_release_query_slot", None)
            if release_slot is not None:
                release_slot(query)
            return False
        # Both facts are required. Core forgets only this exact settled query;
        # this path never calls query.read/_release or an owner destructor again.
        forget = getattr(self._ctx, "_forget", None)
        if forget is not None:
            forget(query)
        state["pending"] = None
        state["last_command_query"] = query
        if not state.get("owned_values") and not state.get("unique_owner") and not state.get("staging_owner"):
            _treaty_proposals.mature._free(state, *tuple(state.get("owned_buffers", ())))
        self._retire_state(state)
        return True

    async def _continue_treaty_effect(self, terms):
        try:
            settled = await self._settle_prior_effect_read(terms)
        except Exception as error:
            terms._observation = {"status": "accepted_observation_unknown", "observation_status": "unknown",
                "observation_error": str(error), "id": None, "proposal_created": None,
                "in_force": None, "attribution_unique": None}
            return self._proposal_result(terms)
        if not settled:
            terms._observation = {"status": "accepted_observation_pending",
                "observation_status": "pending_original_read_settlement",
                "observation_error": "the original effect read or its query storage remains unsettled; no new read was dispatched",
                "id": None, "proposal_created": None, "in_force": None, "attribution_unique": None}
            return self._proposal_result(terms)
        return await self._read_treaty_effect(terms)

    async def _observe_terms(self, terms):
        task = terms._observation_task
        if task is None or task.done():
            # Settlement and any following fresh read share one continuation.
            # Repeated status calls can only join this exact original task.
            terms._observation_task = asyncio.create_task(self._continue_treaty_effect(terms))
        return await asyncio.shield(terms._observation_task)

    async def _send_treaty(self, terms):
        if terms._api is not self:
            raise ValueError("these terms belong to another game session")
        terms._check_semantics()
        if terms._task is not None and terms._task is not asyncio.current_task() and not terms._task.done():
            if terms._delivery is not None and terms._delivery["accepted"] is not None:
                return self._proposal_result(terms)
            return await asyncio.shield(terms._task)
        if terms._result is not None and terms._failure is None:
            return self._proposal_result(terms)
        if terms._failure is not None:
            raise terms._failure
        terms._task = asyncio.current_task()
        await self._ctx.refresh()
        if terms._world != self._ctx.world or terms._actor != await self._ctx.country_id():
            raise ValueError("the campaign or acting country changed; prepare terms for the current country")
        backend, state, cleanup_attempted = self._treaty_backend(), None, False
        def retain(created):
            nonlocal state
            state = self._retain_state(created, "proposal", "preparing_treaty_submission")
            terms._proposal_state = state
        try:
            snapshot_state = self._read_state(backend, terms._actor, "before_treaty_proposal",
                world=terms._world, image=terms._image)
            try:
                terms._before = await self._treaty_snapshot(snapshot_state, terms._actor)
            finally:
                self._retire_state(snapshot_state)
            # Baseline observation is done. Validate is the last native read
            # before Submit, with the exact copied intent checked above.
            state = await backend.prepare(self._ctx, terms._actor, terms._actor, terms._target,
                terms._keys, binding_days=terms._binding_days, expected_world=terms._world,
                expected_image=terms._image, on_state=retain)
            retain(state)
            terms._check_semantics()  # Also catch edits made while native prepare was pending.
            validation = state["validation"]
            if not validation.validated:
                return self._delivery_facts(terms, state, attempted=False, submitted=False, accepted=None,
                    status="unavailable", reason=_plain_name(validation.reason) or "the game rejected these treaty terms")
            self._delivery_facts(terms, state, attempted=True, submitted=None, accepted=None, status="submission_in_progress")
            receipt = await backend.submit(state)
            terms._receipt = receipt
            if state.get("handoff_uncertain"):
                self._delivery_facts(terms, state, attempted=True, submitted=None, accepted=None,
                    status="delivery_unknown", reason="the original native handoff is unknown")
                raise RuntimeError("the original treaty delivery is uncertain; its receipt and owner graph are retained")
            if receipt.error:
                # A known still-owned command was rejected before queue handoff.
                owner = state.get("unique_owner")
                owned = getattr(getattr(owner, "attempt", None), "outcome", None) == "owned"
                self._delivery_facts(terms, state, attempted=True, submitted=False if owned else None, accepted=None,
                    status="not_submitted" if owned else "delivery_unknown", reason="native submit error " + str(receipt.error))
                if not owned:
                    raise RuntimeError("the original treaty delivery is uncertain; its receipt and owner graph are retained")
                return self._proposal_result(terms)
            # Freeze receipt facts BEFORE any effect observation can fail.
            self._delivery_facts(terms, state, attempted=True, submitted=True, accepted=bool(receipt.accepted),
                status="accepted_not_observed" if receipt.accepted else "receiver_rejected")
            cleanup_attempted = True
            await self._cleanup_proposal_state(backend, state, "treaty_proposal_unsettled")
            if receipt.accepted:
                return await self._observe_terms(terms)
            return self._proposal_result(terms)
        except BaseException as error:
            state = state or getattr(error, "query_state", None)
            if state is not None:
                self._retain_state(state, "proposal", "treaty_proposal_retained")
                if terms._delivery is None or terms._delivery["status"] == "submission_in_progress":
                    attempted = state.get("submission_attempted", terms._delivery is not None)
                    self._delivery_facts(terms, state, attempted=attempted,
                        submitted=None if attempted else False, accepted=None,
                        status="delivery_unknown" if attempted else "not_submitted", reason=str(error))
            terms._failure = error
            raise
        finally:
            if state is not None and not cleanup_attempted:
                await self._cleanup_proposal_state(backend, state, "treaty_proposal_unsettled")

    async def propose_treaty(self, target_or_terms, *, articles=None, binding_days=None):
        """Send one explicit formal mutual treaty, not an old diplomatic pact.

        Pass PreparedTreaty from prepare_treaty, or a target plus article keys for
        a one-call workflow. Every final proposal is freshly natively validated.
        Accepted receipt, created proposal and in-force treaty remain distinct.
        Re-entering the same prepared terms waits its original submission task.
        """
        async def run():
            if isinstance(target_or_terms, PreparedTreaty):
                if articles is not None or binding_days is not None:
                    raise ValueError("prepared treaty terms cannot be silently replaced during proposal")
                terms = target_or_terms
            else:
                terms = await self._treaty_prepare(target_or_terms, self._article_keys(articles), binding_days)
            return await self._send_treaty(terms)
        return await self._run("propose_treaty", run)

    async def treaty_status(self, proposal):
        """Fresh effect read; supports a proposal result even before an ID exists."""
        if isinstance(proposal, TreatyProposal):
            proposal = proposal._terms
        if isinstance(proposal, PreparedTreaty):
            terms = proposal
            if terms._api is not self:
                raise ValueError("these treaty terms belong to another game session")
            async def run_terms():
                await self._ctx.refresh()
                if terms._world != self._ctx.world:
                    raise ValueError("this proposal belongs to a previous campaign")
                if terms._delivery is None:
                    return TreatyProposal(self, terms, {"submission_attempted": False, "submitted": False,
                        "accepted": None, "proposal_created": False, "in_force": False,
                        "status": "preparing" if terms._task is not None and not terms._task.done() else "not_submitted"})
                if terms._delivery["accepted"] is not True or terms._before is None:
                    return self._proposal_result(terms)
                return await self._observe_terms(terms)
            return await self._run("treaty_status", run_terms)
        full_id = self._proposal_id(proposal)
        async def run():
            await self._ctx.refresh()
            actor = await self._ctx.country_id()
            backend = self._treaty_backend()
            state = self._read_state(backend, actor, "treaty_status")
            try:
                draft = self._ctx.support('diplomacy.treaty.draft')
                source = await draft.read_country_refs(backend.mature, state, country_full_id=actor,
                    runtime_adapter=self._ctx.adapters.get("runtime", "pdx_runtime.dll"))
                # A copied count may request one larger fresh POD read. Neither
                # an undersized nor failed source is evidence of absence.
                count = source.get("native_count")
                if (not source.get("complete_refs_copied") and source.get("header_copied")
                        and type(count) is int and source.get("owned_slots", 64) < count <= 1024):
                    source = await draft.read_country_refs(backend.mature, state, country_full_id=actor,
                        slots=count, runtime_adapter=self._ctx.adapters.get("runtime", "pdx_runtime.dll"))
                self._remember("treaty_status_country_membership", source)
                row = await backend.mature._proposal_snapshot(state, full_id, "current_treaty_state")
            finally:
                self._retire_state(state)
            if actor not in row["party_full_refs"]:
                raise ValueError("this treaty does not bind the selected country")
            complete = bool(source.get("complete_refs_copied"))
            member = full_id in source["full_refs"] if complete else None
            # Registry lifetime is not current-country membership. A valid
            # unlinked object may retain either draft byte; it proves no reply.
            return {"id": full_id, "parties": list(row["party_full_refs"]), "object_present": True,
                "raw_is_draft": row["is_draft"], "current_country_id": actor,
                "current_country_membership": member, "membership_complete": complete,
                "membership_source": "current_country_treaty_refs",
                "status": ("proposed" if row["is_draft"] else "in_force") if member is True else
                    "not_in_current_country_treaties" if member is False else "membership_unknown",
                "in_force": not row["is_draft"] if member is True else None,
                "response": None,
                "response_unavailable_reason": "Current membership and the object's draft flag do not expose the other country's response.",
                "atomic_snapshot": False}
        return await self._run("treaty_status", run)

    async def countries(self):
        """List current countries with native tags/names. Not a historical tag catalog."""
        async def run():
            await self._ctx.refresh()
            rows, start = [], 0
            while True:
                page = await self._ctx.query("diplomacy.countries", lambda start=start: objects.enumerate_objects(
                    self._ctx.native, objects.Domain.COUNTRY, start_index=start, max_items=128,
                    adapter=self._adapter("objects"), process=self._ctx.process))
                if page.error or page.world != self._ctx.world:
                    raise RuntimeError("current country enumeration failed or changed world")
                for row in page.records:
                    if row.valid:
                        rows.append(await self._country_label(row.resolved_id))
                if page.next_index >= page.slot_bound:
                    return rows
                if page.next_index <= start:
                    raise RuntimeError("native country page did not advance")
                start = page.next_index
        return await self._run("countries", run)

    async def plays(self, *, all_countries=False):
        """Read live diplomatic plays and checked leaders, not invented participants."""
        async def run():
            await self._ctx.refresh()
            actor = await self._ctx.country_id()
            rows, start = [], 0
            while True:
                page = await self._ctx.query("diplomacy.plays", lambda start=start: objects.enumerate_objects(
                    self._ctx.native, objects.Domain.DIPLOMATIC_PLAY, start_index=start, max_items=128,
                    adapter=self._adapter("objects"), process=self._ctx.process))
                if page.error or page.world != self._ctx.world:
                    raise RuntimeError("current diplomatic-play enumeration failed or changed world")
                for row in page.records:
                    if not row.valid:
                        continue
                    attacker = None if row.attacker_leader_error else row.attacker_leader_id
                    defender = None if row.defender_leader_error else row.defender_leader_id
                    if all_countries or actor in (attacker, defender):
                        rows.append({"id": row.resolved_id, "attacker_id": attacker, "defender_id": defender,
                            "attacker": None if attacker is None else await self._country_label(attacker),
                            "defender": None if defender is None else await self._country_label(defender),
                            "participant_coverage": "leaders only"})
                if page.next_index >= page.slot_bound:
                    return rows
                if page.next_index <= start:
                    raise RuntimeError("native diplomatic-play page did not advance")
                start = page.next_index
        return await self._run("plays", run)

    def _query_state(self, actor):
        q = self._ctx.support('diplomacy.treaty.query')
        state = q.new_state(self._ctx.native, self._ctx.process, actor, self._adapter("data"), None)
        state.update(country_id=actor, image_base=self._ctx.image_base, world=self._ctx.world,
            access_adapter=self._adapter("access"), call_adapter=self._adapter("call"),
            script_adapter=self._adapter("script"), commands_adapter=self._adapter("commands"),
            owned_buffers=[], treaties=[], articles=[], types={}, row_errors=0, sources_retained=True,
            pointers_live=True, cleanup_uncertain=False, status="reading_received_treaties")
        self._retain_state(state, "read", "received_treaties")
        return q, state

    @staticmethod
    def _parameter_values(parameters):
        """Keep original typed values, without requiring the command codec API."""
        numeric = ((commands.UInt32, "uint32"), (commands.Int64, "int64"),
            (commands.UInt64, "uint64"), (commands.Float32, "float32"))
        def value(item):
            for cls, kind in numeric:
                if isinstance(item, cls):
                    return {"type": kind, "value": item.value}
            if isinstance(item, commands.Token):
                return {"type": "native_token", "id": item.value}
            if isinstance(item, commands.Assignment):
                return {"field_token": item.key.value, "value": value(item.value)}
            if isinstance(item, tuple):
                return [value(part) for part in item]
            return item
        if parameters is None:
            return None
        result = []
        for parameter in parameters:
            if len(parameter) == 1 and parameter[0].key == commands.Token(0x8F) and isinstance(parameter[0].value, str):
                result.append({"type": "text", "value": parameter[0].value})
            else:
                result.append({"type": "native_parameter", "fields": [value(part) for part in parameter]})
        return result

    async def _drafts(self, *, selected=None, clauses=False, source_expression="Country.GetDraftTreaties"):
        actor = await self._ctx.country_id()
        q, state = self._query_state(actor)
        await q._types(state, 256)
        binding = data.ObjectBinding.country(state["types"]["Country"], actor)
        source = await q._evaluate(state, "received_drafts", source_expression, binding)
        count = await q.complete(state, "received_draft_count", lambda: data.list_count(self._ctx.native,
            source.value_handle, adapter=self._adapter("data"), process=self._ctx.process))
        result = []
        for index in range(count.total):
            element = await q.complete(state, "received_draft_" + str(index), lambda index=index: data.list_element(
                self._ctx.native, source.value_handle, index, adapter=self._adapter("data"), process=self._ctx.process))
            value = q._owned_object(state, element, "CDataTreaty", "Treaty")
            full_ref = await q._full_id(state, "received_draft_ref_" + str(index), value, element.value_handle)
            if selected is not None and full_ref != selected:
                continue
            validity = await q._scope_valid(state, "received_draft_valid_" + str(index), q.TREATY_SCOPE,
                full_ref, element.value_handle)
            if not validity["valid"]:
                raise RuntimeError("received treaty reference changed while reading")
            row = {"query_occurrence": index, "source_index": index, "full_treaty_id": full_ref,
                "native_validity": validity, "country_views": [], "views": []}
            state["treaties"].append(row)
            retained = data.ObjectBinding(state["types"]["Treaty"], element.value_handle, retained=True)
            await q._parties(state, row, retained)
            parties = [party["full_country_id"] for party in row["country_views"] if party["native_validity"]["valid"]]
            if actor not in parties or len(parties) != 2:
                raise RuntimeError("draft parties are not independently valid for this country")
            public = {"id": full_ref, "kind": "treaty", "parties": parties,
                "status": "in_force" if source_expression == "Country.GetInForceTreaties" else "draft",
                "direction": None, "direction_unavailable_reason": "native country draft list does not classify incoming versus outgoing"}
            if clauses:
                await q._articles(state, row, retained)
                public["clauses"] = []
                for article in state["articles"]:
                    if article["source_treaty_full_id"] != full_ref:
                        continue
                    if article["status"] != "owned_article_and_definition_origin_observed":
                        public["clauses"].append({"id": article["full_article_id"], "view": article["view"],
                            "parameters": None, "text": None, "complete": False, "text_complete": False,
                            "reason": "article or original definition changed while reading"})
                        continue
                    reader = self._ctx.support('diplomacy.treaty.article_parameters')
                    read = await reader.read_article_parameters(self._ctx.native, self._ctx.process,
                        article_full_id=article["full_article_id"], image_base=self._ctx.image_base, world=self._ctx.world,
                        data_adapter=self._adapter("data"), access_adapter=self._adapter("access"),
                        call_adapter=self._adapter("call"), script_adapter=self._adapter("script"),
                        runtime_adapter=self._ctx.adapters.get("runtime", "pdx_runtime_bridge.dll"))
                    self._remember("received_article", read)
                    body = read["result"]
                    if body["article_full_id"] != article["full_article_id"] or body["treaty_full_id"] != full_ref:
                        raise RuntimeError("original clause does not belong to the selected treaty")
                    public["clauses"].append({"id": body["article_full_id"], "view": article["view"],
                        "parameters": self._parameter_values(body["parameters"]), "text": body.get("text"),
                        "complete": body["complete"], "text_complete": body["text_complete"]})
                    if not await reader.cleanup_completed(read):
                        raise RuntimeError("received clause native storage is unsettled")
                public["complete"] = all(clause["complete"] for clause in public["clauses"])
            result.append(public)
        await q.source_check(state, "received_source_still_current", source.value_handle)
        await q.cleanup_completed(state)
        self._retire_state(state)
        if state["owned_values"] or state["owned_buffers"]:
            raise RuntimeError("received treaty values remain owned")
        if selected is not None and len(result) != 1:
            raise ValueError("proposal is not unique in this country's current received draft list")
        return result

    async def treaties(self, target=None, *, details=False, articles=None):
        """Read actual in-force treaties, optionally checking real article keys.

        Passing articles=['defensive_pact'] evaluates the original has_type
        trigger on each actual Treaty full ID. It never substitutes an old pact
        flag, a localized name, a proposal receipt or a journal timer.
        """
        keys = self._article_keys(articles) if articles is not None else ()
        async def run():
            await self._ctx.refresh()
            other = await self._resolve(target) if target is not None else None
            rows = await self._drafts(clauses=details, source_expression="Country.GetInForceTreaties")
            rows = [row for row in rows if other is None or other in row["parties"]]
            if keys:
                for row in rows:
                    row["has_requested_articles"] = await self._has_articles(row["id"], keys)
                    row["requested_article_keys"] = list(keys)
            return rows
        return await self._run("treaties", run)

    async def proposals(self):
        """List this country's actual treaty drafts. Inspect terms before deciding."""
        async def run():
            await self._ctx.refresh()
            return await self._drafts()
        return await self._run("proposals", run)

    async def terms(self, proposal_id):
        """Read all three original clause views, including full received free text.

        The native reader never receives caller-submitted/display text. Completeness
        flags remain explicit; a list read is not an atomic cross-tick snapshot.
        """
        if isinstance(proposal_id, PreparedTreaty):
            if proposal_id._api is not self:
                raise ValueError("these treaty terms belong to another game session")
            display = deepcopy(dict(proposal_id))
            if display.get("cost_unit") == "influence" and "maintenance_cost" in display:
                display.update(_TREATY_CAPACITY_COST)
            return display
        proposal_id = self._proposal_id(proposal_id)
        async def run():
            await self._ctx.refresh()
            return (await self._drafts(selected=proposal_id, clauses=True))[0]
        return await self._run("terms", run)

    @staticmethod
    def _proposal_id(value):
        if isinstance(value, dict):
            value = value.get("id")
        if type(value) is not int or not 0 <= value < 0xFFFFFFFF:
            raise ValueError("proposal must have a current full treaty ID")
        return value

    async def _controlled(self, actor):
        current = await self._ctx.query("diplomacy.actual_player", lambda: player.observe_player(
            self._ctx.native, adapter=self._adapter("console"), process=self._ctx.process))
        if current.error or current.controlled_country_error or current.observer_enabled or current.world != self._ctx.world or current.controlled_country_id != actor:
            raise RuntimeError("the acting country is not the actual controlled player in this world")

    @staticmethod
    def _response_error(error, state, result):
        # Keep any original operation facts, including explicit unknown values.
        if not hasattr(error, "query_state"):
            error.query_state = state
        if result is not None and not hasattr(error, "observed_result"):
            error.observed_result = result
        error.response_state = state
        error.response_result = result
        return error

    def _response_primary(self, record, state, result):
        state["public_result"] = result
        if record is not None:
            record["state"], record["result"] = state, result
        return result

    @staticmethod
    def _response_unsettled(state):
        if (state.get("pending") is not None or state.get("handoff_uncertain")
                or state.get("cleanup_uncertain") or state.get("unadopted_clone") is not None
                or state.get("unsettled_clone") is not None):
            return True
        return any(owner is not None and (getattr(owner, "pending", None) is not None
            or getattr(getattr(owner, "attempt", None), "outcome", None) in ("pending", "unknown"))
            for owner in (state.get(name) for name in ("unique_owner", "staging_owner", "owner", "clone_owner")))

    async def _finish_response(self, state, result, cleanup):
        # A primary fact does not authorize release of an uncertain native graph.
        if self._response_unsettled(state):
            result["cleanup"] = {"status": "retained_pending_or_unknown"}
            return result
        try:
            settled = await cleanup(state)
        except asyncio.CancelledError as error:
            result["cleanup"] = {"status": "cancelled_retained", "error": error}
            self._response_error(error, state, result)
            raise
        except Exception as error:
            result["cleanup"] = {"status": "failed_retained", "error": error}
            return result
        if not settled:
            result["cleanup"] = {"status": "unsettled_retained", "result": settled,
                "error": RuntimeError("original response ownership remains unsettled")}
        elif self._retire_state(state):
            result["cleanup"] = {"status": "completed"}
        else:
            result["cleanup"] = {"status": "retained_pending_or_unknown"}
        return result

    async def _respond(self, proposal_id, accept):
        await self._ctx.refresh()
        key = (self._ctx.world, proposal_id)
        previous = self._responses.get(key)
        if previous is not None:
            # A failed original frame is not resumable. Keep its exact exception,
            # including CancelledError, rather than reconstructing a decision.
            if previous["error"] is not None:
                raise previous["error"]
            if previous["task"] is not asyncio.current_task():
                return await asyncio.shield(previous["task"])
            if previous["result"] is not None:
                return previous["result"]
            raise RuntimeError("the original treaty response is still in progress")
        record = {"task": asyncio.current_task(), "result": None, "error": None, "state": None}
        self._responses[key] = record
        try:
            result = await self._respond_once(proposal_id, accept, record=record)
            record["result"] = result
            state = record["state"]
            if (state is not None and result.get("cleanup", {}).get("status") == "completed"
                    and self._state_can_retire(state) and (result.get("completed")
                    or (result.get("native_result") is not None and result.get("accepted") == False)
                    or result.get("status") == "rejected")):
                self._responses.pop(key, None)
            return result
        except BaseException as error:
            record["error"] = error
            if record["state"] is not None:
                self._response_error(error, record["state"], record["result"])
            raise

    async def _respond_once(self, proposal_id, accept, *, record=None):
        actor = await self._ctx.country_id()
        # Fresh exact-ID lookup; no cached list index is reused as identity.
        draft = (await self._drafts(selected=proposal_id, clauses=True))[0]
        if not draft["complete"]:
            raise RuntimeError("received treaty clauses are incomplete; read full terms before deciding")
        await self._controlled(actor)
        if accept:
            helper = self._ctx.support('diplomacy.treaty.proposal')
            try:
                state = await helper.prepare_response(self._ctx.native, self._ctx.process,
                    actor_full_country_id=actor, proposal_full_id=proposal_id, image_base=self._ctx.image_base,
                    data_adapter=self._adapter("data"), access_adapter=self._adapter("access"),
                    call_adapter=self._adapter("call"), script_adapter=self._adapter("script"),
                    commands_adapter=self._adapter("commands"))
            except BaseException as error:
                state = getattr(error, "query_state", None)
                if state is not None:
                    self._retain_state(state, "response", "response_unsettled")
                    if record is not None:
                        record["state"] = state
                else:
                    self._remember("response_unsettled", error)
                raise
            self._retain_state(state, "response", "accept")
            if record is not None:
                record["state"] = state
            valid = state["validation"]
            result = {"id": proposal_id, "decision": "accept", "accepted": None,
                "completed": False, "validation": valid, "native_result": None, "native_error": None, "native_submit_accepted": None,
                "status": "rejected", "reason": valid.reason}
            if not valid.validated:
                result["accepted"] = False
                self._response_primary(record, state, result)
            else:
                await self._controlled(actor)
                receipt = await helper.submit_response(state, actor_full_country_id=actor,
                    proposal_full_id=proposal_id, prepared_wire_sha256=state["prepared_wire_sha256"])
                outcome = getattr(getattr(state.get("unique_owner"), "attempt", None), "outcome", None)
                result.update(accepted=receipt.accepted, native_submit_accepted=receipt.accepted, native_result=receipt, native_error=receipt.error,
                    owner_outcome=outcome, status="native_error" if receipt.error else "effect_not_observed")
                self._response_primary(record, state, result)
                if outcome not in ("owned", "consumed") or self._response_unsettled(state):
                    result.update(status="delivery_uncertain", cleanup={"status": "retained_pending_or_unknown"})
                    return result
                if not receipt.error and outcome == "consumed" and receipt.accepted:
                    try:
                        observed = await helper.observe_response_state(state)
                    except asyncio.CancelledError as error:
                        result["after_error"] = error
                        self._response_error(error, state, result)
                        raise
                    except Exception as error:
                        result["after_error"] = error
                    else:
                        result["after"] = observed
                        result["completed"] = bool(observed["native_draft_to_inforce_transition"])
                        if result["completed"]:
                            result["status"] = "in_force"
            return await self._finish_response(state, result, helper.cleanup_completed)
        return await self._decline_native(draft, actor, record=record)

    async def accept(self, proposal_id):
        """Explicitly accept this treaty. No automatic proposal-selection policy."""
        proposal_id = self._proposal_id(proposal_id)
        return await self._run("accept", lambda: self._respond(proposal_id, True))

    async def decline(self, proposal_id):
        """Explicit original decline; may have native hostility consequences."""
        proposal_id = self._proposal_id(proposal_id)
        return await self._run("decline", lambda: self._respond(proposal_id, False))

    async def _decline_native(self, draft, actor, *, record=None):
        # Original Data producer owns its typed fields. Never patch an Accept
        # buffer or treat the native false-branch's easy Validate as permission.
        q, state = self._query_state(actor)
        if record is not None:
            record["state"] = state
        accept_helper = self._ctx.support('diplomacy.treaty.accept')
        state.update(unique_owner=None, unadopted_clone=None, handoff_uncertain=False, cleanup_uncertain=False)
        await q._types(state, 256)
        country = data.ObjectBinding.country(state["types"]["Country"], actor)
        source = await q._evaluate(state, "decline_current_drafts", "Country.GetDraftTreaties", country)
        count = await q.complete(state, "decline_current_count", lambda: data.list_count(self._ctx.native,
            source.value_handle, adapter=self._adapter("data"), process=self._ctx.process))
        matches = []
        for index in range(count.total):
            element = await q.complete(state, "decline_draft_" + str(index), lambda index=index: data.list_element(
                self._ctx.native, source.value_handle, index, adapter=self._adapter("data"), process=self._ctx.process))
            value = q._owned_object(state, element, "CDataTreaty", "Treaty")
            ref = await q._full_id(state, "decline_ref_" + str(index), value, element.value_handle)
            if ref == draft["id"]:
                matches.append(element)
        if len(matches) != 1:
            raise ValueError("proposal changed before decline; no index fallback")
        binding = data.ObjectBinding(state["types"]["Treaty"], matches[0].value_handle, retained=True)
        generated = await q._evaluate(state, "native_decline_producer", "Treaty.DeclineTreatyProposal", binding)
        carrier_result = await q.complete(state, "decline_original_command", lambda: data.command_value(self._ctx.native,
            generated.value_handle, output_bytes=32, adapter=self._adapter("data"), process=self._ctx.process))
        carrier = carrier_result.value
        if not isinstance(carrier, data.NativeDataCommand) or not carrier.primary_pointer or carrier.generation_world != self._ctx.world:
            raise RuntimeError("decline producer has no original current-world command owner")
        clone = await accept_helper._operation(state, "clone_original_decline", lambda: commands.request(self._ctx.native,
            commands.Action.CLONE, command=carrier.primary_pointer, world=carrier.generation_world, capacity=8,
            adapter=self._adapter("commands"), process=self._ctx.process))
        state["unadopted_clone"] = clone if clone.cloned_command else None
        if clone.error or clone.type_token != accept_helper.TOKEN or clone.world != self._ctx.world or not clone.cloned_command or clone.cloned_command == carrier.primary_pointer:
            raise RuntimeError("original decline unique ownership is not proved")
        owner = commands.NativeCommand.from_result(self._ctx.native, clone,
            adapter=self._adapter("commands"), process=self._ctx.process)
        state["unique_owner"] = owner
        state["unadopted_clone"] = None
        wire = await accept_helper._operation(state, "serialize_original_decline", owner.serialize, owner=owner)
        if wire.error or owner.attempt.outcome != "owned":
            raise RuntimeError("original decline serialization failed or ownership unknown")
        tree = commands.decode_command(wire.data)
        one = accept_helper._one
        domain = one(tree.value, 0x41)
        actual = one(tree.value, 0x2840)
        repeated, ref, decision = one(domain, 0x240), one(domain, 0x57D6), one(domain, 0x37C5)
        if (tree.key != commands.Token(accept_helper.TOKEN) or actual != commands.UInt32(actor)
                or repeated != actual or ref != commands.UInt32(draft["id"]) or decision is not False):
            raise RuntimeError("original decline actor, proposal or decision differs from explicit intent")
        valid = await accept_helper._operation(state, "validate_original_decline", owner.validate, owner=owner)
        if valid.error not in (0, 122) or owner.attempt.outcome != "owned":
            raise RuntimeError("original decline validation ownership is uncertain")
        result = {"id": draft["id"], "decision": "decline", "accepted": None, "completed": False,
            "status": "rejected", "reason": valid.reason, "validation": valid,
            "native_result": None, "native_error": None, "native_submit_accepted": None}
        if not valid.validated:
            result["accepted"] = False
            self._response_primary(record, state, result)
        else:
            await self._controlled(actor)
            receipt = await accept_helper._operation(state, "submit_original_decline_once", owner.submit, owner=owner)
            state["submission_receipt"] = receipt
            result.update(accepted=receipt.accepted, native_submit_accepted=receipt.accepted,
                native_result=receipt, native_error=receipt.error, owner_outcome=owner.attempt.outcome,
                status="native_error" if receipt.error else "effect_not_observed")
            self._response_primary(record, state, result)
            if owner.attempt.outcome not in ("owned", "consumed") or self._response_unsettled(state):
                state["handoff_uncertain"] = True
                result.update(status="delivery_uncertain", cleanup={"status": "retained_pending_or_unknown"})
                return result
            if not receipt.error and owner.attempt.outcome == "consumed" and receipt.accepted:
                try:
                    remaining = await self._drafts()
                except asyncio.CancelledError as error:
                    result["after_error"] = error
                    self._response_error(error, state, result)
                    raise
                except Exception as error:
                    result["after_error"] = error
                else:
                    result["after"] = remaining
                    result["completed"] = draft["id"] not in {row["id"] for row in remaining}
                    if result["completed"]:
                        result["status"] = "declined_removed_from_drafts"
        async def cleanup(current):
            if owner.command:
                destroyed = await accept_helper._operation(current, "destroy_known_decline", owner.destroy, owner=owner)
                if destroyed.error or owner.attempt.outcome != "consumed":
                    error = RuntimeError("decline cleanup is uncertain")
                    error.native_result = destroyed
                    raise error
            return await q.cleanup_completed(current)
        return await self._finish_response(state, result, cleanup)

    async def _country_functions(self):
        """Cache copied metadata only; never country values, wrappers or scores."""
        country_type = await self._ctx.type_id("Country")
        epoch = (self._ctx.world, self._ctx.image_base, country_type)
        if getattr(self, "_country_function_epoch", None) == epoch:
            return self._country_function_rows
        rows, start = [], 0
        while True:
            page = await self._ctx.query("diplomacy.country_functions", lambda start=start: data.enumerate_functions(
                self._ctx.native, country_type, start=start, capacity=256,
                adapter=self._adapter("data"), process=self._ctx.process))
            if page.error:
                return ()
            rows.extend(page.rows)
            if page.next >= page.total:
                break
            if page.next <= start:
                raise RuntimeError("native country-function metadata did not advance")
            start = page.next
        self._country_function_rows = tuple(rows)
        self._country_function_epoch = epoch
        return self._country_function_rows

    async def _relation_points(self, actor, target):
        """Use the exact original GUI expression and current int32 metadata.

        Country is the target; GetPlayer is the independently observed source.
        There are never two conflicting Country bindings or a guessed alias.
        """
        current = await self._ctx.query("diplomacy.relations_player", lambda: player.observe_player(
            self._ctx.native, adapter=self._adapter("console"), process=self._ctx.process))
        if current.error or current.controlled_country_error or current.world != self._ctx.world:
            return None, "The native current-player observation is unavailable for this relation score."
        if current.observer_enabled or current.controlled_country_id != actor:
            return None, "The native GetPlayer relation getter requires the source to be the current controlled country."
        functions = await self._country_functions()
        matches = [row for row in functions if row.name == "GetRelationsWith"]
        if len(matches) != 1:
            return None, "Country.GetRelationsWith is missing or ambiguous in current native metadata."
        function = matches[0]
        country_type = await self._ctx.type_id("Country")
        int_type = await self._ctx.type_id("int32")
        direct, scoped, action_direct, action_scoped = function.callbacks
        if (function.type_id != country_type or function.argument_count != 1
                or function.return_type_id != int_type or action_direct or action_scoped
                or bool(direct) == bool(scoped)):
            return None, "Country.GetRelationsWith is not the original one-country int32 read getter."
        # Original GUI: outliner_ongoing_types.gui uses IntToFloat on this getter;
        # outliner_pinnable_types.gui uses TransparentIfZero_int32. Do not parse a
        # tooltip/description, reinterpret CFixedPoint, or invent a second context.
        expression = "Country.GetRelationsWith(GetPlayer)"
        def copied(result):
            self._remember("relation_points_original", result)
            if result.error:
                return None, "Country.GetRelationsWith returned native error " + str(result.error) + "."
            if result.kind != data.Kind.I32 or result.type_id != int_type:
                return None, "The native relation score did not have the declared int32 type."
            return result.value, None
        return await self._copied_read("diplomacy.relation_points", lambda run: data.evaluate(
            run.native, expression, (data.ObjectBinding.country(country_type, target),),
            output_bytes=8, adapter=run.adapters["data"], process=run.process), copied)

    async def relations(self, target):
        """Read the genuine improve-relations pact and request state for a country.

        Relation points come from the original int32 getter, not a pact flag or
        formatted tooltip. Explicit non-player source contexts remain distinct.
        """
        async def run():
            await self._ctx.refresh()
            actor, other = await self._ctx.country_id(), await self._resolve(target)
            if actor == other:
                raise ValueError("relations require a different country")
            helper = self._ctx.support('diplomacy._country_commands')
            plan = await helper.prepare(self._ctx.native, self._ctx.process, image_base=self._ctx.image_base,
                expected_world=self._ctx.world, action_key="increase_relations", first=actor, second=other,
                intent="start_pact", commands_adapter=self._adapter("commands"),
                access_adapter=self._adapter("access"), call_adapter=self._adapter("call"))
            self._remember("relations", plan)
            seen = plan["before"]
            # Release the private read/validation command before unrelated name
            # and numeric queries. The current pact snapshot is already copied.
            await helper.destroy_unsubmitted(plan, keep_status=True)
            points, points_reason = await self._relation_points(actor, other)
            result = {"country": await self._country_label(other), "relation_points": points,
                "relation_points_unit": "relation points", "relation_points_unavailable_reason": points_reason,
                "improving_relations": seen["pact_valid"],
                "improve_relations_pact_id": seen["pact_id"] if seen["pact_valid"] else None,
                "pending_improve_relations_request_id": seen["action_id"] if seen["pending_valid"] else None}
            return result
        return await self._run("relations", run)

    @staticmethod
    def _action_reason(plan):
        reason = getattr(plan.get("validation"), "reason", None)
        if reason:
            return reason
        status, before = plan.get("status"), plan.get("before", {})
        if status == "intent_precondition_not_met":
            if before.get("pending_valid"):
                return "A diplomatic request for this action is already pending."
            if plan.get("intent") == "break_pact":
                return "There is no active improve-relations pact to stop."
            if before.get("pact_valid"):
                return "Relations are already being improved with this country."
            return "The current native diplomatic state does not allow this action."
        if status == "native_type_does_not_match_intent":
            return "The current native action does not support this kind of diplomatic request."
        if status == "intent_precondition_changed_known_owner_held":
            return "The diplomatic state changed before this action was submitted."
        if status == "native_rejected_no_submission":
            return "The game rejected this diplomatic action without a detailed reason."
        return None

    async def actions(self, target=None):
        """Discover the supported gameplay actions, with live constraints if targeted.

        This is the current convenience-action subset, not a fabricated catalog
        of every native diplomatic definition. Call either named method directly;
        no native key, schema, preparation or submission steps are needed.
        """
        if target is not None:
            return (await self.options(target))["actions"]
        return [{"action": action, "name": name, "target_required": True,
            "available": None, "reason": "Select a country to check current game constraints.",
            "source": "Original native improve-relations pact, current Definition flags and command validation",
            "cost": None, "cost_unavailable_reason": "No native cost value is exposed by the current action helper"}
            for action, name in (("improve_relations", "Improve relations"),
                ("stop_improving_relations", "Stop improving relations"))] + [
            {"action": "propose_treaty", "name": "Propose a formal treaty", "target_required": True,
             "terms_required": True, "available": None,
             "reason": "Choose countries and article terms; treaty_options reads the current native article registry and conditions.",
             "source": "Original native treaty article definitions and proposal validation", "cost": None}]

    async def options(self, target):
        """Copy the original country action catalogs without candidate commands.

        Each row includes its real native key, parties, costs and source category.
        Default eligibility is not guessed from a separate command preview.
        """
        return await self.diplomatic_options(target)

    async def diplomatic_action(self, action, target, *, kind="proposal"):
        """Advanced native-key escape hatch; use actions/options and named methods normally.

        kind is proposal, start_pact or stop_pact. The original definition flags
        and validator reject wrong kinds/targets. No selectable bypass is used.
        This is not a claim that every action or its final effect was tested.
        """
        if not isinstance(action, str) or not action or "\0" in action:
            raise ValueError("action must be a native diplomatic action key")
        intents = {"proposal": "propose", "start_pact": "start_pact", "stop_pact": "break_pact"}
        if kind not in intents:
            raise ValueError("kind must be proposal, start_pact or stop_pact")
        async def run():
            await self._ctx.refresh()
            actor, other = await self._ctx.country_id(), await self._resolve(target)
            await self._controlled(actor)
            helper = self._ctx.support('diplomacy._country_commands')
            plan = await helper.prepare(self._ctx.native, self._ctx.process,
                image_base=self._ctx.image_base, expected_world=self._ctx.world, action_key=action,
                first=actor, second=other, intent=intents[kind], commands_adapter=self._adapter("commands"),
                access_adapter=self._adapter("access"), call_adapter=self._adapter("call"))
            self._remember("action", plan)
            result = {"action": action, "target_id": other, "accepted": False, "completed": False,
                "status": "unavailable", "reason": self._action_reason(plan)}
            if plan["status"] == "prepared_not_submitted":
                await self._controlled(actor)
                await helper.submit(plan, intent=intents[kind])
                accepted = bool(plan.get("submission") and plan["submission"].accepted)
                completed = plan["status"] == "observed_expected_effect"
                result.update(status="completed" if completed else "effect_pending" if accepted else "unavailable",
                    accepted=accepted, completed=completed, reason=self._action_reason(plan))
                after = plan.get("after")
                result["diplomatic_request_id"] = after["action_id"] if after and after["pending_valid"] else None
                result["pact_id"] = after["pact_id"] if after and after["pact_valid"] else None
            await helper.destroy_unsubmitted(plan, keep_status=True)
            return result
        return await self._run("diplomatic_action", run)

    async def improve_relations(self, target):
        """Start the native continuous improve-relations pact, not instant points."""
        return await self.diplomatic_action("increase_relations", target, kind="start_pact")

    async def stop_improving_relations(self, target):
        """Separate explicit stop intent; never implicit cleanup or rollback."""
        return await self.diplomatic_action("increase_relations", target, kind="stop_pact")
