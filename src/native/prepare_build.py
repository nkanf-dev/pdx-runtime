"""Prepare an MSVC x64 build of the carrier, host and standalone V3 adapters.

Run with the project's Python interpreter, then execute the printed build command.
All generated commands and artifacts stay in the explicit output directory. Choose
an empty local directory rather than overwriting any DLL loaded by a process.
"""
from __future__ import annotations

import argparse
from pathlib import Path


# name: (artifact, BUILD macro, source files)
TARGETS = {
    "carrier": ("pdx_runtime_bridge.dll", "PDX_RUNTIME_BUILD", (
        "pdx_runtime_bridge.cpp", "v3_world_entry.cpp", "v3_construction.cpp", "v3_query.cpp")),
    "host": ("bridge_host.exe", None, ("bridge_host.cpp",)),
    "query": ("pdx_v3_query.dll", "PDX_V3_QUERY_BUILD", ("v3_query.cpp",)),
    "session-hook": ("v3_session_hook.dll", "PDX_RUNTIME_BUILD", ("v3_session_hook.cpp",)),
    "session-callbacks": ("v3_session_callbacks.dll", "PDX_RUNTIME_BUILD", ("v3_session_hook.cpp",)),
    "session-callback-host": ("v3_session_callback_host.exe", "PDX_RUNTIME_BUILD", (
        "tests/v3_session_callback_host.cpp",)),
    "construction-validate": ("pdx_v3_construction_validate.dll", "PDX_RUNTIME_BUILD", (
        "v3_construction_validate.cpp",)),
    "start-game": ("pdx_v3_start_game.dll", "PDX_V3_START_GAME_BUILD", ("v3_start_game.cpp",)),
    "options": ("pdx_v3_options.dll", "PDX_V3_OPTIONS_BUILD", ("v3_options.cpp",)),
    "options-host": ("v3_options_host.exe", None, ("tests/v3_options_host.cpp",)),
    "time-step": ("pdx_v3_time_step.dll", "PDX_V3_TIME_STEP_BUILD", ("v3_time_step.cpp",)),
    "time-step-host": ("v3_time_step_host.exe", None, (
        "tests/v3_time_step_host.cpp",)),
    "lifecycle": ("pdx_v3_lifecycle.dll", "PDX_V3_LIFECYCLE_BUILD", ("v3_lifecycle.cpp",)),
    "v3-script": ("pdx_v3_script.dll", "PDX_V3_SCRIPT_BUILD", ("v3_script.cpp",)),
    "script-random-host": ("v3_script_random_scope_host.exe", None, (
        "tests/v3_script_random_scope_host.cpp",)),
    "v3-data": ("pdx_v3_data.dll", "PDX_V3_DATA_BUILD", ("v3_data.cpp",)),
    "commands": ("pdx_v3_commands.dll", "PDX_V3_COMMANDS_BUILD", ("v3_commands.cpp",)),
    "commands-host": ("v3_commands_host.exe", "PDX_V3_COMMANDS_BUILD", (
        "v3_commands.cpp", "tests/v3_commands_host.cpp")),
    "diagnostics": ("pdx_v3_diagnostics.dll", "PDX_V3_DIAGNOSTICS_BUILD", ("v3_diagnostics.cpp",)),
    "diagnostics-host": ("v3_diagnostics_host.exe", "PDX_V3_DIAGNOSTICS_BUILD", (
        "v3_diagnostics.cpp", "tests/v3_diagnostics_host.cpp")),
    "console": ("pdx_v3_console.dll", "PDX_V3_CONSOLE_BUILD", ("v3_console.cpp",)),
    "console-host": ("v3_console_host.exe", "PDX_V3_CONSOLE_BUILD", (
        "v3_console.cpp", "tests/v3_console_host.cpp")),
    "player-control-host": ("v3_player_control_host.exe", "PDX_V3_CONSOLE_BUILD", (
        "v3_console.cpp", "tests/v3_player_control_host.cpp")),
    "ai-control-host": ("v3_ai_host.exe", "PDX_V3_CONSOLE_BUILD", (
        "v3_console.cpp", "tests/v3_ai_host.cpp")),
    "objects": ("pdx_v3_objects.dll", "PDX_V3_OBJECTS_BUILD", ("v3_objects.cpp",)),
    "objects-host": ("v3_objects_host.exe", "PDX_V3_OBJECTS_BUILD", (
        "v3_objects.cpp", "tests/v3_objects_host.cpp")),
    "access": ("pdx_v3_access.dll", "PDX_V3_ACCESS_BUILD", ("v3_access.cpp",)),
    "access-host": ("v3_access_host.exe", "PDX_V3_ACCESS_BUILD PDX_NATIVE_CALL_BUILD", (
        "v3_access.cpp", "native_call.cpp", "native_call.asm", "tests/v3_access_host.cpp")),
    "play-finalize": ("pdx_v3_play_finalize.dll", "PDX_V3_PLAY_FINALIZE_BUILD", (
        "v3_play_finalize.cpp",)),
    "ck3-query": ("ck3_query.dll", "PDX_CK3_QUERY_BUILD", ("ck3_query.cpp",)),
    "native-call": ("pdx_native_call.dll", "PDX_NATIVE_CALL_BUILD", (
        "native_call.cpp", "native_call.asm")),
    "native-call-host": ("native_call_host.exe", "PDX_NATIVE_CALL_BUILD", (
        "native_call.cpp", "native_call.asm", "tests/native_call_host.cpp",
        "tests/native_call_host.asm")),
    "native-call-chain-host": ("native_call_result_chaining_host.exe",
        "PDX_NATIVE_CALL_BUILD PDX_V3_ACCESS_BUILD", (
            "native_call.cpp", "native_call.asm", "v3_access.cpp",
            "tests/native_call_result_chaining_host.cpp")),
}


