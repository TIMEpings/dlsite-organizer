#pragma once

#include <string>
#include <string_view>

namespace dlsite::shell {

bool WideToUtf8(std::wstring_view value, std::string& output);
bool Utf8ToWide(std::string_view value, std::wstring& output);

}  // namespace dlsite::shell
