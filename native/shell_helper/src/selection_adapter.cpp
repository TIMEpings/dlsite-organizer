#include "selection_adapter.h"

#include "constants.h"

#include <windows.h>

#include <algorithm>
#include <limits>
#include <string>
#include <utility>
#include <vector>

namespace dlsite::shell {
namespace {

bool IsDriveAbsolute(const std::wstring& path) {
    return path.size() >= 3 &&
           ((path[0] >= L'a' && path[0] <= L'z') || (path[0] >= L'A' && path[0] <= L'Z')) &&
           path[1] == L':' &&
           (path[2] == L'\\' || path[2] == L'/');
}

bool IsUncAbsolute(const std::wstring& path) {
    if (path.size() >= 7 && path.compare(0, 4, L"\\\\?\\") == 0 &&
        ((path[4] >= L'a' && path[4] <= L'z') || (path[4] >= L'A' && path[4] <= L'Z')) &&
        path[5] == L':' && (path[6] == L'\\' || path[6] == L'/')) {
        return true;
    }
    if (path.size() >= 9 && path.compare(0, 8, L"\\\\?\\UNC\\") == 0) {
        const std::size_t server_end = path.find_first_of(L"\\/", 8);
        return server_end != std::wstring::npos && server_end + 1 < path.size() &&
               path[server_end + 1] != L'\\' && path[server_end + 1] != L'/';
    }
    if (path.size() < 5 || path.compare(0, 4, L"\\\\.\\") == 0 ||
        path[0] != L'\\' || path[1] != L'\\' ||
        path[2] == L'\\' || path[2] == L'/') {
        return false;
    }
    const std::size_t server_end = path.find_first_of(L"\\/", 3);
    return server_end != std::wstring::npos && server_end + 1 < path.size() &&
           path[server_end + 1] != L'\\' && path[server_end + 1] != L'/';
}

bool IsFilesystemAbsolute(const std::wstring& path) {
    return IsDriveAbsolute(path) || IsUncAbsolute(path);
}

bool GetFullPath(const std::wstring& input, std::wstring& output) {
    output.clear();
    std::vector<wchar_t> buffer(512);
    for (;;) {
        const DWORD result = GetFullPathNameW(
            input.c_str(),
            static_cast<DWORD>(buffer.size()),
            buffer.data(),
            nullptr);
        if (result == 0) {
            return false;
        }
        if (result < buffer.size()) {
            output.assign(buffer.data(), result);
            return true;
        }
        if (buffer.size() >= 32768 || result >= 32768) {
            return false;
        }
        buffer.resize(static_cast<std::size_t>(result) + 1);
    }
}

bool NormalizeForDuplicate(const std::wstring& path, std::wstring& normalized) {
    if (!GetFullPath(path, normalized)) {
        return false;
    }
    std::replace(normalized.begin(), normalized.end(), L'/', L'\\');
    std::size_t minimum_root_length = 3;
    if (normalized.size() >= 7 && normalized.compare(0, 4, L"\\\\?\\") == 0 &&
        normalized[5] == L':') {
        minimum_root_length = 7;
    } else if (normalized.size() >= 2 && normalized[0] == L'\\' && normalized[1] == L'\\') {
        minimum_root_length = 2;
    }
    while (normalized.size() > minimum_root_length && normalized.back() == L'\\') {
        normalized.pop_back();
    }
    if (normalized.empty()) {
        return false;
    }
    const int source_length = static_cast<int>(normalized.size());
    std::vector<wchar_t> lowered(normalized.size() + 1);
    const int result = LCMapStringEx(
        LOCALE_NAME_INVARIANT,
        LCMAP_LOWERCASE,
        normalized.data(),
        source_length,
        lowered.data(),
        static_cast<int>(lowered.size()),
        nullptr,
        nullptr,
        0);
    if (result <= 0) {
        return false;
    }
    normalized.assign(lowered.data(), static_cast<std::size_t>(result));
    return true;
}

bool HasDuplicate(const std::vector<std::wstring>& normalized, const std::wstring& candidate) {
    return std::any_of(normalized.begin(), normalized.end(), [&](const std::wstring& previous) {
        return CompareStringOrdinal(
                   previous.data(),
                   static_cast<int>(previous.size()),
                   candidate.data(),
                   static_cast<int>(candidate.size()),
                   TRUE) == CSTR_EQUAL;
    });
}

HRESULT Fail(std::wstring& error, const wchar_t* message) {
    error = message;
    return E_INVALIDARG;
}

}  // namespace

HRESULT ValidateSelectionPaths(
    const std::vector<std::wstring>& candidates,
    std::vector<std::wstring>& paths,
    std::wstring& error) {
    paths.clear();
    error.clear();
    if (candidates.empty()) {
        return Fail(error, L"selection is empty");
    }
    if (candidates.size() > kMaxQuickRenameItems) {
        return Fail(error, L"selection exceeds the batch limit");
    }

    std::vector<std::wstring> normalized;
    normalized.reserve(candidates.size());
    for (const std::wstring& candidate : candidates) {
        if (candidate.empty() || candidate.size() > kMaxQuickRenamePathLength ||
            !IsFilesystemAbsolute(candidate)) {
            paths.clear();
            return Fail(error, L"selection contains an invalid filesystem path");
        }
        if (std::any_of(candidate.begin(), candidate.end(), [](wchar_t character) {
                return character < 0x20 || character == 0x7f;
            })) {
            paths.clear();
            return Fail(error, L"selection contains a control character");
        }
        std::wstring normalized_path;
        if (!NormalizeForDuplicate(candidate, normalized_path)) {
            paths.clear();
            return Fail(error, L"selection path normalization failed");
        }
        if (HasDuplicate(normalized, normalized_path)) {
            paths.clear();
            return Fail(error, L"selection contains a duplicate path");
        }
        normalized.push_back(std::move(normalized_path));
        paths.push_back(candidate);
    }
    return S_OK;
}

HRESULT ShellSelectionAdapter::Extract(
    IShellItemArray* selection,
    std::vector<std::wstring>& paths,
    std::wstring& error) {
    paths.clear();
    error.clear();
    if (selection == nullptr) {
        return Fail(error, L"selection is null");
    }

    DWORD count = 0;
    HRESULT result = selection->GetCount(&count);
    if (FAILED(result)) {
        return Fail(error, L"selection count could not be read");
    }
    if (count == 0) {
        return Fail(error, L"selection is empty");
    }
    if (count > kMaxQuickRenameItems) {
        return Fail(error, L"selection exceeds the batch limit");
    }

    std::vector<std::wstring> candidates;
    candidates.reserve(count);
    for (DWORD index = 0; index < count; ++index) {
        IShellItem* item = nullptr;
        result = selection->GetItemAt(index, &item);
        if (FAILED(result) || item == nullptr) {
            if (item != nullptr) {
                item->Release();
            }
            paths.clear();
            return Fail(error, L"selection item could not be read");
        }

        SFGAOF attributes = 0;
        result = item->GetAttributes(SFGAO_FILESYSTEM | SFGAO_FOLDER, &attributes);
        if (FAILED(result) || (attributes & (SFGAO_FILESYSTEM | SFGAO_FOLDER)) !=
                                  (SFGAO_FILESYSTEM | SFGAO_FOLDER)) {
            item->Release();
            paths.clear();
            return Fail(error, L"selection contains a non-filesystem or non-folder item");
        }

        LPWSTR display_name = nullptr;
        result = item->GetDisplayName(SIGDN_FILESYSPATH, &display_name);
        item->Release();
        if (FAILED(result) || display_name == nullptr) {
            if (display_name != nullptr) {
                CoTaskMemFree(display_name);
            }
            paths.clear();
            return Fail(error, L"selection filesystem path could not be read");
        }
        const std::size_t length = wcsnlen_s(display_name, kMaxQuickRenamePathLength + 1);
        if (length == 0 || length > kMaxQuickRenamePathLength) {
            CoTaskMemFree(display_name);
            paths.clear();
            return Fail(error, L"selection filesystem path is empty or too long");
        }
        candidates.emplace_back(display_name, length);
        CoTaskMemFree(display_name);
    }

    return ValidateSelectionPaths(candidates, paths, error);
}

}  // namespace dlsite::shell
