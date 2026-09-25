#include "helper_runtime.h"

#include "com_server.h"
#include "identity.h"
#include "launch_coordinator.h"

#include <objbase.h>

#include <cwchar>

namespace dlsite::shell {

int RunShellHelper(int argc, wchar_t** argv) {
    // COM launches a local server with -Embedding.  No other command-line
    // value is part of the production helper contract.
    if (argc > 2 || (argc == 2 && _wcsicmp(argv[1], L"-Embedding") != 0)) {
        return 1;
    }

    const HRESULT initialize_result = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (FAILED(initialize_result)) {
        return 1;
    }

    int exit_code = 1;
    const HRESULT security_result = CoInitializeSecurity(
        nullptr,
        -1,
        nullptr,
        nullptr,
        RPC_C_AUTHN_LEVEL_DEFAULT,
        RPC_C_IMP_LEVEL_IDENTIFY,
        nullptr,
        EOAC_NONE,
        nullptr);
    if (FAILED(security_result)) {
        CoUninitialize();
        return exit_code;
    }

    InstanceIdentity identity;
    if (BuildDefaultInstanceIdentity(identity)) {
        Win32IpcClient ipc_client(identity.pipe_name);
        Win32ProcessLauncher process_launcher;
        Win32HandoffPublisher publisher(identity.profile_root);
        LaunchCoordinator dispatcher(ipc_client, process_launcher, publisher);
        ShellSelectionAdapter selection_adapter;
        const HRESULT result = RunComLocalServer(selection_adapter, dispatcher);
        exit_code = SUCCEEDED(result) ? 0 : 1;
    }

    CoUninitialize();
    return exit_code;
}

}  // namespace dlsite::shell
