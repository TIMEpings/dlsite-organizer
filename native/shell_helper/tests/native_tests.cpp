#include "com_command.h"
#include "constants.h"
#include "identity.h"
#include "launch_coordinator.h"
#include "protocol.h"
#include "selection_adapter.h"

#include <windows.h>

#include <atomic>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <string>
#include <utility>
#include <vector>

namespace {

bool Check(bool condition, const char* message) {
    if (!condition) {
        std::cerr << "FAIL: " << message << '\n';
        return false;
    }
    return true;
}

class FakeSelectionAdapter final : public dlsite::shell::ISelectionAdapter {
public:
    HRESULT result = S_OK;
    std::vector<std::wstring> output;
    int calls = 0;

    HRESULT Extract(
        IShellItemArray* selection,
        std::vector<std::wstring>& paths,
        std::wstring& error) override {
        ++calls;
        if (selection == nullptr) {
            return E_INVALIDARG;
        }
        if (FAILED(result)) {
            paths.clear();
            error = L"fake selection failure";
            return result;
        }
        paths = output;
        error.clear();
        return S_OK;
    }
};

class FakeDispatcher final : public dlsite::shell::IBatchDispatcher {
public:
    dlsite::shell::DispatchResult result = dlsite::shell::DispatchResult::Accepted;
    std::vector<std::wstring> last_paths;
    int calls = 0;

    dlsite::shell::DispatchResult Send(const std::vector<std::wstring>& paths) override {
        ++calls;
        last_paths = paths;
        return result;
    }
};

class FakeIpcSender final : public dlsite::shell::IBatchIpcSender {
public:
    struct Attempt {
        dlsite::shell::DispatchResult result;
        bool wrote = false;
    };

    std::vector<Attempt> attempts;
    std::vector<dlsite::shell::BatchRequest> requests;
    std::size_t next_attempt = 0;
    int writes = 0;

    dlsite::shell::DispatchResult SendToPipe(
        const dlsite::shell::BatchRequest& request,
        unsigned long) override {
        requests.push_back(request);
        if (next_attempt >= attempts.size()) {
            return dlsite::shell::DispatchResult::ConnectionFailed;
        }
        const Attempt attempt = attempts[next_attempt++];
        if (attempt.wrote) {
            ++writes;
        }
        return attempt.result;
    }
};

class FakeProcessLauncher final : public dlsite::shell::IProcessLauncher {
public:
    bool launch_result = true;
    int calls = 0;

    dlsite::shell::LaunchResult LaunchSiblingApplication() override {
        ++calls;
        return {launch_result, launch_result ? L"" : L"test launch failure"};
    }
};

class FakeShellItem final : public IShellItem {
public:
    FakeShellItem(std::wstring path, SFGAOF attributes)
        : path_(std::move(path)), attributes_(attributes) {}

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** object) override {
        if (object == nullptr) {
            return E_POINTER;
        }
        *object = nullptr;
        if (riid == IID_IUnknown || riid == IID_IShellItem) {
            *object = static_cast<IShellItem*>(this);
            AddRef();
            return S_OK;
        }
        return E_NOINTERFACE;
    }

    ULONG STDMETHODCALLTYPE AddRef() override { return reference_count_.fetch_add(1) + 1; }

    ULONG STDMETHODCALLTYPE Release() override {
        const ULONG remaining = reference_count_.fetch_sub(1) - 1;
        if (remaining == 0) {
            delete this;
        }
        return remaining;
    }

    HRESULT STDMETHODCALLTYPE BindToHandler(
        IBindCtx*, REFGUID, REFIID, void**) override {
        return E_NOTIMPL;
    }

    HRESULT STDMETHODCALLTYPE GetParent(IShellItem**) override { return E_NOTIMPL; }

