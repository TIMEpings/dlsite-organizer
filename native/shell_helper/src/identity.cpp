#include "identity.h"

#include "constants.h"
#include "utf.h"

#include <windows.h>
#include <knownfolders.h>
#include <shlobj.h>
#include <bcrypt.h>

#include <algorithm>
#include <limits>
#include <memory>
#include <string_view>
#include <vector>

#pragma comment(lib, "bcrypt.lib")

namespace dlsite::shell {
namespace {

class BcryptAlgorithm final {
public:
    BcryptAlgorithm() = default;
    BcryptAlgorithm(const BcryptAlgorithm&) = delete;
    BcryptAlgorithm& operator=(const BcryptAlgorithm&) = delete;
    ~BcryptAlgorithm() {
        if (handle_ != nullptr) {
            BCryptCloseAlgorithmProvider(handle_, 0);
        }
    }

    bool Open() {
        return BCryptOpenAlgorithmProvider(&handle_, BCRYPT_SHA256_ALGORITHM, nullptr, 0) == 0;
    }

    BCRYPT_ALG_HANDLE get() const { return handle_; }

private:
    BCRYPT_ALG_HANDLE handle_ = nullptr;
};

bool GetFullPath(const std::wstring& input, std::wstring& output) {
    output.clear();
    if (input.empty() || input.size() > static_cast<std::size_t>(std::numeric_limits<DWORD>::max())) {
        return false;
    }

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

void ReplaceForwardSlashes(std::wstring& value) {
    std::replace(value.begin(), value.end(), L'/', L'\\');
}

void RemoveTrailingSeparators(std::wstring& value) {
    if (value.empty()) {
        return;
    }
    std::size_t minimum_length = 0;
    if (value.size() >= 3 && value[1] == L':' && value[2] == L'\\') {
        minimum_length = 3;
    } else if (value.rfind(L"\\\\?\\", 0) == 0 && value.size() >= 7 && value[5] == L':') {
        minimum_length = 7;
    } else if (value.rfind(L"\\\\", 0) == 0) {
        // A UNC root can be represented with or without its final separator;
        // keeping the server prefix prevents stripping the meaningful share.
        minimum_length = 2;
    }
    while (value.size() > minimum_length && value.back() == L'\\') {
        value.pop_back();
    }
}

bool InvariantLowercase(std::wstring& value) {
    if (value.empty()) {
        return true;
    }
    const int input_length = static_cast<int>(value.size());
    std::vector<wchar_t> lowered(value.size() + 1);
    const int result = LCMapStringEx(
        LOCALE_NAME_INVARIANT,
        LCMAP_LOWERCASE,
        value.data(),
        input_length,
        lowered.data(),
        static_cast<int>(lowered.size()),
        nullptr,
        nullptr,
        0);
    if (result <= 0) {
        return false;
    }
    value.assign(lowered.data(), static_cast<std::size_t>(result));
    return true;
}

bool Sha256Hex(std::string_view input, std::string& output) {
    output.clear();
    if (input.size() > static_cast<std::size_t>(std::numeric_limits<ULONG>::max())) {
        return false;
    }

    BcryptAlgorithm algorithm;
    if (!algorithm.Open()) {
        return false;
    }

    DWORD object_length = 0;
    DWORD result_length = 0;
    if (BCryptGetProperty(
            algorithm.get(),
            BCRYPT_OBJECT_LENGTH,
            reinterpret_cast<PUCHAR>(&object_length),
            sizeof(object_length),
            &result_length,
            0) != 0 ||
        result_length != sizeof(object_length) ||
        object_length == 0) {
        return false;
    }

    DWORD hash_length = 0;
    if (BCryptGetProperty(
            algorithm.get(),
            BCRYPT_HASH_LENGTH,
            reinterpret_cast<PUCHAR>(&hash_length),
            sizeof(hash_length),
            &result_length,
            0) != 0 ||
        result_length != sizeof(hash_length) ||
        hash_length != 32) {
        return false;
    }

    std::vector<UCHAR> object(object_length);
    std::vector<UCHAR> hash(hash_length);
    BCRYPT_HASH_HANDLE hash_handle = nullptr;
    if (BCryptCreateHash(
            algorithm.get(),
            &hash_handle,
            object.data(),
            object_length,
            nullptr,
            0,
            0) != 0) {
        return false;
    }
    const auto close_hash = [&]() { BCryptDestroyHash(hash_handle); };
    const NTSTATUS hash_result = BCryptHashData(
        hash_handle,
        reinterpret_cast<PUCHAR>(const_cast<char*>(input.data())),
        static_cast<ULONG>(input.size()),
        0);
    if (hash_result != 0 || BCryptFinishHash(hash_handle, hash.data(), hash_length, 0) != 0) {
        close_hash();
        return false;
    }
    close_hash();

    static constexpr char hex[] = "0123456789abcdef";
    output.reserve(hash.size() * 2);
    for (const UCHAR byte : hash) {
        output.push_back(hex[(byte >> 4) & 0x0f]);
        output.push_back(hex[byte & 0x0f]);
    }
    return true;
}

bool GetEnvironmentValue(const wchar_t* name, std::wstring& value) {
    value.clear();
    std::vector<wchar_t> buffer(512);
    for (;;) {
        const DWORD length = GetEnvironmentVariableW(name, buffer.data(), static_cast<DWORD>(buffer.size()));
        if (length == 0) {
            return false;
        }
        if (length < buffer.size()) {
            value.assign(buffer.data(), length);
            return true;
        }
        if (buffer.size() >= 32768) {
            return false;
        }
        buffer.resize(static_cast<std::size_t>(length) + 1);
    }
}

bool GetDefaultProfileRoot(std::wstring& profile_root) {
    if (GetEnvironmentValue(L"LOCALAPPDATA", profile_root) && !profile_root.empty()) {
        profile_root += L"\\dlsite-organizer";
        return true;
    }

    PWSTR known_folder = nullptr;
    const HRESULT result = SHGetKnownFolderPath(FOLDERID_LocalAppData, KF_FLAG_DEFAULT, nullptr, &known_folder);
    if (FAILED(result) || known_folder == nullptr) {
        return false;
    }
    profile_root.assign(known_folder);
    CoTaskMemFree(known_folder);
    profile_root += L"\\dlsite-organizer";
    return true;
}

}  // namespace

bool CanonicalizeProfileRoot(const std::wstring& profile_root, std::wstring& canonical) {
    if (!GetFullPath(profile_root, canonical)) {
        return false;
    }
    ReplaceForwardSlashes(canonical);
    RemoveTrailingSeparators(canonical);
    return InvariantLowercase(canonical);
}

bool BuildInstanceIdentity(const std::wstring& profile_root, InstanceIdentity& identity) {
    std::wstring canonical;
    if (!CanonicalizeProfileRoot(profile_root, canonical)) {
        return false;
    }
    std::string canonical_utf8;
    if (!WideToUtf8(canonical, canonical_utf8)) {
        return false;
    }
    std::string hash;
    if (!Sha256Hex(canonical_utf8, hash) || hash.size() < 32) {
        return false;
    }

    identity.profile_root = profile_root;
    identity.canonical_profile_root = canonical;
    identity.profile_hash = hash.substr(0, 32);
    identity.server_name = L"dlsite-organizer-";
    identity.server_name.append(identity.profile_hash.begin(), identity.profile_hash.end());
    identity.pipe_name = L"\\\\.\\pipe\\";
    identity.pipe_name += identity.server_name;
    return true;
}

bool BuildDefaultInstanceIdentity(InstanceIdentity& identity) {
    std::wstring profile_root;
    if (!GetDefaultProfileRoot(profile_root)) {
        return false;
    }
    return BuildInstanceIdentity(profile_root, identity);
}

}  // namespace dlsite::shell