# Release builds keep ordinary translation units separate and link one image.
# Query is already in carrier; both Session development aliases share one TU.
# Never use #include .cpp unity compilation: anonymous namespaces are per TU.
RELEASE_TARGETS = (
    "carrier", "query", "session-callbacks", "construction-validate",
    "start-game", "play-finalize", "v3-data", "objects", "commands",
    "lifecycle", "v3-script", "access", "native-call", "diagnostics",
    "console", "time-step", "options",
)
RELEASE_SOURCES = tuple(dict.fromkeys(
    filename for name in RELEASE_TARGETS for filename in TARGETS[name][2]))
RELEASE_MACROS = tuple(dict.fromkeys(
    macro for name in RELEASE_TARGETS for macro in (TARGETS[name][1] or "").split()))
TARGETS["release"] = ("pdx_runtime.dll", " ".join(RELEASE_MACROS), RELEASE_SOURCES)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parent,
                        help="native source directory; useful for a private build-script candidate")
    parser.add_argument("--source-overlay", type=Path,
                        help="optional private source overlay; other inputs stay in --source")
    parser.add_argument("--output", required=True, type=Path,
                        help="private staging directory; root publishes verified DLLs to flat latest/ when unloaded")
    parser.add_argument("--target", choices=("all", *TARGETS), nargs="+", default=["all"],
                        help="default all builds development targets; release builds only pdx_runtime.dll")
    parser.add_argument("--vsdev", type=Path, default=Path(
        r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\Tools\VsDevCmd.bat"))
    args = parser.parse_args(argv)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    source = args.source.resolve()
    names = [name for name in TARGETS if name != "release"] if "all" in args.target else list(dict.fromkeys(args.target))
    commands = ["@echo off", f'call "{args.vsdev}" -arch=x64 -host_arch=x64 >nul',
                "if errorlevel 1 exit /b %errorlevel%", f'cd /d "{output}"']
    for name in names:
        artifact, macro, files = TARGETS[name]
        flags = "/LD" if artifact.endswith(".dll") else ""
        if macro:
            flags += " " + " ".join("/D" + value for value in macro.split())
        commands.append(f"echo Building {name}: {artifact}")
        inputs = []
        for filename in files:
            path = source / filename
            if args.source_overlay is not None:
                overlay = args.source_overlay.resolve() / filename
                if overlay.is_file():
                    path = overlay
            if path.suffix == ".asm":
                # Keep MASM objects distinct from cl's same-stem C++ objects.
                obj = path.stem + "_asm.obj"
                commands.extend((f'ml64.exe /nologo /c /Fo"{obj}" "{path}"',
                                 "if errorlevel 1 exit /b %errorlevel%"))
                inputs.append(f'"{obj}"')
            else:
                inputs.append(f'"{path}"')
        sources = " ".join(inputs)
        runtime_flags = "/MT /WX" if name == "script-random-host" else "/MT" if name == "release" else "/MD"
        if name == "release":
            runtime_flags += " /D_ITERATOR_DEBUG_LEVEL=0"
        commands.extend((
            f'cl.exe /nologo /std:c++17 {flags} /O2 /W4 /EHsc /utf-8 {runtime_flags} /I"{source}" {sources} '
            f'/Fe{artifact} /link /MACHINE:X64 /INCREMENTAL:NO',
            "if errorlevel 1 exit /b %errorlevel%"))
    commands.append("exit /b 0")
    command_file = output / "build.cmd"
    command_file.write_text("\r\n".join(commands) + "\r\n", encoding="utf-8", newline="")
    print(f'cmd.exe /d /c "{command_file}"')
    for name in names:
        print(f"{name}: {output / TARGETS[name][0]}")


if __name__ == "__main__":
    main()
