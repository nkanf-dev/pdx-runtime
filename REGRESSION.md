# Reproducible regression checks

These tools keep three kinds of evidence separate:

| Check | What it runs | Evidence label |
| --- | --- | --- |
| Runner unit tests | Fake game and OS interfaces | CPU contracts |
| Owned native hosts | Freshly built test executables and DLL | `OWNED HOST` |
| Fixed-save scenario | One explicitly selected disposable Victoria 3 process | `REAL GAME` |

**The fixed-save scenario has not been run against a game. Its status is `NOT RUN`.**
A passing owned-host check does not change that status.

## 1. Owned native hosts

Use Windows x64, Python 3.11 or later, CMake, and the MSVC x64 toolchain.
Run from the repository root:

```powershell
cmake -S . -B build/owned-host -G "Visual Studio 17 2022" -A x64 -DPDX_BUILD_TEST_HOSTS=ON
cmake --build build/owned-host --config Release --target pdx_regression_hosts --parallel
python tools/run_owned_host_regression.py --bin-dir build/owned-host/Release --out build/owned-host/evidence.json --timeout 60
```

The CMake option defaults to `OFF`. These executables are not distribution files.
The CI `owned-host` job builds and runs them separately from CPU and package checks.

The runner tests:

- The ordinary Win64 ABI: argument positions, integer and floating-point returns,
  stack alignment, register preservation, batch errors, and C++ unwinding.
- Result chaining through the real native call and Access implementations.
  The existing host has six cases and 120 checks.
- Bridge DLL loading, raw memory read/write, invalid-read recovery, and reconnect.
  This is the existing opt-in `NativeHostTests` test, not a new mock of it.

The runner creates each process itself. The bridge PID comes from its own child
handle and must match the host handshake. Inherited `PDX_NATIVE_TEST_*` selectors
are discarded. Each child has a deadline and is reaped. A skipped bridge test is
an error. There is no game discovery or game attach path.

The JSON report records binary SHA-256 hashes, host output, and the result of
each check. It always labels real-game E2E as `NOT RUN`.

## 2. Prepare a disposable real-game fixture

This scenario changes the selected game's world. It loads a save, changes one
national tax band, advances normal game time, reconnects, and reloads the save.
**Never point it at a campaign you want to continue or a controller already in use.**

Before execution:

1. Prepare a separate disposable game process with a separate user profile.
   Keep cloud sync off for that profile. Keep the original campaign and its
   process untouched. Do not run another controller against the disposable PID.
2. Put a copy of a fixed, local, non-Ironman save in that profile. Do not use the
   original save file or a link to it. Keep private saves and game binaries out
   of this repository.
3. Prepare the runtime in that disposable process through the normal SDK setup.
   This runner requires the pinned DLL to be resident already. It does not
   launch a game or load, replace, or unload a DLL. The lifecycle API can restore
   its normal hooks when it loads a world.
4. Record the exact executable, DLL, and save hashes. Record the controlled
   country's full ID, initial date, and initial tax band from the fixed fixture.
   Choose a different target tax band that the game permits. Use a fixture with
   no pending event and no expected event during the next day.
5. Keep game content, enabled DLC, and mods unchanged between runs. Record that
   configuration alongside the private manifest. This first runner hashes the
   save, executable, and runtime; it does not hash the complete game content.

The operator must verify profile isolation. The runner checks the explicit PID's
loaded executable and DLL paths and file hashes, checks the connected bridge's
module base, rejects unpinned split runtime modules, and checks that the save path
is inside the declared profile. Those checks do not prove the process's profile
selection. `isolated_profile` and `disposable_process` are explicit attestations,
not automatic discovery results.

Create a private manifest. Replace **all** placeholders with fixture values:

```json
{
  "schema": 1,
  "scenario": "tax-clock-reload-v1",
  "pid": 12345,
  "disposable_process": true,
  "isolated_profile": true,
  "profile_directory": "D:/private-regression/profile",
  "save": {
    "path": "D:/private-regression/profile/save games/fixed.v3",
    "sha256": "<64 lowercase hexadecimal characters>"
  },
  "executable": {
    "path": "D:/Games/Victoria 3/binaries/victoria3.exe",
    "sha256": "<64 lowercase hexadecimal characters>"
  },
  "runtime": {
    "path": "D:/private-regression/runtime/pdx_runtime.dll",
    "sha256": "<64 lowercase hexadecimal characters>"
  },
  "country_id": 123,
  "initial_date": "1836-01-01",
  "initial_tax_level": "medium",
  "target_tax_level": "high",
  "days": 1
}
```

