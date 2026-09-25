#pragma once

#include "ipc_client.h"
#include "durable_handoff.h"

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
    virtual LaunchResult LaunchSiblingApplication(const std::string& handoff_id) = 0;
};

class Win32ProcessLauncher final : public IProcessLauncher {
public:
    LaunchResult LaunchSiblingApplication(const std::string& handoff_id) override;
};

class LaunchCoordinator final : public IBatchDispatcher {
public:
    LaunchCoordinator(IBatchIpcSender& ipc_client, IProcessLauncher& process_launcher,
                      IHandoffPublisher& publisher)
        : ipc_client_(ipc_client), process_launcher_(process_launcher), publisher_(publisher) {}

    DispatchResult Send(const std::vector<std::wstring>& paths) override;

private:
    IBatchIpcSender& ipc_client_;
    IProcessLauncher& process_launcher_;
    IHandoffPublisher& publisher_;
};

}  // namespace dlsite::shell
