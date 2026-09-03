#pragma once

#include "ipc_client.h"
#include "selection_adapter.h"

#include <shobjidl_core.h>

#include <atomic>
#include <cstdint>
#include <mutex>
#include <vector>

namespace dlsite::shell {

struct ServerLifetimeState {
    std::atomic<std::uint64_t> activity{0};
    std::atomic<std::uint32_t> object_count{0};
    std::atomic<std::uint32_t> execute_count{0};
    std::atomic<std::uint32_t> class_locks{0};

    void Touch() { activity.fetch_add(1, std::memory_order_relaxed); }
};

class ShellCommand final : public IExecuteCommand, public IObjectWithSelection {
public:
    ShellCommand(
        ISelectionAdapter& selection_adapter,
        IBatchDispatcher& dispatcher,
        ServerLifetimeState* lifetime = nullptr);

    ShellCommand(const ShellCommand&) = delete;
    ShellCommand& operator=(const ShellCommand&) = delete;

    // IUnknown
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** object) override;
    ULONG STDMETHODCALLTYPE AddRef() override;
    ULONG STDMETHODCALLTYPE Release() override;

    // IExecuteCommand
    HRESULT STDMETHODCALLTYPE SetKeyState(DWORD key_state) override;
    HRESULT STDMETHODCALLTYPE SetParameters(LPCWSTR parameters) override;
    HRESULT STDMETHODCALLTYPE SetPosition(POINT position) override;
    HRESULT STDMETHODCALLTYPE SetShowWindow(int show_window) override;
    HRESULT STDMETHODCALLTYPE SetNoShowUI(BOOL no_show_ui) override;
    HRESULT STDMETHODCALLTYPE SetDirectory(LPCWSTR directory) override;
    HRESULT STDMETHODCALLTYPE Execute() override;

    // IObjectWithSelection
    HRESULT STDMETHODCALLTYPE SetSelection(IShellItemArray* selection) override;
    HRESULT STDMETHODCALLTYPE GetSelection(REFIID riid, void** object) override;

private:
    ~ShellCommand();

    void ClearSelection();
    HRESULT DispatchResultToHresult(DispatchResult result) const;

    std::atomic<ULONG> reference_count_{1};
    ISelectionAdapter& selection_adapter_;
    IBatchDispatcher& dispatcher_;
    ServerLifetimeState* lifetime_;
    std::mutex mutex_;
    std::vector<std::wstring> paths_;
    bool selection_valid_ = false;
    bool executed_ = false;
};

}  // namespace dlsite::shell
