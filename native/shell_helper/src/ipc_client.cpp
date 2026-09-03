#include "ipc_client.h"

#include "constants.h"

#include <windows.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <cstdint>
#include <limits>
#include <memory>
#include <string>
#include <vector>

namespace dlsite::shell {
namespace {

class UniqueHandle final {
public:
    UniqueHandle() = default;
    explicit UniqueHandle(HANDLE handle) : handle_(handle) {}
    UniqueHandle(const UniqueHandle&) = delete;
    UniqueHandle& operator=(const UniqueHandle&) = delete;
    UniqueHandle(UniqueHandle&& other) noexcept : handle_(other.release()) {}
    UniqueHandle& operator=(UniqueHandle&& other) noexcept {
        if (this != &other) {
            reset(other.release());
        }
        return *this;
    }
    ~UniqueHandle() { reset(); }

    HANDLE get() const { return handle_; }
    HANDLE release() {
        HANDLE result = handle_;
        handle_ = INVALID_HANDLE_VALUE;
        return result;
    }
    void reset(HANDLE handle = INVALID_HANDLE_VALUE) {
        if (handle_ != INVALID_HANDLE_VALUE && handle_ != nullptr) {
            CloseHandle(handle_);
        }
        handle_ = handle;
    }
    explicit operator bool() const {
        return handle_ != INVALID_HANDLE_VALUE && handle_ != nullptr;
    }

private:
    HANDLE handle_ = INVALID_HANDLE_VALUE;
};

class Deadline final {
public:
    explicit Deadline(unsigned long milliseconds)
        : end_(std::chrono::steady_clock::now() + std::chrono::milliseconds(milliseconds)) {}

    unsigned long RemainingMs() const {
        const auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(
            end_ - std::chrono::steady_clock::now());
        if (remaining.count() <= 0) {
            return 0;
        }
        const auto value = static_cast<unsigned long long>(remaining.count());
        return static_cast<unsigned long>(std::min<unsigned long long>(value, INFINITE - 1ULL));
    }

