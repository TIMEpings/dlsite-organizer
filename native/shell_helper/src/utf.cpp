#include "utf.h"

#include <windows.h>

#include <limits>

namespace dlsite::shell {
namespace {

bool FitsWin32Length(std::size_t length) {
    return length <= static_cast<std::size_t>(std::numeric_limits<int>::max());
}

}  // namespace

bool WideToUtf8(std::wstring_view value, std::string& output) {
    output.clear();
    if (!FitsWin32Length(value.size())) {
        return false;
    }

    const int input_length = static_cast<int>(value.size());
    const wchar_t* input = value.empty() ? L"" : value.data();
    const int required = WideCharToMultiByte(
        CP_UTF8,
        WC_ERR_INVALID_CHARS,
        input,
        input_length,
        nullptr,
        0,
        nullptr,
        nullptr);
    if (required < 0) {
        return false;
    }
    if (required == 0 && !value.empty()) {
        return false;
    }
    output.resize(static_cast<std::size_t>(required));
    if (required == 0) {
        return true;
    }
    const int written = WideCharToMultiByte(
        CP_UTF8,
        WC_ERR_INVALID_CHARS,
        input,
        input_length,
        output.data(),
        required,
        nullptr,
        nullptr);
    if (written != required) {
        output.clear();
        return false;
    }
    return true;
}

bool Utf8ToWide(std::string_view value, std::wstring& output) {
    output.clear();
    if (!FitsWin32Length(value.size())) {
        return false;
    }

    const int input_length = static_cast<int>(value.size());
    const char* input = value.empty() ? "" : value.data();
    const int required = MultiByteToWideChar(
        CP_UTF8,
        MB_ERR_INVALID_CHARS,
        input,
        input_length,
        nullptr,
        0);
    if (required < 0) {
        return false;
    }
    if (required == 0 && !value.empty()) {
        return false;
    }
    output.resize(static_cast<std::size_t>(required));
    if (required == 0) {
        return true;
    }
    const int written = MultiByteToWideChar(
        CP_UTF8,
        MB_ERR_INVALID_CHARS,
        input,
        input_length,
        output.data(),
        required);
    if (written != required) {
        output.clear();
        return false;
    }
    return true;
}

}  // namespace dlsite::shell
