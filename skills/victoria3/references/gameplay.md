# Victoria 3: game-level methods

Use methods on `game`, a `V3Game` connection. Native discovery, automatic paging,
real key association, command ownership, validation/submission and known cleanup
are internal. These methods expose game facts and choices, not a national
strategy. Unavailable fields retain reasons; a game rejection is not a reason
to abandon unrelated work.

For a complete install/connect/observe/choose/action/close program, start with
[the ordinary Python entry](connection-control.md#run-as-an-ordinary-python-program).
All examples below use that same installed `game`; no repository experiment or
native ownership setup is needed. Read candidates, pass the chosen real key/ID,
and inspect the returned facts and native reason.

## Country and lightweight overview

```python
overview = await game.country.summary()
states = await game.country.states()
buildings = await game.country.buildings(state=state)
```

- `country.summary()` returns `id`, plain `name`, population in people, GDP,
  `units` and field-specific `reasons`. It does not perform an industry IO scan.
- `states(*, details=False)` returns actual owned State IDs/names by default.
  `details=True` adds each row's State-scoped `taxation`: native `capacity`,
  `usage`, `balance`, units/reasons and `insufficient` (observed usage > capacity).
  These are local taxation-capacity points, not national bureaucracy; one does
  not stand in for the other. This detail option does not scan State populations.
- `capacities(*, details=False)` returns `bureaucracy`, `authority`, `influence`
  and national `institutions` observations with units/reasons. Each resource
  exposes signed `balance`, `produced`, `used`, `deficit_ratio`, `excess_ratio`
  and `used_observation`. Resource points are not money; ratios are fractions.
  `used` reverses the proven native balance formula as produced minus balance
  from separate native getters, not an atomic resource snapshot.
  `details=True` additionally reads original expense text/raw text. Institution
  bureaucracy cost is the national total, not a police-only cost or tax-loss
  diagnosis.
- `buildings(*, state=None, details=False)` returns domestic Building IDs,
  genuine names/keys, State names/ownership and levels. The optional State can
  be an observed ID, name, or `{"id": ...}` row. `None` covers all owned states.
  A missing key is `None` with a reason, not an invented definition join.
  The default list keeps its lightweight shape and does not format tooltips.
  Reads are separate native observations, not a cross-world transaction. Copied
  values remain available if an observed world change adds a diagnostic; the
  reader does not refresh the whole world before and after each field.

```python
detailed_buildings = await game.country.buildings(state=state, details=True)
```

`details=True` adds `tooltip` (readable plain text), `tooltip_raw` (original rich
text), and `tooltip_complete` to each row. The flag covers the original native
formatting context, complete UTF-8 copy and completed temporary cleanup; an
incomplete description retains `reasons["tooltip"]`. Buffer sizing and original
pending work stay internal, with no caller-managed 4 KiB text limit.
The shared formatter has native evidence for two descriptions of 1567 and 769
UTF-8 bytes. The larger-text fallback is CPU-tested; those short native cases
do not establish its native coverage.

```python
capacities = await game.country.capacities(details=True)
detailed_states = await game.country.states(details=True)
```

Here and below `state`, `building_id`, `army_id`, `target`, `event_id` and chosen
candidate rows come from the corresponding game-level list or caller intent.
They are not native pointers, DataFactory types or owner ledgers.

## One State's population

`country.population(*, state, details=False)` requires one owned State full ID
or an observed `{"id": ...}` row; it is not a country-wide or name-based scan.
The default result has State identity/name, `population`, `radicals`, `loyalists`
and `strata.lower/middle/upper.population`, all in people, with reasons for
unavailable values. State-wide radical/loyalist totals do not identify the
political counts in each stratum: those fields remain `None` with reasons in
the default summary, not proportional allocations.

`details=True` adds a targeted scan of the original `State.GetPops` collection.
Real retained Pop receivers and native SocialClass/SocialHierarchy opaque
membership identify lower/middle/upper strata; profession, numeric ordinal and
localized labels are not substituted for that membership. Sums use the original
Pop `GetTotalSize`, `GetNumRadicals` and `GetNumLoyalists` getters.

`population_scan` reports initial/observed/unique Pop-record counts, `complete`
and `reasons`. Where available, each stratum's `observed_population`,
`observed_radicals` and `observed_loyalists` are sums from those observations,
independent of the direct State totals and direct stratum population getters.
Partial observations are not final zeros or allocated shares of State totals.
Verified political fields can be published independently; unavailable/incomplete
fields retain `None` and reasons. Scan completeness is not a single-tick atomic
snapshot, and the API does not overwrite the State totals to make sums agree.

For `state_row`, choose one row returned by `game.country.states()`:

```python
population = await game.country.population(state=state_row)
detailed_population = await game.country.population(state=state_row, details=True)
```

The detailed aggregation now has native read coverage as well as CPU coverage;
a verified zero remains zero. Separate sampling and partial-result semantics
remain unchanged.
Neither `states()` nor `states(details=True)` adds this population operation.
Population counts alone do not establish how an event's scripted political
change applies to the State or its strata.

## Budget, market and economic controls

```python
budget = await game.economy.budget()
print(budget["balance"]["predicted_weekly"])
print(budget["credit"]["principal"], budget["credit"]["remaining"])
print(budget["credit"]["interest"]["weekly_payment"])
market = await game.economy.market()
iron = await game.economy.goods("iron")
```

The budget follows the game's budget and credit bindings. Its sections are
`balance`, `income`, `expenses`, `assets` and `credit`.

- The UI balance uses `Country.PredictWeeklyBalance`. The separate
  `Country.GetWeeklyBalance` observation is not substituted for that prediction.
- Debt principal uses `Country.GetPrincipal`. `GetGovernmentSelfDebt` is only
  the self-held component, not total debt.
- Credit includes the limit, remaining capacity, utilization, interest payment
  and weekly/yearly interest rates. Monetary stocks are pounds; flows are
  pounds/week. Rates keep their declared time basis.
- `details=True` opens income and expense categories, including investment-pool
  transfers and government/private construction spending.
- `explanations=True` reads the original budget, credit and interest tooltips.
  These text reads are not part of the default scalar summary.

Original sources, units, observations and partial errors remain available.
Values from separate reads are not forced to reconcile. Fixed-only balance
excludes temporary income and expenses; a constant-deficit runway calculation
is a scenario, not a forecast of the bankruptcy date.

`market()` resolves the selected country's original Market relation and returns
`id`, `country_id`, `name` and `goods`. `goods(key=None)` returns that Goods list,
or a genuine key/returned numeric ID. Rows expose key/plain name, price,
demand/supply, imports/exports, units and unavailable-field reasons. `shortage`
is the observed buy-minus-sell order gap, not a native shortage penalty or ratio.
The actual provider determines row count; an earlier 53-row world is not a cap.

| Read state / constraints | Action | Parameters and observed effect |
| --- | --- | --- |
| `tax_level()`, `tax_options()` | `set_tax_level(level)` | Bands are `very_low`, `low`, `medium`, `high`, `very_high`; options return description, `allowed`, reason. A band is not a fabricated effective percentage. |
| `wages()` | `set_wages(sector, level)` | `government`/`military` and the same bands; read current bands separately. |
| `tariffs(good)` | `set_tariff(good, direction, policy)` | Real Goods key; `import`/`export`. Read effective policy, including native market-owner delegation. |
| `consumption_tax(good)` | `set_consumption_tax(good, enabled=True)` | Read actual `taxed` membership; an absolute intent does not blindly repeat a toggle. |

`tax_level()` returns `country_id`, the current native `level` and an unavailable
`reason`, without producing a command. `tax_options()` also marks each band's
`current` flag from that read; unknown current state stays `None` with
`current_reason`. Eligibility and current selection are separate facts.

All methods are under `game.economy`. Setter results separate acceptance from
independent observations. Tariff setter policy values are `subventions_max`,
`subventions_high`, `subventions_low`, `tariffs_none`, `tariffs_low`,
`tariffs_high`, `tariffs_max`; the observation currently uses short display
bands such as `high` or `subsidy_high`.

```python
current_tax = await game.economy.tax_level()
tax_choices = await game.economy.tax_options()
wage_bands = await game.economy.wages()
iron_tariffs = await game.economy.tariffs("iron")
iron_tax = await game.economy.consumption_tax("iron")
```

## Building status, IO and explicit industry panorama

`economy.building_status(building_id, *, details=True)` reads a real Building's
`employment`, `hiring`, `wages`, `cash`, `subsidies`, `input_supply`, State-wide
context and available `production_methods` groups/current/options. Values keep
units and per-section reasons. `details=True` adds original hiring explanation
text/raw text/completeness; `False` omits that formatting work. Native hiring
indicators and descriptions are observations, not a labour/material/wage cause
inferred by the API. A State-wide qualifications warning does not diagnose one
Building's understaffing. PM fields in this status read do not change a method;
use the dedicated production-method calls below for an explicit selection.

```python
status = await game.economy.building_status(building_id, details=True)
```

### Production-method choices and selection

`economy.production_methods(building_id, *, details=True)` returns original
`groups`, each group's `current` method and `candidates` with genuine keys,
names and independent `selected`/`allowed` observations. `allowed` is the
original native change-command validation, not candidate membership or mere
mode validity; `selected` reports the active method separately. Unknown group
keys, IDs or effects keep their reasons. With details, Goods/jobs effects come
from the original game context, not a fabricated output forecast.
Each candidate's `full_effects` supplies `text`, `raw_text`, `complete`, `reason`
and source scope for `ProductionMethod.GetTooltip(GetPlayer.Self)` when
available. Its scope is `production_method_for_player_country` and
`building_specific=False`: it is a player-country PM tooltip, not a prediction
of this particular Building's resulting production or employment.

```python
methods = await game.economy.production_methods(building_id, details=True)
change = await game.economy.set_production_method(building_id, method)
```

`set_production_method(building_id, method)` accepts a genuine method key or a
returned candidate row. If already selected, it returns `submitted=False`,
`accepted=None`, `effect_observed=True`, `status="already_selected"`: no native
change was dispatched and no acceptance is fabricated. Otherwise `accepted`
and `effect_observed` remain separate; the latter checks the fresh active PM,
not later production or employment. Read `building_status()` / `building_io()`
after normal time for those actual outcomes. Original uncertain work stays
internal and is not resubmitted just to obtain another receipt.

### Subsidy state and explicit on/off intent

`economy.subsidy(building_id)` returns the current effective `current` flag,
`can_enable` / `can_disable`, their native reasons, `current_cost` in
`pounds/week`, and sampling scope. `estimated_cost` remains `None` with
`estimate_reason`: current spending is not a forecast for enabling support.
The effective native policy includes Country/building-type rules; Off can also
clear the Country's per-type override, not only this one Building's flag.

```python
support = await game.economy.subsidy(building_id)
change = await game.economy.set_subsidy(building_id, enabled=True)
```

`set_subsidy(building_id, enabled=True)` expresses an absolute native on/off
intent and reads the effective state back. A no-op has `submitted=False`,
`accepted=None`, `changed=False`, `effect_observed=True`; it does not fabricate
an accepted native command. Acceptance and fresh observed state remain separate.
`status="original_handoff_retained"` means the original query/receipt remains
internal; that result dispatches no second or reversal action.
Native operating use has covered `subsidy()` and one caller-chosen On request:
`set_subsidy(..., enabled=True)` returned `submitted=True`, `accepted=True`,
`effect_observed=True` and `changed=True`. An independent read then observed
143.43 pounds/week current expense; `estimated_cost` remained `None`, not a
forecast. A wage-subsidy observation of 1.86472 pounds/year/person and initial
3999/5000 staffing did not establish additional hiring. This verifies read and
On, not an executed Off request or later employment/output gains. PM read-only
native coverage remains separate.

### Goods IO and panorama

`economy.building_io(building_id)` reads both original consumed and produced
Goods sources. Its result has `inputs`, `outputs`, `building_id`, `units`,
`reasons` and sampling scope. Each Goods row preserves true key/plain name,
`consumption`, `production`, `consumption_value`, `production_value` and reasons.
Quantities are Goods orders; values are pounds. Missing sources/fields stay
unknown, not empty/zero substitutes. Collections span separate observations.

`industries(state=None, *, include_io=True, on_row=None)` explicitly scans all
selected domestic Buildings and optionally adds `io` to every row. This is the
panorama operation; `country.buildings()` remains lightweight. `on_row(row)`
can be a regular or async callback. `industry_progress()` is a synchronous copy
of currently produced `rows`, `complete` and available country/reason fields.
It can be inspected while the original panorama operation remains in progress.

```python
io = await game.economy.building_io(building_id)

def show_industry(row):
    print(row["state_name"], row["name"], row["levels"])

industries = await game.economy.industries(
    state=state, include_io=True, on_row=show_industry
)
progress = game.economy.industry_progress()
```

Automatic enumeration and native cleanup remain inside these methods. A full
multi-Building scan is not one atomic snapshot of the national economy.

## Research

```python
research = await game.research.current()
production = await game.research.options(category="production")
planned_research = await game.research.queue()
technology = await game.research.details("atmospheric_engine")
```

`current()` exposes actual identity/key/name and available progress/cost in
`research_points`. `options(category=None)` covers production, military, society
and other, with cost, progress and researched/current state. `researchable`
preserves the native pure flag; it is not automatically complete eligibility
for another country. `can_start` checks selection for the actual target Country
using the original game's selection rule, including prerequisites/current state.
An unknown check remains `None` with `eligibility_reason`, not a guessed bool.
`queue()` preserves original planned order and excludes current research.
Unknown progress/eligibility remains unknown with its source reason.

`details(key)` reads the complete original technology definition key, including
researched technologies or ones absent from the current candidate lists. It
returns actual `researched`, `is_current`, progress/cost in `research_points`,
key and available name; completion is the original researched predicate, not
candidate absence or a progress ratio. It does not evaluate selection eligibility,
select research or change the queue. `category`, `researchable` and `can_start`
stay `None` with `category_reason` / `eligibility_reason` explaining that they
were not queried. Each native factory/getter pair consumes its temporary
receiver within one original data expression; no player-held receiver is needed.

`start(key)` selects through native rules when research is empty; the same
already-active key returns `already_current` without another command. It does
not preempt a different current technology. `enqueue(key)` plans later work;
`dequeue(key)` uses native removal, which can prune dependents. Read `current()`
after normal time to observe actual progress; queue admission is not completion.

## Construction

Select a State to open its actual construction menus. Queue reads and queue
cancellation use the government's original queue source, not a world Building
scan or a reverse lookup from localized names.

```python
candidates = await game.construction.options(state=state_id)
estimate = await game.construction.estimate(
    state=state_id, building=building_key, levels=1
)
order = await game.construction.build(
    state=state_id, building=building_key, levels=1
)
queue = await game.construction.queue(start=0, limit=20)
```

- `options(*, state, details=False, validate=False)` reads only the selected
  State's native building menus. Validation is optional; `estimate()` validates
  one selected intent without submitting it.
- `build(state, building, levels=1, *, details=False)` constructs and validates
  one native command, then submits that same command. Acceptance is separate
  from construction completion.
- `queue(*, start=0, limit=None, details=False)` returns an envelope with `items`,
  source counts, the requested range and partial results. Rows contain original
  names, progress, speed, validity, `source_position` and the separate native
  `index`. No typed State/building identity is invented from display names.
- `details=True` adds native queue tooltip, queue number, base speed, current
  construction state and estimated weeks left when returned by that element.
- `cancel(*, position, details=False)` selects the position in the **current
  unfiltered government source** and invokes that native element's cancel
  command. It does not accept a stored observation as a stable witness.

To cancel the first position in the current source:

```python
cancelled = await game.construction.cancel(position=0)
```

`source_position` is zero-based source selection, not a persistent order ID and
not the native command's queue ordinal. If the queue changes, the same position
can select another order. The native element supplies its own command arguments;
callers do not reconstruct State/building inputs from translated names.

`capacity()` reads construction use and queue counts. `costs()` reads national
government/private/total construction Goods expenses in pounds/week, not a
project's total bill. `inputs(*, market=True)` reads construction-sector inputs
and joins Market goods by genuine key. Sector consumption is not a fixed
per-level recipe, and an order gap is not a native shortage penalty.

Read the selected Building after normal time to observe completed levels.
A cancelled or absent queue entry alone does not establish completion.

## Politics

| Read state / candidates | Action | Effect query |
| --- | --- | --- |
| `laws()`, `law_options()`, `enactment()` | `enact(law_key)`, `stop_enactment()` | `enactment()` during legislation; `laws()` for passage |
| `institutions()` | `set_institution(key, level)` | `institutions()` exposes current and target levels |
| `interest_groups()`, `political_movements()` | `bolster(movement, enabled=True)`, `suppress(movement, enabled=True)` | `political_movements()` |
| `decree_options(state)`, `decrees(state)` | `set_decree(state, decree)`, `remove_decree(state, decree)` | `decrees(state)` |

`law_options(*, details=False)` keeps the existing candidate shape and native
query cost by default. `details` must be a bool; other values raise `ValueError`
before any query. Both forms retain the original per-unique-Law internal
`Law.StartEnacting` producer → Validate → Destroy path for `enactable` and its
reason; neither submits/executes legislation nor invokes `Law.Select`.
`details=True` adds getter reads, not another producer round.

```python
law_choices = await game.politics.law_options(details=True)
```

- `national_clout`, scope `national_interest_group_clout`, supplies native
  `approve_fraction`, `disapprove_fraction`, `undecided_fraction` as `Decimal`
  fractions, plus original plain `approve_igs_description`,
  `disapprove_igs_description`, `undecided_igs_description` and field `reasons`.
  These are national IG clout shares, not population support or law
  success probabilities. Values are not normalized or filled by subtraction;
  description strings are not fabricated IG-ID sets.
- `checkpoint`, scope `first_checkpoint_candidate`, supplies `success_fraction`,
  `advance_fraction`, `debate_fraction`, `stall_fraction` and their corresponding
  `*_breakdown` text with field reasons. These are the original first-checkpoint
  candidate outcomes/factors, not final passage probability or clout shares.
  Only a law observed `active=False` and `enacting=False` has that scope;
  active/enacting/unknown states keep those fields `None` with reasons.
  A final native state recheck clears candidate values if the state changed or
  became unreadable. Independent native field errors remain visible.

Missing/wrong-kind/truncated detail values stay `None` with independent reasons;
real zero/False/empty descriptions stay real values. `enactable=True` establishes
start-command eligibility, not political support or a checkpoint probability.
`interest_groups(*, details=False)` keeps the lightweight ID/name rows by
default. `details=True` adds `clout` as a fraction (1 means 100%), `approval` in
points, and the original `in_government` boolean. Unknown fields stay `None`
with `reasons`; separate native callbacks are not an atomic political snapshot.

```python
groups = await game.politics.interest_groups(details=True)
```

`political_movements(*, details=False)` retains lightweight IDs/names and
bolstered/suppressed flags by default. `details=True` adds `support_fraction`,
`radicalism_fraction` (the UI's activism value), `supporting_individuals`,
insurrection/obstinance flags, core ideology text/law stances, and the original
`affected_laws_success` / `affected_laws_stall` lists. Fractions are not displayed
percentages; supporting individuals are a count. Affected-law membership is a
native movement effect list, not an enactment command or proof a law passed.

`supporting_characters` carries independently checked native identity and actual
support: popularity in points, original role text/flags, ideology/IG text and
field reasons. A name is not an identity join, and support does not imply the
person is the movement leader or holds an inferred office. Unknown values stay
`None` with reasons; core ideology is a political position, not a predicted
destination for a numeric metric.

```python
movements = await game.politics.political_movements(details=True)
```

All methods are under `game.politics`. Use the returned keys/names/IDs as their
respective inputs. Law admission starts legislation; it does not pass a law.
`laws()` follows the native panel, including current laws and enacting
replacements; `active` and `enacting` are the original BOOL observations,
`status` derives from those flags alone, and unavailable flags stay `None` with
`state_reasons`, not a passage judgment from progress or source names.
Institution changes set an investment target, which can settle later. This
adapter's bolster/suppress commands target political movements, not interest
groups. The API returns native eligibility/reasons without command-object work.

## Diplomacy

Methods are under `game.diplomacy`:

- `countries()` returns current country IDs, tags and genuine plain names.
- `plays(*, all_countries=False)` returns native plays/leaders, not a guessed
  full participant list.
- `relations(target)` accepts an observed country ID/tag. It reads original
  int32 `relation_points` in relation points, plus improve-relations pact and
  pending-request state. An unavailable numeric getter retains its reason;
  the number is not inferred from a pact flag or formatted tooltip.
- `actions(target=None)` lists supported `improve_relations` and
  `stop_improving_relations`. `options(target)` returns real availability,
  native rejection reasons, active/pending state and unknown-cost reasons.
- `improve_relations(target)` / `stop_improving_relations(target)` start/stop
  the real continuous pact, not an instant point effect.
- `proposals()` lists the country's actual treaty drafts. `terms(proposal_id)`
  reads received original clause views/full free text and completeness.
  `accept(proposal_id)` / `decline(proposal_id)` return native acceptance and
  independently observed state.
- `diplomatic_action(action, target, *, kind="proposal")` remains the advanced
  native-key fallback. Kind is `proposal`, `start_pact` or `stop_pact`;
  the convenience catalog does not claim every native definition.

```python
diplomatic_actions = await game.diplomacy.actions()
target_options = await game.diplomacy.options(target)
relations = await game.diplomacy.relations(target)
```

### Formal treaty preview, proposal and status

These methods use actual Treaty/ArticleType sources, not the older diplomatic
pact-action table. Targets are three-character country tags or complete country
IDs. Articles are genuine type keys or a list of keys; their existence is not
proof of current availability with every country, and the API chooses no
strategic allowlist.

| Method | Return / semantics |
| --- | --- |
| `treaty_articles()` | Current native article `key`/`kind` catalog; eligibility/cost require selected terms. |
| `treaty_options(target, *, articles=None, binding_days=None)` | Without articles, catalog and unknown eligibility; with articles, the same preview as `prepare_treaty`. |
| `prepare_treaty(target, *, articles, binding_days=None)` | A `PreparedTreaty` dict: actual parties, selected articles with mutual `obligation_scope`, commitment period/source, `Decimal` influence `maintenance_cost`/`party_costs`, `can_propose` and game reason. Sends nothing. |
| `terms(proposal_id)` | Accepts a prepared object for deep-copied display terms; an existing proposal full ID reads original received clause views. |
| `propose_treaty(target_or_terms, *, articles=None, binding_days=None)` | One explicit proposal from prepared terms, or target plus articles in one call; returns `TreatyProposal`. |
| `treaty_status(proposal)` | Accepts a proposal, prepared object or full ID; reads delivery/effect state even before a proposal ID exists. |
| `treaties(target=None, *, details=False, articles=None)` | Actual in-force source. Optional articles add original per-row `has_requested_articles` checks, not old pact-flag substitutes. |

The current proposal path supports mutual clauses with no declared inputs;
actual kind/declarations and the original instance are checked internally before
native validation. It does not promise support for every directed or input-bearing
article. Known preparation storage is released internally; no Reader, owner or
schema manipulation is required from the caller.

Omit `binding_days` to use the game's original default commitment.
`binding_days_source` distinguishes `native_game_default` from `explicit`.
An in-force treaty continues until broken or withdrawn; this commitment window
is neither a journal countdown nor automatic expiry. The window is not an
absolute ban on early exit, but cancellation/withdrawal within it carries the native
commitment-period penalties; after it ends, those penalties no longer apply.

Treaty costs use `cost_model="capacity_allocation"` and
`cost_unit_label="influence points"`: Influence is capacity, not a stored balance
or a periodic charge. `cost_period=None` / `cost_period_status="not_applicable"`
and `cost_period_reason` express that distinction. Compatible `maintenance_cost`,
`cost_unit="influence"` and `party_costs` remain available. A draft does not count
toward this treaty-maintenance allocation; once the treaty itself is accepted
and in force it does, although frozen/excluded treaties can contribute zero.
A native decline of a new draft does not deduct this particular capacity cost,
but is not consequence-free: verified proposal/refusal rules also include a
7-day proposal cooldown, 365-day AI-refusal memory and improved-conditions checks.
`terms(existing_prepared)` can add display metadata locally without
re-preparation or Submit.

`ai_acceptance` remains `None` with its reason when the headless path has no
initialized Draft model; neither validation nor acceptance is an AI score.

```python
article_types = await game.diplomacy.treaty_articles()
prepared = await game.diplomacy.prepare_treaty(
    target, articles=["defensive_pact"]
)
preview = await game.diplomacy.terms(prepared)
```

A preview is not a dispatched proposal. When the caller intends to send those
terms, either `await prepared.propose()` or the following call expresses that
intent; a target plus articles can also be sent directly without a separate
preview or approval step:

```python
proposal = await game.diplomacy.propose_treaty(prepared)
status = await game.diplomacy.treaty_status(proposal)
```

`PreparedTreaty` is copied game-level intent, not native ownership. Display-term
copies are not a way to edit the send intent: prepare again to change parties,
articles or commitment. Edited semantic fields are explicitly rejected before
sending rather than silently becoming a different proposal. This is intent
consistency, not a human/Root review file or approval gate.

Final sending performs fresh original validation and at most one Submit for
that prepared intent. Re-entering the same prepared object joins/reuses its
original operation. Result fields distinguish these cases:

- Validation rejection: `submission_attempted=False`, `submitted=False`,
  `accepted=None`, with the game's reason.
- Known owned failure before queue handoff: attempted true, submitted false,
  accepted unknown. No native receipt is fabricated.
- Actual receiver rejection: submitted true, accepted false; this is not an AI
  refusal. Accepted true is only the native Submit receipt, not draft creation
  or an in-force treaty.
- Unknown handoff retains its original exception/operation. It is not sent
  again to obtain a clearer result.

`await proposal.observe()`, `await prepared.status()` and `treaty_status(...)`
read delivery and draft/in-force state; they do not send another proposal.
A full explicit AI-refusal-reason reader is not implemented; disappearance or
`accepted_not_observed` does not establish an AI refusal.

Once a handle has uniquely observed its created treaty, later reads preserve
that historical `id` and `proposal_created=True`. Current presence is separate:
`current_country_membership` / `currently_observed` report the latest read, not
whether creation once happened. An unlinked known treaty reports
`status="not_in_current_country_treaties"`, `in_force=None`, `response=None`;
it is not classified as a refusal or replaced by a later same-terms treaty.

The numeric-full-ID form also requires current Country treaty-reference
membership before reporting `proposed` / `in_force`. A still-resident object or
its raw draft flag alone does not establish that membership or another country's
response. Unreadable membership stays unknown rather than becoming absence.
Both handle and numeric-ID observations have `atomic_snapshot=False`.

An observation error keeps delivery/history facts and reports
`observation_status="unknown"`. If the original read is not settled,
`observation_status="pending_original_read_settlement"` preserves it without a
new dispatch or replayed cleanup. Before any unique historical attribution,
multiple new matches report `matching_treaty_ids` and their own in-force states,
while this proposal's `proposal_created` / `in_force` stay `None` and
`attribution_unique=False`; resemblance is not unique attribution.

## Military

Start from a country directory, then select the object involved in the task.
The relationships follow the game's war, front, formation and commander views.
A directory is not a full military situation report.

```python
wars = await game.military.wars()
forces = await game.military.formations(kind="army")
war = game.military.war(war_id)
war_state = await war.read()
fronts = await war.fronts()
formation = game.military.formation(formation_id)
readiness = await formation.read()
units = await formation.units.read()
orders = await game.military.commander(character_id).orders()
```

| Selected context | Read path | Meaning |
| --- | --- | --- |
| Country | `wars()`, `formations(kind=...)` | Actual source directories, with their scope and partial results. |
| War | `war(id).read()`, `.fronts()`, `.battles()`, `.peace.read()` | Participants and sides, fronts, battles, and peace state for that selected war. |
| Formation | `formation(id).read()`, `.units.read()`, `.mobilization.read()`, `.logistics.read()` | Readiness, regular/conscript composition, mobilization choices and supply. |
| Fleet | `formation(id).naval.area()`, `.naval.battles()` | Mission regions and the fleet's actual naval battle sources. |
| Commander | `commander(id).read()`, `.orders()` | Character, assignment, current order and next-order choices. |

Formation directories can use an explicit `country_id` to read that country's
native source. Read access to another country's formation is separate from
permission to issue an order to it. Actions stay on the selected context and use
the original native command and validation path.

Read details when needed. Do not infer enemy strength from a list of your own
armies, merge opposite front sides, or call an absent source an empty force.
Native values, partial results, unknown fields and rich text remain available.
Mission region bindings use `StateRegion`, not `Province`. Queue positions,
tooltip tags and battle-list positions are not fabricated stable IDs.