    HRESULT STDMETHODCALLTYPE GetDisplayName(SIGDN name, LPWSTR* output) override {
        if (output == nullptr) {
            return E_POINTER;
        }
        *output = nullptr;
        if (name != SIGDN_FILESYSPATH) {
            return E_INVALIDARG;
        }
        const std::size_t bytes = (path_.size() + 1) * sizeof(wchar_t);
        auto* memory = static_cast<wchar_t*>(CoTaskMemAlloc(bytes));
        if (memory == nullptr) {
            return E_OUTOFMEMORY;
        }
        std::memcpy(memory, path_.c_str(), bytes);
        *output = memory;
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE GetAttributes(SFGAOF mask, SFGAOF* output) override {
        if (output == nullptr) {
            return E_POINTER;
        }
        *output = attributes_ & mask;
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE Compare(IShellItem*, SICHINTF, int*) override { return E_NOTIMPL; }

private:
    ~FakeShellItem() = default;

    std::atomic<ULONG> reference_count_{1};
    std::wstring path_;
    SFGAOF attributes_;
};

class FakeShellItemArray final : public IShellItemArray {
public:
    explicit FakeShellItemArray(std::vector<IShellItem*> items) : items_(std::move(items)) {}

    ~FakeShellItemArray() {
        for (IShellItem* item : items_) {
            if (item != nullptr) {
                item->Release();
            }
        }
    }

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** object) override {
        if (object == nullptr) {
            return E_POINTER;
        }
        *object = nullptr;
        if (riid == IID_IUnknown || riid == IID_IShellItemArray) {
            *object = static_cast<IShellItemArray*>(this);
            AddRef();
            return S_OK;
        }
        return E_NOINTERFACE;
    }

    ULONG STDMETHODCALLTYPE AddRef() override { return reference_count_.fetch_add(1) + 1; }

    ULONG STDMETHODCALLTYPE Release() override {
        const ULONG remaining = reference_count_.fetch_sub(1) - 1;
        if (remaining == 0) {
            delete this;
        }
        return remaining;
    }

    HRESULT STDMETHODCALLTYPE BindToHandler(IBindCtx*, REFGUID, REFIID, void**) override {
        return E_NOTIMPL;
    }

    HRESULT STDMETHODCALLTYPE GetPropertyStore(GETPROPERTYSTOREFLAGS, REFIID, void**) override {
        return E_NOTIMPL;
    }

    HRESULT STDMETHODCALLTYPE GetPropertyDescriptionList(REFPROPERTYKEY, REFIID, void**) override {
        return E_NOTIMPL;
    }

    HRESULT STDMETHODCALLTYPE GetAttributes(SIATTRIBFLAGS, SFGAOF, SFGAOF*) override {
        return E_NOTIMPL;
    }

    HRESULT STDMETHODCALLTYPE GetCount(DWORD* count) override {
        if (count == nullptr) {
            return E_POINTER;
        }
        *count = static_cast<DWORD>(items_.size());
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE GetItemAt(DWORD index, IShellItem** item) override {
        if (item == nullptr) {
            return E_POINTER;
        }
        *item = nullptr;
        if (index >= items_.size() || items_[index] == nullptr) {
            return E_INVALIDARG;
        }
        *item = items_[index];
        (*item)->AddRef();
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE EnumItems(IEnumShellItems**) override { return E_NOTIMPL; }

private:
    std::atomic<ULONG> reference_count_{1};
    std::vector<IShellItem*> items_;
};

FakeShellItemArray* MakeShellArray(
    const std::vector<std::pair<std::wstring, SFGAOF>>& items) {
    std::vector<IShellItem*> shell_items;
    shell_items.reserve(items.size());
    for (const auto& [path, attributes] : items) {
        shell_items.push_back(new FakeShellItem(path, attributes));
    }
    return new FakeShellItemArray(std::move(shell_items));
}

bool TestIdentityAndProtocol() {
    using namespace dlsite::shell;
    std::string generated_id;
    if (!Check(GenerateRequestId(generated_id) && generated_id.size() <= kMaxRequestIdLength, "GUID request id")) {
        return false;
    }
    InstanceIdentity identity;
    if (!Check(
            BuildInstanceIdentity(L"C:\\Users\\Alice\\AppData\\Local\\dlsite-organizer", identity),
            "identity builds")) {
        return false;
    }
    if (!Check(identity.server_name.rfind(L"dlsite-organizer-", 0) == 0, "server prefix")) {
        return false;
    }
    if (!Check(identity.profile_hash.size() == 32, "profile hash truncation")) {
        return false;
    }
    if (!Check(
            identity.canonical_profile_root == L"c:\\users\\alice\\appdata\\local\\dlsite-organizer" &&
                identity.profile_hash == "2a0bada22a395e9c5c3e281435568572",
            "profile canonicalization and SHA-256 match Python golden vector")) {
        return false;
    }
    if (!Check(identity.pipe_name == L"\\\\.\\pipe\\" + identity.server_name, "pipe mapping")) {
        return false;
    }

    BatchRequest request{
        "abc-123",
        {L"C:\\作品\\A folder", L"C:\\作品\\B\\name"},
    };
    std::vector<std::uint8_t> frame;
    if (!Check(EncodeBatchRequest(request, frame), "request encoding")) {
        return false;
    }
    const std::string expected_json =
        "{\"version\":1,\"request_id\":\"abc-123\",\"command\":\"QUICK_RENAME\","
        "\"payload\":{\"paths\":[\"C:\\\\作品\\\\A folder\",\"C:\\\\作品\\\\B\\\\name\"]}}";
    const std::string actual_json(
        reinterpret_cast<const char*>(frame.data() + kFrameHeaderSize),
        frame.size() - kFrameHeaderSize);
    if (!Check(actual_json == expected_json, "request bytes match Python JSON contract")) {
        return false;
    }

    const std::string reply_json =
        "{\"version\":1,\"request_id\":\"abc-123\",\"status\":\"ACCEPTED\"}";
    std::vector<std::uint8_t> reply_frame(kFrameHeaderSize + reply_json.size());
    const auto length = static_cast<std::uint32_t>(reply_json.size());
    reply_frame[0] = static_cast<std::uint8_t>(length >> 24);
    reply_frame[1] = static_cast<std::uint8_t>(length >> 16);
    reply_frame[2] = static_cast<std::uint8_t>(length >> 8);
    reply_frame[3] = static_cast<std::uint8_t>(length);
    std::memcpy(reply_frame.data() + kFrameHeaderSize, reply_json.data(), reply_json.size());
    AdmissionReply reply;
    std::string error;
    if (!Check(DecodeAdmissionReply(reply_frame, reply, error), "reply decoding")) {
        return false;
    }
    if (!Check(reply.status == AdmissionStatus::Accepted, "reply status mapping")) {
        return false;
    }

    BatchRequest single{"single", {L"C:\\single"}};
    if (!Check(EncodeBatchRequest(single, frame), "single path batch vector")) {
        return false;
    }
    const std::wstring maximum_path = L"C:\\" + std::wstring(kMaxQuickRenamePathLength - 3, L'x');
    if (!Check(
            maximum_path.size() == kMaxQuickRenamePathLength &&
                EncodeBatchRequest(BatchRequest{"max-path", {maximum_path}}, frame),
            "maximum legal path vector")) {
        return false;
    }
    std::vector<std::wstring> oversized_paths(kMaxQuickRenameItems, maximum_path);
    if (!Check(
            !EncodeBatchRequest(BatchRequest{"oversized", oversized_paths}, frame),
            "oversized request rejected before IPC")) {
        return false;
    }
    return true;
}

bool TestSelectionValidation() {
    using namespace dlsite::shell;
    std::vector<std::wstring> output;
    std::wstring error;
    if (!Check(FAILED(ValidateSelectionPaths({}, output, error)), "empty selection rejected")) {
        return false;
    }
    if (!Check(
            SUCCEEDED(ValidateSelectionPaths({L"C:\\one"}, output, error)) && output.size() == 1,
            "one path accepted")) {
        return false;
    }
    std::vector<std::wstring> three{L"C:\\one", L"C:\\two", L"C:\\three"};
    if (!Check(SUCCEEDED(ValidateSelectionPaths(three, output, error)), "three paths accepted")) {
        return false;
    }
    std::vector<std::wstring> thirty_two;
    for (int index = 0; index < 32; ++index) {
        thirty_two.push_back(L"C:\\batch\\item-" + std::to_wstring(index));
    }
    if (!Check(SUCCEEDED(ValidateSelectionPaths(thirty_two, output, error)), "32 paths accepted")) {
        return false;
    }
    thirty_two.push_back(L"C:\\batch\\item-32");
    if (!Check(FAILED(ValidateSelectionPaths(thirty_two, output, error)), "33 paths rejected")) {
        return false;
    }
    if (!Check(
            FAILED(ValidateSelectionPaths({L"C:\\A", L"c:\\a\\."}, output, error)),
            "normalized duplicate rejected")) {
        return false;
    }
    if (!Check(
            FAILED(ValidateSelectionPaths({L"relative\\folder"}, output, error)),
            "relative path rejected")) {
        return false;
    }
    const std::wstring embedded_nul = std::wstring(L"C:\\bad") + L'\0' + L"path";
    if (!Check(
            FAILED(ValidateSelectionPaths({embedded_nul}, output, error)),
            "embedded NUL rejected by validation seam")) {
        return false;
    }
    const std::wstring long_path = L"C:\\long\\" + std::wstring(1000, L'x');
    if (!Check(
            SUCCEEDED(ValidateSelectionPaths({long_path}, output, error)),
            "long path preserved and accepted")) {
        return false;
    }
    const std::wstring embedded_duplicate = std::wstring(L"C:\\folder") + L'\0' + L"hidden";
    return Check(
        FAILED(ValidateSelectionPaths(
            {L"C:\\folder", embedded_duplicate}, output, error)),
        "NUL-containing duplicate candidate rejected");
}

bool TestShellSelectionAttributes() {
    using namespace dlsite::shell;
    ShellSelectionAdapter adapter;
    std::vector<std::wstring> paths;
    std::wstring error;
    const SFGAOF valid = SFGAO_FILESYSTEM | SFGAO_FOLDER;
    {
        FakeShellItemArray* array = MakeShellArray({{L"C:\\folder", valid}});
        const HRESULT result = adapter.Extract(array, paths, error);
        array->Release();
        if (!Check(SUCCEEDED(result) && paths == std::vector<std::wstring>{L"C:\\folder"}, "folder extracted")) {
            return false;
        }
    }
    {
        FakeShellItemArray* array = MakeShellArray({{L"C:\\file.txt", SFGAO_FILESYSTEM}});
        const HRESULT result = adapter.Extract(array, paths, error);
        array->Release();
        if (!Check(FAILED(result) && paths.empty(), "file item rejected")) {
            return false;
        }
    }
    {
        FakeShellItemArray* array = MakeShellArray({{L"shell:AppsFolder", SFGAO_FOLDER}});
        const HRESULT result = adapter.Extract(array, paths, error);
        array->Release();
        if (!Check(FAILED(result) && paths.empty(), "non-filesystem item rejected")) {
            return false;
        }
    }
    return true;
}

bool TestComCommandOneSend() {
    using namespace dlsite::shell;
    FakeSelectionAdapter selection;
    selection.output = {L"C:\\A folder", L"C:\\作品"};
    FakeDispatcher dispatcher;
    ServerLifetimeState lifetime;
    auto* command = new ShellCommand(selection, dispatcher, &lifetime);
    IObjectWithSelection* with_selection = nullptr;
    IExecuteCommand* execute = nullptr;
    if (!Check(
            SUCCEEDED(command->QueryInterface(IID_IObjectWithSelection, reinterpret_cast<void**>(&with_selection))),
            "selection interface query")) {
        command->Release();
        return false;
    }
    if (!Check(
            SUCCEEDED(command->QueryInterface(IID_IExecuteCommand, reinterpret_cast<void**>(&execute))),
            "execute interface query")) {
        with_selection->Release();
        command->Release();
        return false;
    }
    auto* dummy = reinterpret_cast<IShellItemArray*>(static_cast<std::uintptr_t>(1));
    const bool first_selected = SUCCEEDED(with_selection->SetSelection(dummy));
    selection.output = {L"C:\\C folder", L"D:\\資料"};
    const bool second_selected = SUCCEEDED(with_selection->SetSelection(dummy));
    const bool executed = SUCCEEDED(execute->Execute());
    const bool executed_again = execute->Execute() == E_UNEXPECTED;
    const bool one_send = dispatcher.calls == 1 && dispatcher.last_paths == selection.output;
    execute->Release();
    with_selection->Release();
    command->Release();
    if (!Check(
            first_selected && second_selected && executed && executed_again && one_send,
            "multiple SetSelection calls produce one batch send from the latest snapshot")) {
        return false;
    }

    FakeSelectionAdapter invalid_selection;
    invalid_selection.result = E_INVALIDARG;
    FakeDispatcher invalid_dispatcher;
    auto* invalid_command = new ShellCommand(invalid_selection, invalid_dispatcher);
    IObjectWithSelection* invalid_object = nullptr;
    IExecuteCommand* invalid_execute = nullptr;
    invalid_command->QueryInterface(IID_IObjectWithSelection, reinterpret_cast<void**>(&invalid_object));
    invalid_command->QueryInterface(IID_IExecuteCommand, reinterpret_cast<void**>(&invalid_execute));
    const bool no_selection_send =
        FAILED(invalid_object->SetSelection(dummy)) &&
        invalid_execute->Execute() == E_INVALIDARG &&
        invalid_dispatcher.calls == 0;
    invalid_execute->Release();
    invalid_object->Release();
    invalid_command->Release();
    return Check(no_selection_send, "invalid selection produces zero sends");
}

bool TestLaunchCoordinator() {
    using namespace dlsite::shell;
    const std::vector<std::wstring> paths{L"C:\\A", L"C:\\B"};

    FakeIpcSender primary_sender;
    primary_sender.attempts = {{DispatchResult::Accepted, true}};
    FakeProcessLauncher primary_launcher;
    LaunchCoordinator primary(primary_sender, primary_launcher);
    if (!Check(primary.Send(paths) == DispatchResult::Accepted, "primary running accepted")) {
        return false;
    }
    if (!Check(
            primary_launcher.calls == 0 && primary_sender.requests.size() == 1 &&
                primary_sender.writes == 1,
            "primary one attempt")) {
        return false;
    }

    FakeIpcSender no_primary_sender;
    no_primary_sender.attempts = {
        {DispatchResult::ConnectionFailed, false},
        {DispatchResult::Accepted, true},
    };
    FakeProcessLauncher no_primary_launcher;
    LaunchCoordinator no_primary(no_primary_sender, no_primary_launcher);
    if (!Check(no_primary.Send(paths) == DispatchResult::Accepted, "no-primary launch accepted")) {
        return false;
    }
    if (!Check(
            no_primary_launcher.calls == 1 && no_primary_sender.requests.size() == 2 &&
                no_primary_sender.writes == 1 &&
                no_primary_sender.requests[0].request_id == no_primary_sender.requests[1].request_id,
            "no-primary launches once and reuses one request id")) {
        return false;
    }

    FakeIpcSender ambiguous_sender;
    ambiguous_sender.attempts = {{DispatchResult::AmbiguousFailure, true}};
    FakeProcessLauncher ambiguous_launcher;
    LaunchCoordinator ambiguous(ambiguous_sender, ambiguous_launcher);
    if (!Check(ambiguous.Send(paths) == DispatchResult::AmbiguousFailure, "ambiguous result preserved")) {
        return false;
    }
    if (!Check(
            ambiguous_launcher.calls == 0 && ambiguous_sender.requests.size() == 1 &&
                ambiguous_sender.writes == 1,
            "ambiguous never relaunches")) {
        return false;
    }

    FakeIpcSender timeout_sender;
    timeout_sender.attempts = {
        {DispatchResult::ConnectionFailed, false},
        {DispatchResult::ConnectionFailed, false},
    };
    FakeProcessLauncher timeout_launcher;
    LaunchCoordinator timeout(timeout_sender, timeout_launcher);
    return Check(
            timeout.Send(paths) == DispatchResult::ConnectionFailed &&
            timeout_launcher.calls == 1 && timeout_sender.requests.size() == 2 &&
            timeout_sender.writes == 0,
        "startup timeout has one launch and no replay after no write");
}

bool TestRealSiblingLauncher() {
    using namespace dlsite::shell;
    wchar_t temporary_path[32768]{};
    const DWORD temporary_length = GetTempPathW(
        static_cast<DWORD>(std::size(temporary_path)),
        temporary_path);
    if (!Check(temporary_length != 0 && temporary_length < std::size(temporary_path), "temp path for launch test")) {
        return false;
    }
    const std::wstring marker =
        std::wstring(temporary_path) + L"dlsite-shell-helper-launch-" +
        std::to_wstring(GetCurrentProcessId()) + L".txt";
    DeleteFileW(marker.c_str());
    if (!Check(SetEnvironmentVariableW(L"DLSITE_NATIVE_LAUNCH_MARKER", marker.c_str()), "launch marker environment")) {
        return false;
    }

    Win32ProcessLauncher launcher;
    const LaunchResult result = launcher.LaunchSiblingApplication();
    if (!result.launched) {
        SetEnvironmentVariableW(L"DLSITE_NATIVE_LAUNCH_MARKER", nullptr);
        return Check(false, "sibling application launch");
    }

    bool marker_matches = false;
    for (int attempt = 0; attempt != 100; ++attempt) {
        HANDLE file = CreateFileW(
            marker.c_str(),
            GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            nullptr,
            OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL,
            nullptr);
        if (file != INVALID_HANDLE_VALUE) {
            char content[32]{};
            DWORD read = 0;
            if (ReadFile(file, content, sizeof(content) - 1, &read, nullptr)) {
                marker_matches = std::string(content, read) == "argc=1\n";
            }
            CloseHandle(file);
            if (marker_matches) {
                break;
            }
        }
        Sleep(10);
    }
    SetEnvironmentVariableW(L"DLSITE_NATIVE_LAUNCH_MARKER", nullptr);
    DeleteFileW(marker.c_str());
    return Check(marker_matches, "sibling launch has no selection argv");
}

bool TestRealNoPrimaryFlow() {
    using namespace dlsite::shell;
    wchar_t temporary_path[32768]{};
    const DWORD temporary_length = GetTempPathW(
        static_cast<DWORD>(std::size(temporary_path)),
        temporary_path);
    if (!Check(temporary_length != 0 && temporary_length < std::size(temporary_path), "temp path for no-primary test")) {
        return false;
    }
    const std::wstring suffix =
        std::to_wstring(GetCurrentProcessId()) + L"-" + std::to_wstring(GetTickCount64());
    const std::wstring marker = std::wstring(temporary_path) + L"dlsite-shell-helper-primary-" + suffix + L".txt";
    const std::wstring pipe_name = L"\\\\.\\pipe\\dlsite-shell-helper-primary-" + suffix;
    DeleteFileW(marker.c_str());
    if (!Check(SetEnvironmentVariableW(L"DLSITE_NATIVE_LAUNCH_MARKER", marker.c_str()), "no-primary marker environment")) {
        return false;
    }
    if (!Check(SetEnvironmentVariableW(L"DLSITE_NATIVE_TEST_PIPE", pipe_name.c_str()), "no-primary pipe environment")) {
        SetEnvironmentVariableW(L"DLSITE_NATIVE_LAUNCH_MARKER", nullptr);
        return false;
    }

    Win32IpcClient client(pipe_name);
    Win32ProcessLauncher launcher;
    LaunchCoordinator coordinator(client, launcher);
    const DispatchResult result = coordinator.Send({L"C:\\no-primary\\A folder", L"C:\\no-primary\\作品"});

    bool marker_matches = false;
    for (int attempt = 0; attempt != 100; ++attempt) {
        HANDLE file = CreateFileW(
            marker.c_str(),
            GENERIC_READ,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
            nullptr,
            OPEN_EXISTING,
            FILE_ATTRIBUTE_NORMAL,
            nullptr);
        if (file != INVALID_HANDLE_VALUE) {
            char content[64]{};
            DWORD read = 0;
            if (ReadFile(file, content, sizeof(content) - 1, &read, nullptr)) {
                marker_matches = std::string(content, read) == "argc=1\nrequests=1\n";
            }
            CloseHandle(file);
            if (marker_matches) {
                break;
            }
        }
        Sleep(10);
    }
    SetEnvironmentVariableW(L"DLSITE_NATIVE_TEST_PIPE", nullptr);
    SetEnvironmentVariableW(L"DLSITE_NATIVE_LAUNCH_MARKER", nullptr);
    DeleteFileW(marker.c_str());
    return Check(
        result == DispatchResult::Accepted && marker_matches,
        "no-primary launches one sibling and sends one batch without argv paths");
}

bool TestReplyValidation() {
    using namespace dlsite::shell;
    const std::vector<std::string> statuses{
        "ACCEPTED", "DUPLICATE", "QUEUE_FULL", "SHUTTING_DOWN", "REJECTED", "UNSUPPORTED_VERSION"};
    for (const std::string& status : statuses) {
        const std::string json =
            "{\"version\":1,\"request_id\":\"id1\",\"status\":\"" + status + "\"}";
        std::vector<std::uint8_t> frame(kFrameHeaderSize + json.size());
        const auto length = static_cast<std::uint32_t>(json.size());
        frame[0] = static_cast<std::uint8_t>(length >> 24);
        frame[1] = static_cast<std::uint8_t>(length >> 16);
        frame[2] = static_cast<std::uint8_t>(length >> 8);
        frame[3] = static_cast<std::uint8_t>(length);
        std::memcpy(frame.data() + kFrameHeaderSize, json.data(), json.size());
        AdmissionReply reply;
        std::string error;
        if (!Check(DecodeAdmissionReply(frame, reply, error), "all ACK statuses parse")) {
            return false;
        }
    }

    const std::string invalid_json =
        "{\"version\":1,\"request_id\":\"id1\",\"status\":\"ACCEPTED\",}";
    std::vector<std::uint8_t> invalid_frame(kFrameHeaderSize + invalid_json.size());
    const auto invalid_length = static_cast<std::uint32_t>(invalid_json.size());
    invalid_frame[0] = static_cast<std::uint8_t>(invalid_length >> 24);
    invalid_frame[1] = static_cast<std::uint8_t>(invalid_length >> 16);
    invalid_frame[2] = static_cast<std::uint8_t>(invalid_length >> 8);
    invalid_frame[3] = static_cast<std::uint8_t>(invalid_length);
    std::memcpy(invalid_frame.data() + kFrameHeaderSize, invalid_json.data(), invalid_json.size());
    AdmissionReply reply;
    std::string error;
    return Check(!DecodeAdmissionReply(invalid_frame, reply, error), "trailing comma rejected");
}

}  // namespace

int main() {
    const HRESULT com_result = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    if (FAILED(com_result)) {
        std::cerr << "FAIL: COM initialization\n";
        return 1;
    }
    const bool passed =
        TestIdentityAndProtocol() &&
        TestSelectionValidation() &&
        TestShellSelectionAttributes() &&
        TestComCommandOneSend() &&
        TestLaunchCoordinator() &&
        TestRealSiblingLauncher() &&
        TestRealNoPrimaryFlow() &&
        TestReplyValidation();
    CoUninitialize();
    if (passed) {
        std::cout << "native shell helper tests: PASS\n";
        return 0;
    }
    return 1;
}
