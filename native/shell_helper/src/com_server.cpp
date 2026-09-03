#include "com_server.h"

#include "com_class_factory.h"
#include "constants.h"

#include <objbase.h>

#include <chrono>
#include <new>
#include <thread>

namespace dlsite::shell {
namespace {

class ClassRegistration final {
public:
    ClassRegistration() = default;
    ClassRegistration(const ClassRegistration&) = delete;
    ClassRegistration& operator=(const ClassRegistration&) = delete;
    ~ClassRegistration() {
        if (cookie_ != 0) {
            CoRevokeClassObject(cookie_);
        }
    }

    HRESULT Register(IClassFactory* factory, REFCLSID class_id) {
        return CoRegisterClassObject(
            class_id,
            factory,
            CLSCTX_LOCAL_SERVER,
            REGCLS_MULTIPLEUSE | REGCLS_SUSPENDED,
            &cookie_);
    }

    HRESULT Resume() { return CoResumeClassObjects(); }

    void Revoke() {
        if (cookie_ != 0) {
            CoRevokeClassObject(cookie_);
            cookie_ = 0;
        }
    }

private:
    DWORD cookie_ = 0;
};

bool IsIdle(const ServerLifetimeState& lifetime, std::uint64_t& activity, std::chrono::steady_clock::time_point& last_activity) {
    const std::uint64_t current_activity = lifetime.activity.load(std::memory_order_relaxed);
    if (current_activity != activity) {
        activity = current_activity;
        last_activity = std::chrono::steady_clock::now();
    }
    if (lifetime.object_count.load(std::memory_order_acquire) != 0 ||
        lifetime.execute_count.load(std::memory_order_acquire) != 0 ||
        lifetime.class_locks.load(std::memory_order_acquire) != 0) {
        return false;
    }
    const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::steady_clock::now() - last_activity);
    return elapsed.count() >= static_cast<long long>(kHelperIdleLifetimeMs);
}

}  // namespace

HRESULT RunComLocalServer(ISelectionAdapter& selection_adapter, IBatchDispatcher& dispatcher) {
    CLSID class_id{};
    const HRESULT parse_result = CLSIDFromString(kClassIdString, &class_id);
    if (FAILED(parse_result)) {
        return parse_result;
    }

    ServerLifetimeState lifetime;
    auto* factory = new (std::nothrow) ShellClassFactory(selection_adapter, dispatcher, lifetime);
    if (factory == nullptr) {
        return E_OUTOFMEMORY;
    }

    ClassRegistration registration;
    const HRESULT register_result = registration.Register(factory, class_id);
    if (FAILED(register_result)) {
        factory->Release();
        return register_result;
    }
    const HRESULT resume_result = registration.Resume();
    if (FAILED(resume_result)) {
        registration.Revoke();
        factory->Release();
        return resume_result;
    }

    std::uint64_t activity = lifetime.activity.load(std::memory_order_relaxed);
    auto last_activity = std::chrono::steady_clock::now();
    for (;;) {
        if (IsIdle(lifetime, activity, last_activity)) {
            break;
        }
        // COINIT_MULTITHREADED dispatches COM calls on the RPC worker pool;
        // the bounded sleep keeps this lifetime loop from busy-spinning.
        std::this_thread::sleep_for(std::chrono::milliseconds(25));
    }

    registration.Revoke();
    factory->Release();
    return S_OK;
}

}  // namespace dlsite::shell
