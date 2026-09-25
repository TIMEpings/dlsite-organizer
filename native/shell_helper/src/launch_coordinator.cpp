#include "launch_coordinator.h"

#include "constants.h"

#include <windows.h>

#include <string>
#include <vector>

namespace dlsite::shell {
namespace {

bool GetModulePath(std::wstring& path) {
    path.clear();
    std::vector<wchar_t> buffer(512);
    for (;;) {
        const DWORD length = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
        if (length == 0) {
            return false;
        }
        if (length < buffer.size() - 1) {
            path.assign(buffer.data(), length);
            return true;
        }
        if (buffer.size() >= 32768) {
            return false;
        }
        buffer.resize(buffer.size() * 2);
    }
}

bool SiblingExecutablePath(std::wstring& path) {
    std::wstring module_path;
    if (!GetModulePath(module_path)) {
        return false;
    }
    const std::size_t separator = module_path.find_last_of(L"\\/");
    if (separator == std::wstring::npos) {
        return false;
    }
    path = module_path.substr(0, separator + 1);
    path += L"dlsite-organizer.exe";

    const DWORD attributes = GetFileAttributesW(path.c_str());
    if (attributes == INVALID_FILE_ATTRIBUTES || (attributes & FILE_ATTRIBUTE_DIRECTORY) != 0) {
        return false;
    }
    DWORD binary_type = 0;
    if (!GetBinaryTypeW(path.c_str(), &binary_type) ||
        (binary_type != SCS_32BIT_BINARY && binary_type != SCS_64BIT_BINARY)) {
        return false;
    }
    return true;
}

}  // namespace

LaunchResult Win32ProcessLauncher::LaunchSiblingApplication(const std::string& handoff_id) {
    std::wstring executable;
    if (!SiblingExecutablePath(executable)) {
        return {false, L"sibling application is not a regular executable"};
    }

    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION process{};
    // lpApplicationName is the validated sibling path.  The command line is a
    // fixed internal signal only: no selection path is placed on argv and no
    // shell is involved.
    std::wstring command_line = L"dlsite-organizer.exe ";
    command_line += kQuickRenameHostArgument;
    command_line += L" --handoff-id ";
    command_line.append(handoff_id.begin(), handoff_id.end());
    const BOOL created = CreateProcessW(
        executable.c_str(),
        command_line.data(),
        nullptr,
        nullptr,
        FALSE,
        0,
        nullptr,
        nullptr,
        &startup,
        &process);
    if (!created) {
        return {false, L"sibling application could not be launched"};
    }
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return {true, {}};
}

DispatchResult LaunchCoordinator::Send(const std::vector<std::wstring>& paths) {
    BatchRequest request;
    request.paths = paths;
    if (!GenerateRequestId(request.request_id)) {
        return DispatchResult::AmbiguousFailure;
    }

    const DispatchResult initial = ipc_client_.SendToPipe(request, kInitialConnectDeadlineMs);
    if (initial != DispatchResult::ConnectionFailed && initial != DispatchResult::AmbiguousFailure &&
        initial != DispatchResult::ShuttingDown) {
        if (initial == DispatchResult::Accepted || initial == DispatchResult::Duplicate) {
            OutputDebugStringW(L"DLsite Quick Rename: WARM IPC ADMITTED\n");
        }
        return initial;
    }

    const PublishResult publication = publisher_.Publish(request);
    if (publication == PublishResult::Conflict) return DispatchResult::Rejected;
    if (publication == PublishResult::Failed) return DispatchResult::PublicationFailed;
    OutputDebugStringW(L"DLsite Quick Rename: DURABLY PREPARED FOR LAUNCH\n");
    const LaunchResult launched = process_launcher_.LaunchSiblingApplication(request.request_id);
    if (!launched.launched) {
        if (!publisher_.MarkLaunchFailed(request.request_id)) {
            // Publish leaves the payload in prepared/, which the primary inbox
            // never scans. A failed recovery move therefore remains fail-closed.
            OutputDebugStringW(
                L"DLsite Quick Rename: launch failed; recovery transition failed; id=");
            OutputDebugStringA(request.request_id.c_str());
            OutputDebugStringW(L" remains non-executable in prepared\\\n");
        }
        return DispatchResult::LaunchFailed;
    }
    if (!publisher_.MarkLaunchSucceeded(request.request_id)) {
        OutputDebugStringW(
            L"DLsite Quick Rename: process launched but handoff promotion failed; id=");
        OutputDebugStringA(request.request_id.c_str());
        OutputDebugStringW(L" remains non-executable in prepared\\\n");
        return DispatchResult::PublicationFailed;
    }
    OutputDebugStringW(L"DLsite Quick Rename: HANDOFF PROMOTED TO PENDING\n");
    return DispatchResult::Deferred;
}

}  // namespace dlsite::shell
