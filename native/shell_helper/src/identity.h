#pragma once

#include <string>

namespace dlsite::shell {

struct InstanceIdentity {
    std::wstring profile_root;
    std::wstring canonical_profile_root;
    std::string profile_hash;
    std::wstring server_name;
    std::wstring pipe_name;
};

bool CanonicalizeProfileRoot(const std::wstring& profile_root, std::wstring& canonical);
bool BuildInstanceIdentity(const std::wstring& profile_root, InstanceIdentity& identity);
bool BuildDefaultInstanceIdentity(InstanceIdentity& identity);

}  // namespace dlsite::shell