Use the date string returned by `game.time.observe()`. Native tax bands are
`very_low`, `low`, `medium`, `high`, and `very_high`.
Compute each file hash with `(Get-FileHash -Algorithm SHA256 <path>).Hash.ToLower()`.
Paths can be absolute or relative to the manifest. Use a new trace directory for
each invocation; the runner refuses to overwrite an earlier trace.

### Validate without connecting

```powershell
python tools/run_game_regression.py --manifest D:/private-regression/manifest.json --out D:/private-regression/validate-001
```

This command reads local files only. It does not import the SDK, open a process,
connect to a bridge, or change a game. A valid manifest still produces `NOT RUN`.
Invalid or changed hashes also produce `NOT RUN`, with exit code 2.

### Execute only after checking isolation

```powershell
python tools/run_game_regression.py --manifest D:/private-regression/manifest.json --out D:/private-regression/run-001 --execute-isolated-game
```

The runner uses the existing SDK APIs: `NativeBridge.connect`, `RuntimeSession`,
`V3Game`, `game.load`, `game.time.observe/pause/run_for`,
`game.economy.tax_level/set_tax_level`, and `game.detach`.
It supplies no fabricated native addresses or new ABI.

The scenario:

1. Checks the explicit process and resident runtime. It never discovers a PID.
2. Rechecks the save hash and loads that exact local file with `save=False`.
3. Pauses and reads the clock, country ID, and initial tax band.
4. Submits the requested tax change once. Records acceptance separately from effect.
5. Runs one normal game day. A pending event blocks the scenario; it chooses no event option.
6. Reads the tax band again and requires the requested effect to be visible.
7. Detaches and reconnects to the same retained OS process. Requires the same date and tax band.
8. Rechecks the fixed save hash, reloads it, and requires the initial date and tax band.
9. Detaches and closes its own SDK handles. It does not quit or kill the game.

The runner never calls `save()`. The game's own autosave settings still apply;
use only the disposable profile. A failed check can leave that disposable world
at its last observed state. It does not attempt a blind rollback after failure.

## 3. Read the trace

Each private output directory contains `trace.jsonl` and `summary.json`.
The trace contains ordered calls, returned public values, and outcome markers.
It can contain local paths and gameplay data; inspect it before sharing.

| Status or marker | Meaning |
| --- | --- |
| `NOT RUN` | No game execution was requested, or preflight failed. |
| `RUNNING` | The scenario has started; no final result yet. |
| `PENDING` | The original scenario/native operation is still active. |
| `ACCEPTED` | The game accepted the action. Effect is not yet established. |
| `EFFECT OBSERVED` | A fresh game read showed the requested tax band. |
| `REJECTED` | A known native rejection or a negative action receipt. |
| `BLOCKED` | An event or another normal clock stop prevented the one-day check. |
| `UNKNOWN` | Transport, observation, or ownership failure prevents a conclusion. |
| `FAIL` | A known observation did not match the fixture or expected effect. |
| `PASS` | All steps, including reconnect and baseline reload checks, completed. |

The 30-second controller wait is an observation interval, not a cancellation
limit. On `PENDING`, the controller continues the same task and resumes the same
`PendingOperation`; it does not submit the action again. A live `TimeRun` is also
waited once, not replaced.

On `UNKNOWN`, the controller keeps its game objects and event loop alive for
inspection. Do not restart the scenario or replay its last action. Ctrl+C
abandons the controller, writes `UNKNOWN`, and can leave native work outstanding.
A trace file cannot restore native handles after controller exit. The runner
never force-kills the game to hide that uncertainty.

Exit codes are 0 for a validated dry run or a passed real run, 1 for a completed
non-passing run, 2 for invalid preflight, and 130 for operator interruption.
Always read `summary.json`: exit code 0 from validation is still `NOT RUN`.
`UNKNOWN` keeps the controller running until the operator intervenes.

## 4. Runner-only tests

```powershell
$env:PYTHONPATH = "src;tests"
python -m unittest test_regression_runners test_ci_contracts -v
```

These tests use fake game/OS interfaces. They cover fixed-save validation,
acceptance versus observed effect, known rejection versus uncertainty, pending
owner retention, event stops, reconnect/reload checks, bounded host cleanup,
and the owned-host CI job. They do not establish a real-game result.
