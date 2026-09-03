#include "com_command.h"

#include <windows.h>

#include <utility>

namespace dlsite::shell {

ShellCommand::ShellCommand(
    ISelectionAdapter& selection_adapter,
    IBatchDispatcher& dispatcher,
    ServerLifetimeState* lifetime)
    : selection_adapter_(selection_adapter), dispatcher_(dispatcher), lifetime_(lifetime) {
    if (lifetime_ != nullptr) {
        lifetime_->object_count.fetch_add(1, std::memory_order_relaxed);
        lifetime_->Touch();
    }
}

ShellCommand::~ShellCommand() {
    if (lifetime_ != nullptr) {
        lifetime_->object_count.fetch_sub(1, std::memory_order_relaxed);
        lifetime_->Touch();
    }
}

HRESULT STDMETHODCALLTYPE ShellCommand::QueryInterface(REFIID riid, void** object) {
    if (object == nullptr) {
        return E_POINTER;
    }
    *object = nullptr;
    if (riid == IID_IUnknown) {
        *object = static_cast<IUnknown*>(static_cast<IExecuteCommand*>(this));
    } else if (riid == IID_IExecuteCommand) {
        *object = static_cast<IExecuteCommand*>(this);
    } else if (riid == IID_IObjectWithSelection) {
        *object = static_cast<IObjectWithSelection*>(this);
    } else {
        return E_NOINTERFACE;
    }
    AddRef();
    return S_OK;
}

ULONG STDMETHODCALLTYPE ShellCommand::AddRef() {
    return reference_count_.fetch_add(1, std::memory_order_relaxed) + 1;
}

ULONG STDMETHODCALLTYPE ShellCommand::Release() {
    const ULONG remaining = reference_count_.fetch_sub(1, std::memory_order_acq_rel) - 1;
    if (remaining == 0) {
        delete this;
    }
    return remaining;
}

HRESULT STDMETHODCALLTYPE ShellCommand::SetKeyState(DWORD key_state) {
    (void)key_state;
    return S_OK;
}

HRESULT STDMETHODCALLTYPE ShellCommand::SetParameters(LPCWSTR parameters) {
    (void)parameters;
    return S_OK;
}

HRESULT STDMETHODCALLTYPE ShellCommand::SetPosition(POINT position) {
    (void)position;
    return S_OK;
}

HRESULT STDMETHODCALLTYPE ShellCommand::SetShowWindow(int show_window) {
    (void)show_window;
    return S_OK;
}

HRESULT STDMETHODCALLTYPE ShellCommand::SetNoShowUI(BOOL no_show_ui) {
    (void)no_show_ui;
    return S_OK;
}

HRESULT STDMETHODCALLTYPE ShellCommand::SetDirectory(LPCWSTR directory) {
    (void)directory;
    return S_OK;
}

HRESULT STDMETHODCALLTYPE ShellCommand::SetSelection(IShellItemArray* selection) {
    if (selection == nullptr) {
        std::scoped_lock lock(mutex_);
        ClearSelection();
        return E_INVALIDARG;
    }

    std::vector<std::wstring> extracted;
    std::wstring error;
    const HRESULT result = selection_adapter_.Extract(selection, extracted, error);
    std::scoped_lock lock(mutex_);
    if (FAILED(result) || extracted.empty()) {
        ClearSelection();
        return FAILED(result) ? result : E_INVALIDARG;
    }
    if (executed_) {
        ClearSelection();
        return E_UNEXPECTED;
    }
    paths_ = std::move(extracted);
    selection_valid_ = true;
    if (lifetime_ != nullptr) {
        lifetime_->Touch();
    }
    return S_OK;
}

HRESULT STDMETHODCALLTYPE ShellCommand::GetSelection(REFIID riid, void** object) {
    (void)riid;
    if (object == nullptr) {
        return E_POINTER;
    }
    *object = nullptr;
    // The adapter snapshots immutable filesystem paths instead of retaining a
    // borrowed IShellItemArray.  There is intentionally no raw selection
    // object to hand back after SetSelection returns.
    return E_NOTIMPL;
}

HRESULT STDMETHODCALLTYPE ShellCommand::Execute() {
    std::vector<std::wstring> paths;
    {
        std::scoped_lock lock(mutex_);
        if (!selection_valid_ || paths_.empty()) {
            return E_INVALIDARG;
        }
        if (executed_) {
            return E_UNEXPECTED;
        }
        executed_ = true;
        paths = paths_;
    }

    if (lifetime_ != nullptr) {
        lifetime_->execute_count.fetch_add(1, std::memory_order_relaxed);
        lifetime_->Touch();
    }
    DispatchResult result = DispatchResult::AmbiguousFailure;
    try {
        result = dispatcher_.Send(paths);
    } catch (...) {
        result = DispatchResult::AmbiguousFailure;
    }
    if (lifetime_ != nullptr) {
        lifetime_->execute_count.fetch_sub(1, std::memory_order_relaxed);
        lifetime_->Touch();
    }
    return DispatchResultToHresult(result);
}

void ShellCommand::ClearSelection() {
    paths_.clear();
    selection_valid_ = false;
}

HRESULT ShellCommand::DispatchResultToHresult(DispatchResult result) const {
    switch (result) {
    case DispatchResult::Accepted:
    case DispatchResult::Duplicate:
        return S_OK;
    case DispatchResult::QueueFull:
        return HRESULT_FROM_WIN32(ERROR_BUSY);
    case DispatchResult::ShuttingDown:
        return HRESULT_FROM_WIN32(ERROR_SHUTDOWN_IN_PROGRESS);
    case DispatchResult::ConnectionFailed:
        return HRESULT_FROM_WIN32(ERROR_PIPE_NOT_CONNECTED);
    case DispatchResult::ConnectionDenied:
        return HRESULT_FROM_WIN32(ERROR_ACCESS_DENIED);
    case DispatchResult::Rejected:
    case DispatchResult::UnsupportedVersion:
        return E_FAIL;
    case DispatchResult::AmbiguousFailure:
        return HRESULT_FROM_WIN32(ERROR_IO_INCOMPLETE);
    }
    return E_FAIL;
}

}  // namespace dlsite::shell
