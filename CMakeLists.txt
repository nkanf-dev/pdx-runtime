cmake_minimum_required(VERSION 3.21)
project(pdx_runtime LANGUAGES CXX ASM_MASM)

# One release image, ordinary independent translation units. No unity build,
# export-all shortcut, custom CRT entrypoint, plugin loader or bundled side DLLs.
if(NOT CMAKE_CONFIGURATION_TYPES AND NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build configuration" FORCE)
endif()

# Compile the Win64 shim explicitly. This also creates the output directory;
# the VS MASM task does not create nested OBJECT paths on all CMake versions.
set(PDX_ASM_OBJECT "${CMAKE_CURRENT_BINARY_DIR}/$<CONFIG>/native_call_asm.obj")
add_custom_command(
    OUTPUT "${PDX_ASM_OBJECT}"
    COMMAND "${CMAKE_COMMAND}" -E make_directory "${CMAKE_CURRENT_BINARY_DIR}/$<CONFIG>"
    COMMAND "${CMAKE_ASM_MASM_COMPILER}" /nologo /c "/Fo${PDX_ASM_OBJECT}"
            "${CMAKE_CURRENT_SOURCE_DIR}/src/native/native_call.asm"
    DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/src/native/native_call.asm"
    VERBATIM
)
set_source_files_properties("${PDX_ASM_OBJECT}" PROPERTIES EXTERNAL_OBJECT TRUE GENERATED TRUE)

add_library(pdx_runtime SHARED
    src/native/pdx_runtime_bridge.cpp
    src/native/pdx_runtime_composition.cpp
    src/native/v3_bridge_dispatch.cpp
    src/native/v3_world_entry.cpp
    src/native/v3_construction.cpp
    src/native/v3_query.cpp
    src/native/v3_session_hook.cpp
    src/native/v3_construction_validate.cpp
    src/native/v3_start_game.cpp
    src/native/v3_play_finalize.cpp
    src/native/v3_data.cpp
    src/native/v3_ui_map.cpp
    src/native/v3_ui_dialogs.cpp
    src/native/v3_objects.cpp
    src/native/v3_commands.cpp
    src/native/v3_lifecycle.cpp
    src/native/v3_script.cpp
    src/native/v3_access.cpp
    src/native/native_call.cpp
    "${PDX_ASM_OBJECT}"
    src/native/v3_diagnostics.cpp
    src/native/v3_console.cpp
    src/native/v3_time_step.cpp
    src/native/v3_options.cpp
)
target_compile_features(pdx_runtime PRIVATE cxx_std_17)
target_include_directories(pdx_runtime PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src/native")
target_compile_definitions(pdx_runtime PRIVATE
    PDX_RUNTIME_BUILD
    PDX_V3_QUERY_BUILD
    PDX_V3_START_GAME_BUILD
    PDX_V3_PLAY_FINALIZE_BUILD
    PDX_V3_DATA_BUILD
    PDX_V3_OBJECTS_BUILD
    PDX_V3_COMMANDS_BUILD
    PDX_V3_LIFECYCLE_BUILD
    PDX_V3_SCRIPT_BUILD
    PDX_V3_ACCESS_BUILD
    PDX_NATIVE_CALL_BUILD
    PDX_V3_DIAGNOSTICS_BUILD
    PDX_V3_CONSOLE_BUILD
    PDX_V3_TIME_STEP_BUILD
    PDX_V3_OPTIONS_BUILD
    $<$<COMPILE_LANGUAGE:CXX>:_ITERATOR_DEBUG_LEVEL=0>
)
target_compile_options(pdx_runtime PRIVATE
    $<$<COMPILE_LANGUAGE:CXX>:/W4>
    $<$<COMPILE_LANGUAGE:CXX>:/EHsc>
    $<$<COMPILE_LANGUAGE:CXX>:/utf-8>
)
target_link_options(pdx_runtime PRIVATE /MACHINE:X64 /INCREMENTAL:NO)
set_target_properties(pdx_runtime PROPERTIES
    OUTPUT_NAME "pdx_runtime"
    PREFIX ""
    CXX_EXTENSIONS OFF
    MSVC_RUNTIME_LIBRARY "MultiThreaded"
    WINDOWS_EXPORT_ALL_SYMBOLS OFF
)

# Opt-in owned-process regression hosts. These never discover or attach to games.
option(PDX_BUILD_TEST_HOSTS "Build isolated native regression hosts" OFF)
if(PDX_BUILD_TEST_HOSTS)
    set(PDX_HOST_ASM_OBJECT "${CMAKE_CURRENT_BINARY_DIR}/$<CONFIG>/native_call_host_asm.obj")
    add_custom_command(
        OUTPUT "${PDX_HOST_ASM_OBJECT}"
        COMMAND "${CMAKE_COMMAND}" -E make_directory "${CMAKE_CURRENT_BINARY_DIR}/$<CONFIG>"
        COMMAND "${CMAKE_ASM_MASM_COMPILER}" /nologo /c "/Fo${PDX_HOST_ASM_OBJECT}"
                "${CMAKE_CURRENT_SOURCE_DIR}/src/native/tests/native_call_host.asm"
        DEPENDS "${CMAKE_CURRENT_SOURCE_DIR}/src/native/tests/native_call_host.asm"
        VERBATIM
    )
    set_source_files_properties("${PDX_HOST_ASM_OBJECT}" PROPERTIES EXTERNAL_OBJECT TRUE GENERATED TRUE)
    add_executable(pdx_bridge_host src/native/bridge_host.cpp)
    add_executable(pdx_transport_host
        src/native/tests/transport_host.cpp
        src/native/pdx_runtime_bridge.cpp)
    target_compile_definitions(pdx_transport_host PRIVATE PDX_RUNTIME_BUILD)
    target_include_directories(pdx_transport_host PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src/native")
    add_executable(pdx_native_call_host src/native/tests/native_call_host.cpp "${PDX_HOST_ASM_OBJECT}")
    add_executable(pdx_native_call_result_chaining_host src/native/tests/native_call_result_chaining_host.cpp)
    add_executable(pdx_owned_context_host src/native/tests/owned_context_host.cpp)
    add_executable(pdx_v3_data_projection_host src/native/tests/v3_data_projection_host.cpp)
    add_executable(pdx_bridge_dispatch_host
        src/native/tests/bridge_dispatch_host.cpp
        src/native/pdx_runtime_bridge.cpp
        src/native/v3_bridge_dispatch.cpp)
    target_compile_definitions(pdx_bridge_dispatch_host PRIVATE PDX_RUNTIME_BUILD)
    target_include_directories(pdx_bridge_dispatch_host PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/src/native")
    target_link_libraries(pdx_v3_data_projection_host PRIVATE pdx_runtime)
    target_link_libraries(pdx_native_call_host PRIVATE pdx_runtime)
    target_link_libraries(pdx_native_call_result_chaining_host PRIVATE pdx_runtime)
    foreach(host IN ITEMS pdx_transport_host pdx_bridge_host pdx_native_call_host pdx_native_call_result_chaining_host pdx_v3_data_projection_host pdx_owned_context_host pdx_bridge_dispatch_host)
        target_compile_features(${host} PRIVATE cxx_std_17)
        target_compile_options(${host} PRIVATE $<$<COMPILE_LANGUAGE:CXX>:/W4> $<$<COMPILE_LANGUAGE:CXX>:/EHsc>)
        set_target_properties(${host} PROPERTIES MSVC_RUNTIME_LIBRARY "MultiThreaded" CXX_EXTENSIONS OFF)
    endforeach()
    add_custom_target(pdx_regression_hosts DEPENDS pdx_runtime pdx_transport_host pdx_bridge_host pdx_native_call_host pdx_native_call_result_chaining_host pdx_v3_data_projection_host pdx_owned_context_host pdx_bridge_dispatch_host)
endif()
