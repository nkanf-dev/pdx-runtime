#include <Windows.h>
#include <stdio.h>
#include <stdint.h>
#include "pdx_runtime_bridge.h"

static unsigned char sample[] = {0x50, 0x44, 0x58, 0x00, 0x78, 0x36, 0x34, 0xff};

int wmain(int argc, wchar_t** argv) {
    printf("{\"pid\":%lu,\"sample_address\":\"0x%llx\",\"sample_hex\":\"50445800783634ff\"}\n",
        GetCurrentProcessId(), reinterpret_cast<uint64_t>(sample));
    fflush(stdout);
    if (argc == 2) {
        HMODULE dll = LoadLibraryW(argv[1]);
        if (!dll) return static_cast<int>(GetLastError());
        auto initialize = reinterpret_cast<DWORD(WINAPI*)(void*)>(GetProcAddress(dll, "PdxRuntimeInitialize"));
        if (!initialize) return static_cast<int>(GetLastError());
        DWORD error = initialize(nullptr);
        printf("{\"local_initialize_error\":%lu,\"dll_base\":\"0x%llx\"}\n", error, reinterpret_cast<uint64_t>(dll));
        fflush(stdout);
        if (error) return static_cast<int>(error);
    }
    // The test controller ends this host; DLL code stays loaded until process exit.
    HANDLE stop = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    WaitForSingleObject(stop, INFINITE);
    return 0;
}
