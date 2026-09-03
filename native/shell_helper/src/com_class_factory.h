#pragma once

#include "com_command.h"

#include <objbase.h>

#include <atomic>

namespace dlsite::shell {

class ShellClassFactory final : public IClassFactory {
public:
    ShellClassFactory(
        ISelectionAdapter& selection_adapter,
        IBatchDispatcher& dispatcher,
        ServerLifetimeState& lifetime)
        : selection_adapter_(selection_adapter), dispatcher_(dispatcher), lifetime_(lifetime) {}

    ShellClassFactory(const ShellClassFactory&) = delete;
    ShellClassFactory& operator=(const ShellClassFactory&) = delete;

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** object) override;
    ULONG STDMETHODCALLTYPE AddRef() override;
    ULONG STDMETHODCALLTYPE Release() override;
    HRESULT STDMETHODCALLTYPE CreateInstance(
        IUnknown* outer,
        REFIID riid,
        void** object) override;
    HRESULT STDMETHODCALLTYPE LockServer(BOOL lock) override;

private:
    ~ShellClassFactory() = default;

    std::atomic<ULONG> reference_count_{1};
    ISelectionAdapter& selection_adapter_;
    IBatchDispatcher& dispatcher_;
    ServerLifetimeState& lifetime_;
};

}  // namespace dlsite::shell
