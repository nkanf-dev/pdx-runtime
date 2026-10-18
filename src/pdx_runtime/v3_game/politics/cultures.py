"""Country population-culture politics through original native Data providers.

The pop-browser GUI binds Culture directly, not a guessed PopCulture wrapper.
Homeland rows bind State. All results are copied native primitives/text. Source
occurrences are never promoted to game IDs or joined across separate snapshots.
"""
from __future__ import annotations

from ... import v3_data as data
from .._political_data import Field, country_source


_CULTURE_SOURCE = "Country.AccessPopCulturesSortedByPopulation"


def _text(name, expression):
    return Field(name, expression, data.Kind.STRING, text=True)


_AWAKENING_FIELDS = (
    Field("id", "Culture.GetID", data.Kind.U32, full_id=True),
    _text("name", "Culture.GetNameNoFormatting"),
    Field("is_valid", "Culture.IsValid", data.Kind.BOOL),
    Field("has_national_awakening", "Culture.HasNationalAwakening", data.Kind.BOOL),
    Field("current_fervor", "Culture.GetCurrentFervor", data.Kind.FIXED),
    Field("target_fervor", "Culture.GetTargetFervor", data.Kind.FIXED),
    Field("monthly_fervor_change", "Culture.GetMonthlyChangeFervor", data.Kind.FIXED),
    _text("target_fervor_breakdown", "Culture.GetTargetFervorBreakdown"),
    Field("target_fervor_country_modifiers", "Culture.GetTargetFervorFromCountryModifiers", data.Kind.FIXED),
    _text("target_fervor_country_modifiers_breakdown", "Culture.GetTargetFervorFromCountryModifiersBreakdown"),
    Field("is_primary_culture", "Culture.IsPrimaryCultureIn(Country.Self)", data.Kind.BOOL),
    Field("has_secession_movement", "Culture.HasSecessionMovement(Country.Self)", data.Kind.BOOL),
    _text("cultural_traits", "Culture.GetCulturalTraitsDesc"),
    Field("homeland_count", "GetDataModelSize(Culture.AccessHomelands)", data.Kind.I32),
)
_HOMELAND_FIELDS = (
    Field("culture_id", "Culture.GetID", data.Kind.U32, full_id=True),
    _text("culture_name", "Culture.GetNameNoFormatting"),
    Field("state_id", "State.GetID", data.Kind.I32, full_id=True),
    _text("state_name", "State.GetName"),
    Field("owner_id", "State.GetOwner.GetID", data.Kind.I32, full_id=True),
    _text("owner_name", "State.GetOwner.GetName"),
    Field("region_id", "State.GetStateRegion.GetID", data.Kind.I32, full_id=True),
    _text("region_name", "State.GetStateRegion.GetName"),
)


class CulturePoliticsMixin:
    """Observe awakening/fervor for cultures in one country's population."""

    async def national_awakenings(self, country_id=None):
        """Read country-population cultures and their original homeland states.

        All native culture rows remain visible, including awakening=False and
        zero fervor. No global culture or world-pop scan is made. Fervor values
        are the native Culture getters, not a percentage conversion or a new
        country-only calculation. Primary/secession predicates use the selected
        Country. Homelands can lie outside that country.

        ``complete`` covers requested copied reads, not all-world coverage or an
        atomic snapshot. Nested homeland rows repeat their actual Culture ID and
        name in the same callback. They are not joined to earlier rows by index.
        """
        async def read():
            cultures = await country_source(self, "politics.national_awakenings.cultures",
                _CULTURE_SOURCE, _AWAKENING_FIELDS, country_id=country_id, perform=False)
            selected = cultures["country_id"]
            homelands = []
            for row in cultures["rows"]:
                occurrence = row["source_index"]
                step = data.CreationStep(_CULTURE_SOURCE, occurrence, cultures["source_count"])
                observed = await country_source(self, "politics.national_awakenings.homelands",
                    "Culture.AccessHomelands", _HOMELAND_FIELDS, country_id=selected,
                    steps=(step,), perform=False)
                observed["requested_culture_occurrence"] = occurrence
                observed["parent_identity_semantics"] = (
                    "culture_id/name are copied inside each original State provider callback; "
                    "the requested ordinal is not a culture identity or a cross-snapshot join")
                if not observed["rows"]:
                    observed["parent_identity_copied"] = False
                    observed["parent_identity_reason"] = "No homeland row copied a current Culture ID/name"
                homelands.append(observed)
            observations = (cultures, *homelands)
            complete = all(value["complete"] for value in observations)
            reasons = []
            for number, observed in enumerate(observations):
                if not observed["complete"]:
                    reasons.append({"scope": "cultures" if number == 0 else "homelands",
                        "requested_culture_occurrence": observed.get("requested_culture_occurrence"),
                        "pages": observed["reasons"],
                        "rows": [{"source_index": row["source_index"], "fields": row["reasons"]}
                                 for row in observed["rows"] if row["reasons"]]})
            result = dict(cultures)
            result.update(homelands=homelands, complete=complete,
                status=("observed" if complete else "partial" if cultures["rows"] else "unavailable"),
                native_pages=[page for value in observations for page in value["native_pages"]],
                reasons=reasons,
                atomic_snapshot=False,
                collection_scope={"kind": "country_population_cultures", "country_id": selected,
                                  "source_expression": _CULTURE_SOURCE, "all_world_cultures": False,
                                  "only_awakened_filter_applied": False},
                fervor_scope="original Culture getters; not recomputed as selected-country-only values",
                homeland_scope="original Culture.AccessHomelands State rows, not filtered by owner country",
                identity={"id_source": "Culture.GetID", "definition_key_available": False,
                          "reason": "No registered Culture.GetKey getter in the checked reference; names and ordinals are not keys"},
                limitations=["Culture.GetKey is not invented from a name, tooltip tag or source occurrence",
                             "Secession is the native country-specific predicate, not invented movement IDs",
                             "Separate source and homeland pages are not one atomic world snapshot"])
            return result
        return await self._ctx.perform("politics.national_awakenings", read)
