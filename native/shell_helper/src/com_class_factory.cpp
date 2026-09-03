#include "com_class_factory.h"

#include <new>

namespace dlsite::shell {

HRESULT STDMETHODCALLTYPE ShellClassFactory::QueryInterface(REFIID riid, void** object) {
    if (object == nullptr) {
        return E_POINTER;
    }
    *object = nullptr;
    if (riid == IID_IUnknown || riid == IID_IClassFactory) {
        *object = static_cast<IClassFactory*>(this);
        AddRef();
        return S_OK;
    }
    return E_NOINTERFACE;
}

ULONG STDMETHODCALLTYPE ShellClassFactory::AddRef() {
    return reference_count_.fetch_add(1, std::memory_order_relaxed) + 1;
}

ULONG STDMETHODCALLTYPE ShellClassFactory::Release() {
    const ULONG remaining = reference_count_.fetch_sub(1, std::memory_order_acq_rel) - 1;
    if (remaining == 0) {
        delete this;
    }
    return remaining;
}

HRESULT STDMETHODCALLTYPE ShellClassFactory::CreateInstance(
    IUnknown* outer,
    REFIID riid,
    void** object) {
    if (object == nullptr) {
        return E_POINTER;
    }
    *object = nullptr;
    if (outer != nullptr) {
        return CLASS_E_NOAGGREGATION;
    }

    auto* command = new (std::nothrow) ShellCommand(selection_adapter_, dispatcher_, &lifetime_);
    if (command == nullptr) {
        return E_OUTOFMEMORY;
    }
    const HRESULT result = command->QueryInterface(riid, object);
    command->Release();
    return result;
}

HRESULT STDMETHODCALLTYPE ShellClassFactory::LockServer(BOOL lock) {
    if (lock) {
        lifetime_.class_locks.fetch_add(1, std::memory_order_relaxed);
    } else {
        std::uint32_t current = lifetime_.class_locks.load(std::memory_order_relaxed);
        while (current != 0 &&
               !lifetime_.class_locks.compare_exchange_weak(
                   current,
                   current - 1,
                   std::memory_order_relaxed,
                   std::memory_order_relaxed)) {
        }
    }
    lifetime_.Touch();
    return S_OK;
}

}  // namespace dlsite::shell
