#include "durable_handoff.h"

#include "constants.h"

#include <windows.h>
#include <objbase.h>

#include <algorithm>
#include <cstdint>
#include <string>
#include <vector>

namespace dlsite::shell {
namespace {

bool SafeId(const std::string& id) {
    return !id.empty() && id.size() <= kMaxRequestIdLength &&
        std::all_of(id.begin(), id.end(), [](unsigned char c) {
            return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                   (c >= '0' && c <= '9') || c == '-' || c == '_';
        });
}

bool EnsureDirectory(const std::wstring& path) {
    const DWORD existing = GetFileAttributesW(path.c_str());
    if (existing != INVALID_FILE_ATTRIBUTES) {
        return (existing & FILE_ATTRIBUTE_DIRECTORY) != 0 &&
               (existing & FILE_ATTRIBUTE_REPARSE_POINT) == 0;
    }
    if (!CreateDirectoryW(path.c_str(), nullptr) && GetLastError() != ERROR_ALREADY_EXISTS) {
        return false;
    }
    const DWORD attributes = GetFileAttributesW(path.c_str());
    return attributes != INVALID_FILE_ATTRIBUTES &&
           (attributes & FILE_ATTRIBUTE_DIRECTORY) != 0 &&
           (attributes & FILE_ATTRIBUTE_REPARSE_POINT) == 0;
}

bool ReadIdentical(const std::wstring& path, const std::vector<std::uint8_t>& expected) {
    HANDLE file = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE,
                              nullptr, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;
    BY_HANDLE_FILE_INFORMATION info{};
    bool identical = GetFileInformationByHandle(file, &info) &&
        (info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) == 0 &&
        info.nFileSizeHigh == 0 && info.nFileSizeLow == expected.size();
    std::vector<std::uint8_t> actual(expected.size());
    DWORD read = 0;
    if (identical) {
        identical = ReadFile(file, actual.data(), static_cast<DWORD>(actual.size()), &read, nullptr) &&
                    read == expected.size() && actual == expected;
    }
    CloseHandle(file);
    return identical;
}

}  // namespace

Win32HandoffPublisher::Win32HandoffPublisher(std::wstring profile_root)
    : profile_root_(std::move(profile_root)) {}

PublishResult Win32HandoffPublisher::Publish(const BatchRequest& request) {
    if (!SafeId(request.request_id)) return PublishResult::Failed;
    std::vector<std::uint8_t> frame;
    if (!EncodeBatchRequest(request, frame)) return PublishResult::Failed;
    const std::vector<std::uint8_t> payload(frame.begin() + kFrameHeaderSize, frame.end());
    const std::wstring base = profile_root_ + L"\\handoff";
    const std::wstring version = base + L"\\v1";
    const std::wstring prepared = version + L"\\prepared";
    if (!EnsureDirectory(profile_root_) || !EnsureDirectory(base) ||
        !EnsureDirectory(version) || !EnsureDirectory(prepared)) return PublishResult::Failed;
    const std::wstring id(request.request_id.begin(), request.request_id.end());
    const std::wstring target = prepared + L"\\" + id + L".json";
    if (GetFileAttributesW(target.c_str()) != INVALID_FILE_ATTRIBUTES) {
        return ReadIdentical(target, payload) ? PublishResult::Identical : PublishResult::Conflict;
    }
    GUID guid{};
    if (FAILED(CoCreateGuid(&guid))) return PublishResult::Failed;
    const std::wstring temporary = prepared + L"\\." + id + L"." +
        std::to_wstring(guid.Data1) + L"." + std::to_wstring(GetCurrentProcessId()) + L".tmp";
    HANDLE file = CreateFileW(temporary.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW,
                              FILE_ATTRIBUTE_NORMAL | FILE_FLAG_WRITE_THROUGH, nullptr);
    if (file == INVALID_HANDLE_VALUE) return PublishResult::Failed;
    DWORD written = 0;
    const bool complete = WriteFile(file, payload.data(), static_cast<DWORD>(payload.size()),
                                    &written, nullptr) && written == payload.size() && FlushFileBuffers(file);
    CloseHandle(file);
    if (!complete) {
        DeleteFileW(temporary.c_str());
        return PublishResult::Failed;
    }
    if (MoveFileExW(temporary.c_str(), target.c_str(), MOVEFILE_WRITE_THROUGH)) {
        return PublishResult::Committed;
    }
    DeleteFileW(temporary.c_str());
    if (GetFileAttributesW(target.c_str()) != INVALID_FILE_ATTRIBUTES) {
        return ReadIdentical(target, payload) ? PublishResult::Identical : PublishResult::Conflict;
    }
    return PublishResult::Failed;
}

bool Win32HandoffPublisher::MarkLaunchSucceeded(const std::string& request_id) {
    if (!SafeId(request_id)) return false;
    const std::wstring id(request_id.begin(), request_id.end());
    const std::wstring base = profile_root_ + L"\\handoff\\v1";
    const std::wstring pending = base + L"\\pending";
    if (!EnsureDirectory(pending)) return false;
    const std::wstring source = base + L"\\prepared\\" + id + L".json";
    const std::wstring target = pending + L"\\" + id + L".json";
    return MoveFileExW(source.c_str(), target.c_str(), MOVEFILE_WRITE_THROUGH) != 0;
}

bool Win32HandoffPublisher::MarkLaunchFailed(const std::string& request_id) {
    if (!SafeId(request_id)) return false;
    const std::wstring id(request_id.begin(), request_id.end());
    const std::wstring base = profile_root_ + L"\\handoff\\v1";
    const std::wstring recoverable = base + L"\\recoverable";
    if (!EnsureDirectory(recoverable)) return false;
    const std::wstring source = base + L"\\prepared\\" + id + L".json";
    const std::wstring target = recoverable + L"\\" + id + L".json";
    return MoveFileExW(source.c_str(), target.c_str(), MOVEFILE_WRITE_THROUGH) != 0;
}

}  // namespace dlsite::shell
