# Readable events and normal time

## Read and choose

```python
events = await game.events.pending()
```

Each event has a local instance `id`, original `key`, `title`, `body`, `flavor`,
readable `options` and unavailable-field reasons. Options include displayed
`index`, label/description, enabled/default/highlighted flags. These are real
observed option indices, not definition IDs or raw pointers. Reading installs
no policy and selects nothing. Original option descriptions are delivered
visible content, not a promise to expose hidden Script consequences.

Original-copy metadata is separate from the readable display: event
`text.title`, `text.body` and `text.flavor`, and each option's
`text.label` / `text.description`, contain `raw_text`, `raw_bytes` (Python
`bytes`, or `None` when unavailable), `complete` and `reason`. `complete`
confirms the copied native bytes/UTF-8, not exposure of all hidden effects or
every optional localization context. Invalid UTF-8 retains original bytes with
an explicit reason instead of replacing them with fabricated text.

For a chosen event row returned by `pending()`:

```python
body_copy = event["text"]["body"]
original_bytes = body_copy["raw_bytes"]
```

## Inspect additional delivered details

`details(event_id, *, option_index=None, fields=None)` discovers current native
pure scalar/text getters for the Window, its Event and original options.
`inspect(event_id, **options)` is a readable alias. Neither invokes actions.
The result keeps the normal event fields and adds `available`, `current`,
`details={"window": ..., "event": ..., "options": ...}` and field-level
`unavailable` reasons. Argument-dependent or object-returning getters remain
unknown; visible tooltip/description text is not hidden Script source.

`fields` accepts one readable name or an iterable, for example `tooltip` or
`trigger_desc`; omitted fields inspect all discoverable copied scalar details.
`option_index` limits the option group to an observed displayed index.

```python
inspection = await game.events.details(
    event_id, option_index=option_index, fields=("tooltip", "trigger_desc")
)
```

A closed/changed instance returns its availability/current-state reason, not a
substitute event. Text completeness and the original instance are checked by
the API; players do not build opaque source guards.

## Choose and observe the original choice

After the caller chooses an event and one of its displayed options:

```python
choice = await game.events.choose(event_id, option_index)
choice_effect = await game.events.choice(event_id)
events_after = await game.events.pending()
```

`choose(event_id, option_index)` refreshes the instance and enabled condition,
submits the original selection once and returns acceptance/resolution status.
The same event choice reuses its original receipt, not a second Select.
`choice(event_id)` observes/reconciles that original choice's effect without
selecting again. Inspect the resulting event/state after normal time where the
effect needs it. Other pending events are separate instances, not reason to
replay this choice.

## Visible journal entries

```python
journal = await game.events.journal_entries(details=True)
```

`journal_entries(*, details=False)` reads existing visible journal state and
returns `entries`, `available`, `unavailable` and `scope="visible"`. An available
empty list is an observed empty source; `available=False` with reasons is an
unreadable source, not proof that no entries exist. Default entry fields include
name/description, native active/valid state, status/progress/goal descriptions
and `instance_tag`; details adds original completion/failure/timeout/activation
text and available condition flags. Reading creates no entry or treaty action.

An entry's `key` / `definition_key` identifies its definition, not its current
instance. `id` can be `None` with an unavailable reason; `instance_tag` comes
from the original native tag getter, with its raw copy in `text.instance_tag`
when provided, not a fabricated key or name-based identity. Some
sources can provide only a standalone copied tooltip; unknown fields retain
reasons rather than being joined across provider positions.

Entry `text` records retain original bytes/raw text and copy/UTF-8 completeness,
using the same meaning as event text above. `complete=True` does not establish
all hidden Script semantics or effects. This describes current source/live
integration; it does not claim an older installed wheel contains the additions.

## Clock controls

| Method | Result / semantics |
| --- | --- |
| `observe()` | Actual date, `paused`, displayed speed 0..5. |
| `pause()` | Native pause; interrupts an active high-level time run. |
| `resume(speed=3)` | Native normal-time resume at displayed speed 1..5. |
| `run_for(*, days, speed=3, wait=False)` | Whole nonnegative game days; stops at a date, event, pause, interruption or error. |
| `run_until(date, *, speed=3, wait=False)` | Date string `YYYY-MM-DD`, date/datetime or `(year, month, day[, hour])`. |
| `interrupt()` | Interrupt the current run and pause after its original query settles. |

All methods are under `game.time`. Normal time uses the game's pause/speed
mechanisms, not partial-tick submissions or date replacement. No event choice
or research/completion effect is injected by clock controls.

```python
clock_run = await game.time.run_for(days=7, speed=3)
```

A running result is `TimeRun`, a dict with `background`, `watch`, `target` and
status. Its `await clock_run.wait()` gives the date/event/interruption notice;
`await clock_run.interrupt()` stops that same run. `wait=True` waits inline.
If an event or already-reached date is observed before starting, the method
returns that notice directly rather than a `TimeRun`.

The existing direct Player executor can follow the same background watch and
send the next request as an interruption; no second command protocol or mailbox
is introduced. Time notices include actual date and readable pending events.
A normal new command need not wait inside an uninterruptible multi-year call.

These are game-level Python compositions. Their native integration coverage is
separate from earlier native text/option and clock primitive validation.
