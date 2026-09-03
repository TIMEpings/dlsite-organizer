#pragma once

#include "constants.h"
#include "protocol.h"

#include <windows.h>

#include <string>
#include <vector>

namespace dlsite::shell {

enum class DispatchResult {
    Accepted,
    Duplicate,
    QueueFull,
    ShuttingDown,
    Rejected,
    UnsupportedVersion,
    ConnectionFailed,
    ConnectionDenied,
    AmbiguousFailure,
};

class IBatchDispatcher {
public:
    virtual ~IBatchDispatcher() = default;
    virtual DispatchResult Send(const std::vector<std::wstring>& paths) = 0;
};

class IBatchIpcSender {
public:
    virtual ~IBatchIpcSender() = default;
    virtual DispatchResult SendToPipe(
        const BatchRequest& request,
        unsigned long connect_deadline_ms) = 0;
};

class Win32IpcClient final : public IBatchDispatcher, public IBatchIpcSender {
public:
    explicit Win32IpcClient(
        std::wstring pipe_name,
        unsigned long initial_connect_deadline_ms = kInitialConnectDeadlineMs,
        unsigned long ack_deadline_ms = kAckDeadlineMs);

    DispatchResult Send(const std::vector<std::wstring>& paths) override;
    DispatchResult SendToPipe(
        const BatchRequest& request,
        unsigned long connect_deadline_ms) override;

private:
    std::wstring pipe_name_;
    unsigned long initial_connect_deadline_ms_;
    unsigned long ack_deadline_ms_;
};

}  // namespace dlsite::shell
