#pragma once

#include <windows.h>
#include <shobjidl_core.h>

#include <string>
#include <vector>

namespace dlsite::shell {

class ISelectionAdapter {
public:
    virtual ~ISelectionAdapter() = default;
    virtual HRESULT Extract(
        IShellItemArray* selection,
        std::vector<std::wstring>& paths,
        std::wstring& error) = 0;
};

class ShellSelectionAdapter final : public ISelectionAdapter {
public:
    HRESULT Extract(
        IShellItemArray* selection,
        std::vector<std::wstring>& paths,
        std::wstring& error) override;
};

// Shared validation seam used by the COM adapter and native unit tests.  It
// preserves input order; the primary application remains authoritative for
// any later domain-level ordering.
HRESULT ValidateSelectionPaths(
    const std::vector<std::wstring>& candidates,
    std::vector<std::wstring>& paths,
    std::wstring& error);

}  // namespace dlsite::shell
