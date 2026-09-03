#include "identity.h"
#include "ipc_client.h"

#include <iostream>
#include <string>
#include <vector>

namespace {

bool ReadArgument(int& index, int argc, wchar_t** argv, const wchar_t* expected, std::wstring& value) {
    if (_wcsicmp(argv[index], expected) != 0 || index + 1 >= argc) {
        return false;
    }
    value = argv[++index];
    return !value.empty();
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
    // This executable is test-only.  The production COM helper has no
    // user-supplied profile/server/pipe argument surface.
    std::wstring profile_root;
    std::vector<std::wstring> paths;
    for (int index = 1; index < argc; ++index) {
        std::wstring value;
        if (ReadArgument(index, argc, argv, L"--profile-root", value)) {
            profile_root = std::move(value);
        } else if (ReadArgument(index, argc, argv, L"--path", value)) {
            paths.push_back(std::move(value));
        } else {
            return 2;
        }
    }
    if (profile_root.empty() || paths.empty()) {
        return 2;
    }

    dlsite::shell::InstanceIdentity identity;
    if (!dlsite::shell::BuildInstanceIdentity(profile_root, identity)) {
        return 2;
    }
    dlsite::shell::Win32IpcClient client(identity.pipe_name);
    const dlsite::shell::DispatchResult result = client.Send(paths);
    switch (result) {
    case dlsite::shell::DispatchResult::Accepted:
        std::cout << "ACCEPTED\n";
        return 0;
    case dlsite::shell::DispatchResult::Duplicate:
        std::cout << "DUPLICATE\n";
        return 0;
    case dlsite::shell::DispatchResult::QueueFull:
        std::cout << "QUEUE_FULL\n";
        return 0;
    case dlsite::shell::DispatchResult::ShuttingDown:
        std::cout << "SHUTTING_DOWN\n";
        return 0;
    case dlsite::shell::DispatchResult::Rejected:
        std::cout << "REJECTED\n";
        return 0;
    case dlsite::shell::DispatchResult::UnsupportedVersion:
        std::cout << "UNSUPPORTED_VERSION\n";
        return 0;
    case dlsite::shell::DispatchResult::ConnectionFailed:
        return 3;
    case dlsite::shell::DispatchResult::ConnectionDenied:
        return 5;
    case dlsite::shell::DispatchResult::AmbiguousFailure:
        return 4;
    }
    return 4;
}