    bool Expired() const { return RemainingMs() == 0; }

private:
    std::chrono::steady_clock::time_point end_;
};

bool WaitForOverlapped(HANDLE handle, OVERLAPPED& overlapped, const Deadline& deadline) {
    const DWORD wait_result = WaitForSingleObject(overlapped.hEvent, deadline.RemainingMs());
    if (wait_result != WAIT_OBJECT_0) {
        if (wait_result == WAIT_TIMEOUT) {
            CancelIoEx(handle, &overlapped);
            WaitForSingleObject(overlapped.hEvent, 100);
        }
        return false;
    }
    return true;
}

bool WriteAll(HANDLE handle, const std::vector<std::uint8_t>& bytes, const Deadline& deadline) {
    std::size_t offset = 0;
    while (offset < bytes.size()) {
        UniqueHandle event(CreateEventW(nullptr, TRUE, FALSE, nullptr));
        if (!event) {
            return false;
        }
        OVERLAPPED overlapped{};
        overlapped.hEvent = event.get();
        const DWORD remaining = static_cast<DWORD>(std::min<std::size_t>(
            bytes.size() - offset,
            static_cast<std::size_t>(std::numeric_limits<DWORD>::max())));
        if (remaining == 0) {
            return false;
        }
        const BOOL started = WriteFile(
            handle,
            bytes.data() + offset,
            remaining,
            nullptr,
            &overlapped);
        if (!started && GetLastError() != ERROR_IO_PENDING) {
            return false;
        }
        if (!started && !WaitForOverlapped(handle, overlapped, deadline)) {
            return false;
        }
        DWORD written = 0;
        if (!GetOverlappedResult(handle, &overlapped, &written, FALSE) || written == 0) {
            return false;
        }
        offset += written;
    }
    return true;
}

bool ReadExact(
    HANDLE handle,
    std::uint8_t* destination,
    std::size_t length,
    const Deadline& deadline) {
    std::size_t offset = 0;
    while (offset < length) {
        UniqueHandle event(CreateEventW(nullptr, TRUE, FALSE, nullptr));
        if (!event) {
            return false;
        }
        OVERLAPPED overlapped{};
        overlapped.hEvent = event.get();
        const DWORD requested = static_cast<DWORD>(std::min<std::size_t>(
            length - offset,
            static_cast<std::size_t>(std::numeric_limits<DWORD>::max())));
        const BOOL started = ReadFile(
            handle,
            destination + offset,
            requested,
            nullptr,
            &overlapped);
        if (!started && GetLastError() != ERROR_IO_PENDING) {
            return false;
        }
        if (!started && !WaitForOverlapped(handle, overlapped, deadline)) {
            return false;
        }
        DWORD read = 0;
        if (!GetOverlappedResult(handle, &overlapped, &read, FALSE) || read == 0) {
            return false;
        }
        offset += read;
    }
    return true;
}

bool NoExtraPipeBytes(HANDLE handle) {
    DWORD available = 0;
    if (PeekNamedPipe(handle, nullptr, 0, nullptr, &available, nullptr)) {
        return available == 0;
    }
    const DWORD error = GetLastError();
    return error == ERROR_BROKEN_PIPE || error == ERROR_PIPE_NOT_CONNECTED;
}

bool ReadReplyFrame(HANDLE handle, const Deadline& deadline, std::vector<std::uint8_t>& frame) {
    std::array<std::uint8_t, kFrameHeaderSize> header{};
    if (!ReadExact(handle, header.data(), header.size(), deadline)) {
        return false;
    }
    const std::uint32_t payload_length =
        (static_cast<std::uint32_t>(header[0]) << 24) |
        (static_cast<std::uint32_t>(header[1]) << 16) |
        (static_cast<std::uint32_t>(header[2]) << 8) |
        static_cast<std::uint32_t>(header[3]);
    if (payload_length > kMaxResponseFrameSize - kFrameHeaderSize) {
        return false;
    }
    frame.resize(kFrameHeaderSize + payload_length);
    std::copy(header.begin(), header.end(), frame.begin());
    if (!ReadExact(
            handle,
            frame.data() + kFrameHeaderSize,
            payload_length,
            deadline)) {
        return false;
    }
    return NoExtraPipeBytes(handle);
}

enum class PipeOpenResult {
    Connected,
    Unavailable,
    AccessDenied,
    Failed,
};

PipeOpenResult OpenPipe(const std::wstring& pipe_name, const Deadline& deadline, UniqueHandle& pipe) {
    while (!deadline.Expired()) {
        HANDLE handle = CreateFileW(
            pipe_name.c_str(),
            GENERIC_READ | GENERIC_WRITE,
            0,
            nullptr,
            OPEN_EXISTING,
            FILE_FLAG_OVERLAPPED,
            nullptr);
        if (handle != INVALID_HANDLE_VALUE) {
            pipe.reset(handle);
            return PipeOpenResult::Connected;
        }
        const DWORD error = GetLastError();
        if (error == ERROR_ACCESS_DENIED) {
            return PipeOpenResult::AccessDenied;
        }
        if (error == ERROR_PIPE_BUSY) {
            const DWORD wait_ms = std::min<DWORD>(deadline.RemainingMs(), 100);
            if (wait_ms == 0 || !WaitNamedPipeW(pipe_name.c_str(), wait_ms)) {
                if (GetLastError() != ERROR_SEM_TIMEOUT && deadline.Expired()) {
                    return PipeOpenResult::Failed;
                }
            }
        } else if (error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND) {
            Sleep(std::min<unsigned long>(kConnectRetryIntervalMs, deadline.RemainingMs()));
        } else {
            return PipeOpenResult::Failed;
        }
    }
    return PipeOpenResult::Unavailable;
}

DispatchResult StatusToDispatchResult(AdmissionStatus status) {
    switch (status) {
    case AdmissionStatus::Accepted:
        return DispatchResult::Accepted;
    case AdmissionStatus::Duplicate:
        return DispatchResult::Duplicate;
    case AdmissionStatus::QueueFull:
        return DispatchResult::QueueFull;
    case AdmissionStatus::ShuttingDown:
        return DispatchResult::ShuttingDown;
    case AdmissionStatus::Rejected:
        return DispatchResult::Rejected;
    case AdmissionStatus::UnsupportedVersion:
        return DispatchResult::UnsupportedVersion;
    }
    return DispatchResult::AmbiguousFailure;
}

}  // namespace

Win32IpcClient::Win32IpcClient(
    std::wstring pipe_name,
    unsigned long initial_connect_deadline_ms,
    unsigned long ack_deadline_ms)
    : pipe_name_(std::move(pipe_name)),
      initial_connect_deadline_ms_(initial_connect_deadline_ms),
      ack_deadline_ms_(ack_deadline_ms) {}

DispatchResult Win32IpcClient::Send(const std::vector<std::wstring>& paths) {
    BatchRequest request;
    if (!GenerateRequestId(request.request_id)) {
        return DispatchResult::AmbiguousFailure;
    }
    request.paths = paths;

    const DispatchResult initial = SendToPipe(request, initial_connect_deadline_ms_);
    if (initial != DispatchResult::ConnectionFailed) {
        return initial;
    }
    return DispatchResult::ConnectionFailed;
}

DispatchResult Win32IpcClient::SendToPipe(
    const BatchRequest& request,
    unsigned long connect_deadline_ms) {
    std::vector<std::uint8_t> frame;
    if (!EncodeBatchRequest(request, frame)) {
        return DispatchResult::Rejected;
    }

    UniqueHandle pipe;
    const PipeOpenResult open_result = OpenPipe(pipe_name_, Deadline(connect_deadline_ms), pipe);
    if (open_result != PipeOpenResult::Connected) {
        if (open_result == PipeOpenResult::Unavailable) {
            return DispatchResult::ConnectionFailed;
        }
        if (open_result == PipeOpenResult::AccessDenied) {
            return DispatchResult::ConnectionDenied;
        }
        return DispatchResult::AmbiguousFailure;
    }

    const Deadline ack_deadline(ack_deadline_ms_);
    if (!WriteAll(pipe.get(), frame, ack_deadline)) {
        return DispatchResult::AmbiguousFailure;
    }

    std::vector<std::uint8_t> reply_frame;
    if (!ReadReplyFrame(pipe.get(), ack_deadline, reply_frame)) {
        return DispatchResult::AmbiguousFailure;
    }
    AdmissionReply reply;
    std::string error;
    if (!DecodeAdmissionReply(reply_frame, reply, error) || reply.request_id != request.request_id) {
        return DispatchResult::AmbiguousFailure;
    }
    return StatusToDispatchResult(reply.status);
}

}  // namespace dlsite::shell
