"""Military task roots and source-scoped selectors on the existing V3Game context.

Directories follow native Country sources. Select an object, then expand only the
related view needed for the task. No selector owns a native pointer or proves a
relationship merely because an ID was supplied by the caller.
"""
from __future__ import annotations

from ... import v3_objects as objects


class MilitaryAPI:
    def __init__(self, ctx):
        self._ctx = ctx

    @staticmethod
    def _id(name, value):
        if type(value) is not int or not 0 <= value < objects.UINT32_MAX:
            raise ValueError(f"{name} must be a complete game ID")
        return value

    @staticmethod
    def _native_full_id(value):
        if isinstance(value, str):
            value = value.strip()
            if not value or not value.isascii() or not value.isdigit():
                return None
            value = int(value, 10)
        if type(value) is not int or not -(1 << 31) <= value < objects.UINT32_MAX:
            return None
        full_id = value & objects.UINT32_MAX
        return None if full_id == objects.UINT32_MAX else full_id

    @staticmethod
    def _pure_read_function(function, argument_count=0):
        if function is None or function.argument_count != argument_count:
            return False
        callbacks = function.callbacks
        return len(callbacks) == 4 and not any(callbacks[2:]) and bool(callbacks[0]) != bool(callbacks[1])

    @property
    def _warfare_runs(self):
        from .._run import retained_runs
        return retained_runs(self._ctx)

    async def _pause_for_action(self):
        observed = await self._ctx.refresh()
        if not observed.paused:
            await self._ctx.domain("time")._pause_native()

    async def _call(self, module, method, *args, action=False, source=None, **kwargs):
        if "validate_only" in kwargs and type(kwargs["validate_only"]) is not bool:
            raise ValueError("validate_only must be a boolean")
        async def operation():
            if action:
                await self._pause_for_action()
            else:
                await self._ctx.refresh()
            result = await getattr(module, method)(source if source is not None else self, *args, **kwargs)
            if action and isinstance(result, dict):
                result["paused"] = True
            return result
        return await self._ctx.perform("military." + method, operation)

    async def wars(self, *, start=0, limit=64):
        """The controlled country's native War directory, not world leader snapshots."""
        from . import _observations
        return await self._call(_observations, "war_directory", start=start, limit=limit)

    def war(self, war_id):
        from ._selectors import War
        return War(self, self._id("war_id", war_id))

    async def formations(self, *, kind=None, country_id=None, start=0, limit=64):
        """Read readiness columns from one actual country's army/fleet sources."""
        from . import _observations
        from ._selectors import CountrySource
        if kind not in (None, "army", "fleet"):
            raise ValueError("kind must be army, fleet or None")
        return await self._call(_observations, "formation_directory", kind=kind, start=start, limit=limit,
                                source=CountrySource(self, country_id))

    def formation(self, formation_id, *, country_id=None):
        from ._selectors import Formation
        return Formation(self, self._id("formation_id", formation_id), country_id)

    async def commanders(self, *, country_id=None, role=None, start=0, limit=64):
        from . import _observations
        from ._selectors import CountrySource
        if role not in (None, "general", "admiral"):
            raise ValueError("role must be general, admiral or None")
        return await self._call(_observations, "commander_directory", role=role, start=start, limit=limit,
                                source=CountrySource(self, country_id))

    def commander(self, character_id, *, country_id=None):
        from ._selectors import Commander
        return Commander(self, self._id("character_id", character_id), country_id)

    async def ships(self, *, fleet_id=None, country_id=None, start=0, limit=64):
        from . import _observations
        from ._selectors import CountrySource
        if fleet_id is not None:
            self._id("fleet_id", fleet_id)
        return await self._call(_observations, "ship_directory", fleet_id=fleet_id, start=start, limit=limit,
                                source=CountrySource(self, country_id))

    def ship(self, ship_id, *, country_id=None):
        from ._selectors import Ship
        return Ship(self, self._id("ship_id", ship_id), country_id)

    async def templates(self):
        from . import ships
        return await self._call(ships, "ship_templates")

    def template(self, template_id):
        from ._selectors import Template
        return Template(self, self._id("template_id", template_id))

    @property
    def construction(self):
        from ._selectors import ShipQueue
        return ShipQueue(self)

    @property
    def logistics(self):
        from ._selectors import NationalLogistics
        return NationalLogistics(self)

    @property
    def recruitment(self):
        from ._selectors import CommanderRecruitment
        return CommanderRecruitment(self)

    async def occupation(self):
        from . import battles
        return await self._call(battles, "state_occupation")

    async def flagship(self):
        from . import ships
        return await self._call(ships, "flagship_state")
