#pragma once

#include "constants.h"
#include "protocol.h"

#include <windows.h>

#include <functional>
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
    Deferred,
    PublicationFailed,
    LaunchFailed,
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

using IpcWriteFile = std::function<BOOL(
    HANDLE handle,
    LPCVOID buffer,
    DWORD bytes_to_write,
    LPDWORD bytes_written,
    LPOVERLAPPED overlapped)>;

class Win32IpcClient final : public IBatchDispatcher, public IBatchIpcSender {
public:
    explicit Win32IpcClient(
        std::wstring pipe_name,
        unsigned long initial_connect_deadline_ms = kInitialConnectDeadlineMs,
        unsigned long ack_deadline_ms = kAckDeadlineMs);
    Win32IpcClient(
        std::wstring pipe_name,
        unsigned long initial_connect_deadline_ms,
        unsigned long ack_deadline_ms,
        IpcWriteFile write_file);

    DispatchResult Send(const std::vector<std::wstring>& paths) override;
    DispatchResult SendToPipe(
        const BatchRequest& request,
        unsigned long connect_deadline_ms) override;

private:
    std::wstring pipe_name_;
    unsigned long initial_connect_deadline_ms_;
    unsigned long ack_deadline_ms_;
    IpcWriteFile write_file_;
};

}  // namespace dlsite::shell
