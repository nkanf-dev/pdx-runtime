// Owned, source-linked generic transport fixture. No DLL injection or game API.
#include "../pdx_runtime_dispatch.h"
#include <stdio.h>
#include <string.h>

static unsigned char sample[64] = {};
static HANDLE entered = nullptr, release_dispatch = nullptr, probe_entered = nullptr;
static unsigned submissions = 0, observations = 0, request_id = 0;
static bool pending = false;

static bool Dispatch(const char* command, char*, char* response, uint32_t capacity) {
    if (!strcmp(command, "hold")) {
        SetEvent(entered);
        const DWORD wait = WaitForSingleObject(release_dispatch, 10000);
        sprintf_s(response, capacity, "{\"status\":\"ok\",\"wait\":%lu}", wait);
        return true;
    }
    if (!strcmp(command, "probe")) {
        SetEvent(probe_entered);
        strcpy_s(response, capacity, "{\"status\":\"ok\",\"probe\":true}");
        return true;
    }
    if (!strcmp(command, "op-submit")) {
        ++submissions;
        if (!pending) {
            ++request_id;
            pending = true;
        }
        // Deliberately return an accepted original owner with a native error.
        // Neither the transport nor the client may infer rejection or replay it.
        sprintf_s(response, capacity,
            "{\"status\":\"error\",\"error\":170,\"delivery\":\"unknown\","
            "\"request_id\":%u,\"pending\":true,\"submissions\":%u,"
            "\"partial\":{\"value\":0,\"enabled\":false}}", request_id, submissions);
        return true;
    }
    if (!strcmp(command, "op-status")) {
        ++observations;
        sprintf_s(response, capacity,
            "{\"status\":\"ok\",\"request_id\":%u,\"pending\":%s,"
            "\"submissions\":%u,\"observations\":%u}",
            request_id, pending ? "true" : "false", submissions, observations);
        return true;
    }
    if (!strcmp(command, "op-finish")) {
        pending = false;
        sprintf_s(response, capacity, "{\"status\":\"ok\",\"request_id\":%u}", request_id);
        return true;
    }
    return false;
}

int main() {
    wchar_t name[128] = {};
    swprintf_s(name, L"Local\\pdx-transport-entered-%lu", GetCurrentProcessId());
    entered = CreateEventW(nullptr, TRUE, FALSE, name);
    swprintf_s(name, L"Local\\pdx-transport-release-%lu", GetCurrentProcessId());
    release_dispatch = CreateEventW(nullptr, TRUE, FALSE, name);
    swprintf_s(name, L"Local\\pdx-transport-probe-%lu", GetCurrentProcessId());
    probe_entered = CreateEventW(nullptr, TRUE, FALSE, name);
    if (!entered || !release_dispatch || !probe_entered) return static_cast<int>(GetLastError());
    const DWORD error = PdxRuntimeStartTransport(Dispatch);
    if (error) return static_cast<int>(error);
    const DWORD again = PdxRuntimeStartTransport(nullptr);
    printf("{\"pid\":%lu,\"sample_address\":\"0x%llx\",\"initialize_error\":%lu,"
        "\"initialize_again_error\":%lu}\n",
        GetCurrentProcessId(), reinterpret_cast<uint64_t>(sample), error, again);
    fflush(stdout);
    HANDLE stop = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    WaitForSingleObject(stop, INFINITE);
    return 0;
}
