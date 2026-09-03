#pragma once

#include "ipc_client.h"

#include <string>
#include <vector>

namespace dlsite::shell {

struct LaunchResult {
    bool launched = false;
    std::wstring error;
};

class IProcessLauncher {
public:
    virtual ~IProcessLauncher() = default;
    virtual LaunchResult LaunchSiblingApplication() = 0;
};

class Win32ProcessLauncher final : public IProcessLauncher {
public:
    LaunchResult LaunchSiblingApplication() override;
};

class LaunchCoordinator final : public IBatchDispatcher {
public:
    LaunchCoordinator(IBatchIpcSender& ipc_client, IProcessLauncher& process_launcher)
        : ipc_client_(ipc_client), process_launcher_(process_launcher) {}

    DispatchResult Send(const std::vector<std::wstring>& paths) override;

private:
    IBatchIpcSender& ipc_client_;
    IProcessLauncher& process_launcher_;
};

}  // namespace dlsite::shell
