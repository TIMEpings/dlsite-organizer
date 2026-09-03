#include <windows.h>

#include <array>
#include <string>

namespace {

bool ReadEnvironment(const wchar_t* name, std::wstring& value) {
    std::array<wchar_t, 32768> buffer{};
    const DWORD length = GetEnvironmentVariableW(
        name,
        buffer.data(),
        static_cast<DWORD>(buffer.size()));
    if (length == 0 || length >= buffer.size()) {
        return false;
    }
    value.assign(buffer.data(), length);
    return true;
}

bool ReadExact(HANDLE pipe, void* destination, DWORD length) {
    auto* bytes = static_cast<BYTE*>(destination);
    DWORD offset = 0;
    while (offset < length) {
        DWORD read = 0;
        if (!ReadFile(pipe, bytes + offset, length - offset, &read, nullptr) || read == 0) {
            return false;
        }
        offset += read;
    }
    return true;
}

bool WriteAll(HANDLE pipe, const void* source, DWORD length) {
    const auto* bytes = static_cast<const BYTE*>(source);
    DWORD offset = 0;
    while (offset < length) {
        DWORD written = 0;
        if (!WriteFile(pipe, bytes + offset, length - offset, &written, nullptr) || written == 0) {
            return false;
        }
        offset += written;
    }
    return true;
}

bool WriteMarker(const std::wstring& path, const std::string& content) {
    HANDLE file = CreateFileW(
        path.c_str(),
        GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
        nullptr,
        CREATE_ALWAYS,
        FILE_ATTRIBUTE_TEMPORARY,
        nullptr);
    if (file == INVALID_HANDLE_VALUE) {
        return false;
    }
    DWORD written = 0;
    const BOOL success = WriteFile(
        file,
        content.data(),
        static_cast<DWORD>(content.size()),
        &written,
        nullptr);
    CloseHandle(file);
    return success && written == content.size();
}

bool RunPrimaryStub(const std::wstring& pipe_name, const std::wstring& marker) {
    HANDLE pipe = CreateNamedPipeW(
        pipe_name.c_str(),
        PIPE_ACCESS_DUPLEX,
        PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
        1,
        256 * 1024,
        256 * 1024,
        5000,
        nullptr);
    if (pipe == INVALID_HANDLE_VALUE) {
        return false;
    }
    BOOL connected = ConnectNamedPipe(pipe, nullptr);
    if (!connected && GetLastError() != ERROR_PIPE_CONNECTED) {
        CloseHandle(pipe);
        return false;
    }

    BYTE header[4]{};
    DWORD payload_length = 0;
    bool success = ReadExact(pipe, header, sizeof(header));
    if (success) {
        payload_length =
            (static_cast<DWORD>(header[0]) << 24) |
            (static_cast<DWORD>(header[1]) << 16) |
            (static_cast<DWORD>(header[2]) << 8) |
            static_cast<DWORD>(header[3]);
        success = payload_length <= 256 * 1024 - 4;
    }
    std::string payload(payload_length, '\0');
    if (success && payload_length != 0) {
        success = ReadExact(pipe, payload.data(), payload_length);
    }
    const std::string prefix = "\"request_id\":\"";
    const std::size_t request_start = payload.find(prefix);
    const std::size_t request_end = request_start == std::string::npos
        ? std::string::npos
        : payload.find('"', request_start + prefix.size());
    success = success && request_start != std::string::npos && request_end != std::string::npos;
    const std::string request_id = success
        ? payload.substr(request_start + prefix.size(), request_end - request_start - prefix.size())
        : std::string();
    const std::string reply =
        "{\"version\":1,\"request_id\":\"" + request_id +
        "\",\"status\":\"ACCEPTED\"}";
    const DWORD reply_length = static_cast<DWORD>(reply.size());
    BYTE reply_header[4]{
        static_cast<BYTE>(reply_length >> 24),
        static_cast<BYTE>(reply_length >> 16),
        static_cast<BYTE>(reply_length >> 8),
        static_cast<BYTE>(reply_length),
    };
    if (success) {
        success = WriteAll(pipe, reply_header, sizeof(reply_header)) &&
                  WriteAll(pipe, reply.data(), reply_length) &&
                  FlushFileBuffers(pipe);
    }
    DisconnectNamedPipe(pipe);
    CloseHandle(pipe);

    if (!marker.empty()) {
        success = WriteMarker(marker, "argc=1\nrequests=1\n") && success;
    }
    Sleep(500);
    return success;
}

}  // namespace

int wmain(int argc, wchar_t**) {
    if (argc != 1) {
        return 7;
    }

    std::wstring marker;
    if (!ReadEnvironment(L"DLSITE_NATIVE_LAUNCH_MARKER", marker)) {
        return 8;
    }
    std::wstring pipe_name;
    if (ReadEnvironment(L"DLSITE_NATIVE_TEST_PIPE", pipe_name)) {
        return RunPrimaryStub(pipe_name, marker) ? 0 : 9;
    }
    return WriteMarker(marker, "argc=1\n") ? 0 : 10;
}
