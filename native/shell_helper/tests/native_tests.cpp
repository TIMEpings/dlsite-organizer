#include "com_command.h"
#include "constants.h"
#include "identity.h"
#include "ipc_client.h"
#include "launch_coordinator.h"
#include "protocol.h"
#include "selection_adapter.h"

#include <windows.h>
#include <knownfolders.h>
#include <sddl.h>
#include <shlobj.h>

#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <string>
#include <thread>
#include <utility>
#include <vector>

namespace {

class ScopedEnvironmentVariable final {
public:
    explicit ScopedEnvironmentVariable(const wchar_t* name) : name_(name) {
        SetLastError(ERROR_SUCCESS);
        const DWORD required = GetEnvironmentVariableW(name_.c_str(), nullptr, 0);
        if (required == 0) {
            present_ = GetLastError() != ERROR_ENVVAR_NOT_FOUND;
            return;
        }

        std::vector<wchar_t> buffer(required);
        const DWORD copied = GetEnvironmentVariableW(
            name_.c_str(), buffer.data(), static_cast<DWORD>(buffer.size()));
        if (copied > 0 && copied < buffer.size()) {
            present_ = true;
            value_.assign(buffer.data(), copied);
        }
    }

    ScopedEnvironmentVariable(const ScopedEnvironmentVariable&) = delete;
    ScopedEnvironmentVariable& operator=(const ScopedEnvironmentVariable&) = delete;

    ~ScopedEnvironmentVariable() {
        SetEnvironmentVariableW(name_.c_str(), present_ ? value_.c_str() : nullptr);
    }

    bool Set(const wchar_t* value) const {
        return SetEnvironmentVariableW(name_.c_str(), value) != FALSE;
    }

private:
    std::wstring name_;
    std::wstring value_;
    bool present_ = false;
};

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
    std::string last_id;

    dlsite::shell::LaunchResult LaunchSiblingApplication(const std::string& id) override {
        ++calls;
        last_id = id;
        return {launch_result, launch_result ? L"" : L"test launch failure"};
    }
};

class FakePublisher final : public dlsite::shell::IHandoffPublisher {
public:
    enum class Location { Empty, Prepared, Pending, Recoverable };

    dlsite::shell::PublishResult result = dlsite::shell::PublishResult::Committed;
    dlsite::shell::BatchRequest request;
    int calls = 0;
    int launch_successes = 0;
    int launch_failures = 0;
    dlsite::shell::PublishResult Publish(const dlsite::shell::BatchRequest& value) override {
        request = value;
        ++calls;
        if (result == dlsite::shell::PublishResult::Committed ||
            result == dlsite::shell::PublishResult::Identical) {
            location = Location::Prepared;
        }
        return result;
    }
    bool MarkLaunchSucceeded(const std::string&) override {
        ++launch_successes;
        if (!launch_success_transition_succeeds || location != Location::Prepared) return false;
        location = Location::Pending;
        return true;
    }
    bool MarkLaunchFailed(const std::string&) override {
        ++launch_failures;
        if (!launch_failure_transition_succeeds || location != Location::Prepared) return false;
        location = Location::Recoverable;
        return true;
    }

    bool launch_success_transition_succeeds = true;
    bool launch_failure_transition_succeeds = true;
    Location location = Location::Empty;
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
    std::vector<std::wstring> near_payload_paths;
    for (int index = 0; index != 6; ++index) {
        near_payload_paths.push_back(
            L"C:\\payload-" + std::to_wstring(index) + L"\\" + std::wstring(31500, L'x'));
    }
    if (!Check(
            EncodeBatchRequest(BatchRequest{"near-cap", near_payload_paths}, frame) &&
                frame.size() - kFrameHeaderSize <= kMaxQuickRenameRequestPayloadSize,
            "request near 192 KiB helper cap is encodable")) {
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
    if (!Check(no_selection_send, "invalid selection produces zero sends")) return false;

    FakeSelectionAdapter launch_failed_selection;
    launch_failed_selection.output = {L"C:\\launch-failed"};
    FakeDispatcher launch_failed_dispatcher;
    launch_failed_dispatcher.result = DispatchResult::LaunchFailed;
    auto* launch_failed_command = new ShellCommand(
        launch_failed_selection, launch_failed_dispatcher);
    IObjectWithSelection* launch_failed_object = nullptr;
    IExecuteCommand* launch_failed_execute = nullptr;
    launch_failed_command->QueryInterface(
        IID_IObjectWithSelection, reinterpret_cast<void**>(&launch_failed_object));
    launch_failed_command->QueryInterface(
        IID_IExecuteCommand, reinterpret_cast<void**>(&launch_failed_execute));
    const bool launch_failure_reported =
        SUCCEEDED(launch_failed_object->SetSelection(dummy)) &&
        launch_failed_execute->Execute() == HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND);
    launch_failed_execute->Release();
    launch_failed_object->Release();
    launch_failed_command->Release();
    return Check(launch_failure_reported, "COM caller receives explicit launch failure");
}

bool TestDefaultIdentityKnownFolderFallback() {
    using namespace dlsite::shell;

    PWSTR known_folder = nullptr;
    const HRESULT known_folder_result = SHGetKnownFolderPath(
        FOLDERID_LocalAppData, KF_FLAG_DEFAULT, nullptr, &known_folder);
    if (!Check(
            SUCCEEDED(known_folder_result) && known_folder != nullptr,
            "LocalAppData Known Folder resolves for parity test")) {
        if (known_folder != nullptr) CoTaskMemFree(known_folder);
        return false;
    }
    std::wstring expected_profile_root(known_folder);
    CoTaskMemFree(known_folder);
    expected_profile_root += L"\\dlsite-organizer";

    InstanceIdentity expected_identity;
    if (!Check(
            BuildInstanceIdentity(expected_profile_root, expected_identity),
            "expected Known Folder identity builds")) {
        return false;
    }

    ScopedEnvironmentVariable local_app_data(L"LOCALAPPDATA");
    const std::pair<const wchar_t*, const char*> scenarios[] = {
        {nullptr, "missing LOCALAPPDATA uses LocalAppData Known Folder"},
        {L"", "empty LOCALAPPDATA uses LocalAppData Known Folder"},
    };
    for (const auto& [value, label] : scenarios) {
        if (!Check(local_app_data.Set(value), label)) return false;
        InstanceIdentity actual_identity;
        if (!Check(BuildDefaultInstanceIdentity(actual_identity), label) ||
            !Check(
                actual_identity.canonical_profile_root == expected_identity.canonical_profile_root &&
                    actual_identity.profile_hash == expected_identity.profile_hash &&
                    actual_identity.pipe_name == expected_identity.pipe_name,
                label)) {
            return false;
        }
    }
    return true;
}

bool TestLaunchCoordinator() {
    using namespace dlsite::shell;
    const std::vector<std::wstring> paths{L"C:\\A", L"C:\\B"};

    FakeIpcSender primary_sender;
    primary_sender.attempts = {{DispatchResult::Accepted, true}};
    FakeProcessLauncher primary_launcher;
    FakePublisher primary_publisher;
    LaunchCoordinator primary(primary_sender, primary_launcher, primary_publisher);
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
    no_primary_sender.attempts = {{DispatchResult::ConnectionFailed, false}};
    FakeProcessLauncher no_primary_launcher;
    FakePublisher no_primary_publisher;
    LaunchCoordinator no_primary(no_primary_sender, no_primary_launcher, no_primary_publisher);
    if (!Check(no_primary.Send(paths) == DispatchResult::Deferred, "no-primary launch deferred")) {
        return false;
    }
    if (!Check(
            no_primary_launcher.calls == 1 && no_primary_sender.requests.size() == 1 &&
                no_primary_publisher.calls == 1 &&
                no_primary_sender.requests[0].request_id == no_primary_publisher.request.request_id &&
                no_primary_sender.requests[0].paths == no_primary_publisher.request.paths &&
                no_primary_launcher.last_id == no_primary_publisher.request.request_id &&
                no_primary_publisher.location == FakePublisher::Location::Pending &&
                no_primary_publisher.launch_successes == 1,
            "launch success promotes the original prepared request to pending")) {
        return false;
    }

    FakeIpcSender ambiguous_sender;
    ambiguous_sender.attempts = {{DispatchResult::AmbiguousFailure, true}};
    FakeProcessLauncher ambiguous_launcher;
    FakePublisher ambiguous_publisher;
    LaunchCoordinator ambiguous(ambiguous_sender, ambiguous_launcher, ambiguous_publisher);
    if (!Check(ambiguous.Send(paths) == DispatchResult::Deferred, "ambiguous request deferred")) {
        return false;
    }
    if (!Check(
            ambiguous_launcher.calls == 1 && ambiguous_sender.requests.size() == 1 &&
                ambiguous_sender.writes == 1 && ambiguous_publisher.calls == 1 &&
                ambiguous_sender.requests[0].request_id == ambiguous_publisher.request.request_id &&
                ambiguous_sender.requests[0].paths == ambiguous_publisher.request.paths &&
                ambiguous_launcher.last_id == ambiguous_publisher.request.request_id,
            "ambiguous publishes original request")) {
        return false;
    }

    FakeIpcSender timeout_sender;
    timeout_sender.attempts = {
        {DispatchResult::ConnectionFailed, false},
        {DispatchResult::ConnectionFailed, false},
    };
    FakeProcessLauncher timeout_launcher;
    FakePublisher timeout_publisher;
    LaunchCoordinator timeout(timeout_sender, timeout_launcher, timeout_publisher);
    if (!Check(
            timeout.Send(paths) == DispatchResult::Deferred &&
            timeout_launcher.calls == 1 && timeout_sender.requests.size() == 1 &&
            timeout_sender.writes == 0,
        "cold request commits without startup timeout")) return false;

    FakeIpcSender failed_sender;
    failed_sender.attempts = {{DispatchResult::ConnectionFailed, false}};
    FakeProcessLauncher failed_launcher;
    failed_launcher.launch_result = false;
    FakePublisher failed_publisher;
    LaunchCoordinator launch_failure(failed_sender, failed_launcher, failed_publisher);
    if (!Check(launch_failure.Send(paths) == DispatchResult::LaunchFailed &&
               failed_publisher.calls == 1 && failed_publisher.launch_failures == 1 &&
               failed_publisher.location == FakePublisher::Location::Recoverable &&
               failed_publisher.request.request_id == failed_launcher.last_id,
               "launch failure moves the same request to recoverable")) return false;

    FakeIpcSender unmarked_sender;
    unmarked_sender.attempts = {{DispatchResult::ConnectionFailed, false}};
    FakeProcessLauncher unmarked_launcher;
    unmarked_launcher.launch_result = false;
    FakePublisher unmarked_publisher;
    unmarked_publisher.launch_failure_transition_succeeds = false;
    LaunchCoordinator unmarked_failure(unmarked_sender, unmarked_launcher, unmarked_publisher);
    if (!Check(unmarked_failure.Send(paths) == DispatchResult::LaunchFailed &&
               unmarked_publisher.calls == 1 && unmarked_publisher.launch_failures == 1 &&
               unmarked_publisher.location == FakePublisher::Location::Prepared &&
               unmarked_sender.requests.size() == 1 &&
               unmarked_publisher.request.request_id == unmarked_sender.requests[0].request_id &&
               unmarked_publisher.request.paths == paths,
               "failed launch-state transition leaves the same request non-executable")) {
        return false;
    }

    FakeIpcSender unpromoted_sender;
    unpromoted_sender.attempts = {{DispatchResult::ConnectionFailed, false}};
    FakeProcessLauncher unpromoted_launcher;
    FakePublisher unpromoted_publisher;
    unpromoted_publisher.launch_success_transition_succeeds = false;
    LaunchCoordinator unpromoted_success(
        unpromoted_sender, unpromoted_launcher, unpromoted_publisher);
    if (!Check(unpromoted_success.Send(paths) == DispatchResult::PublicationFailed &&
               unpromoted_publisher.location == FakePublisher::Location::Prepared &&
               unpromoted_publisher.request.request_id == unpromoted_sender.requests[0].request_id,
               "failed post-launch promotion reports failure and remains non-executable")) {
        return false;
    }

    FakeIpcSender publish_sender;
    publish_sender.attempts = {{DispatchResult::ConnectionFailed, false}};
    FakeProcessLauncher publish_launcher;
    FakePublisher publish_publisher;
    publish_publisher.result = PublishResult::Failed;
    LaunchCoordinator publication_failure(publish_sender, publish_launcher, publish_publisher);
    if (!Check(publication_failure.Send(paths) == DispatchResult::PublicationFailed &&
               publish_launcher.calls == 0, "publication failure is explicit")) return false;

    FakeIpcSender duplicate_sender;
    duplicate_sender.attempts = {{DispatchResult::Duplicate, true}};
    FakeProcessLauncher duplicate_launcher;
    FakePublisher duplicate_publisher;
    LaunchCoordinator duplicate(duplicate_sender, duplicate_launcher, duplicate_publisher);
    return Check(duplicate.Send(paths) == DispatchResult::Duplicate &&
                 duplicate_publisher.calls == 0 && duplicate_launcher.calls == 0,
                 "matching duplicate stays on warm IPC path");
}

bool TestForcedWriteFailureHandoff() {
    using namespace dlsite::shell;
    static int sequence = 0;
    const std::wstring pipe_name = L"\\\\.\\pipe\\dlsite-write-failure-" +
        std::to_wstring(GetCurrentProcessId()) + L"-" + std::to_wstring(++sequence);
    HANDLE server_pipe = CreateNamedPipeW(
        pipe_name.c_str(),
        PIPE_ACCESS_DUPLEX,
        PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
        1,
        256 * 1024,
        256 * 1024,
        1000,
        nullptr);
    if (!Check(server_pipe != INVALID_HANDLE_VALUE, "write-failure pipe created")) return false;
    HANDLE connected_event = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (!Check(connected_event != nullptr, "write-failure connection event created")) {
        CloseHandle(server_pipe);
        return false;
    }

    std::atomic<bool> server_connected{false};
    std::atomic<bool> received_byte{false};
    std::thread server([&]() {
        const BOOL connected = ConnectNamedPipe(server_pipe, nullptr);
        if (connected || GetLastError() == ERROR_PIPE_CONNECTED) {
            server_connected = true;
            SetEvent(connected_event);
            std::uint8_t byte = 0;
            DWORD read = 0;
            received_byte = ReadFile(server_pipe, &byte, 1, &read, nullptr) && read != 0;
        }
    });

    std::vector<std::uint8_t> attempted_frame;
    int write_calls = 0;
    IpcWriteFile fail_write = [&](HANDLE, LPCVOID buffer, DWORD length, LPDWORD written,
                                  LPOVERLAPPED) {
        ++write_calls;
        const auto* bytes = static_cast<const std::uint8_t*>(buffer);
        attempted_frame.assign(bytes, bytes + length);
        if (written != nullptr) *written = 0;
        WaitForSingleObject(connected_event, 1000);
        SetLastError(ERROR_BROKEN_PIPE);
        return FALSE;
    };
    Win32IpcClient client(pipe_name, 200, 75, std::move(fail_write));
    FakeProcessLauncher launcher;
    FakePublisher publisher;
    LaunchCoordinator coordinator(client, launcher, publisher);
    const std::vector<std::wstring> paths{L"C:\\forced-write-failure\\A", L"D:\\作品\\B"};
    const DispatchResult result = coordinator.Send(paths);
    const bool connection_observed = WaitForSingleObject(connected_event, 1000) == WAIT_OBJECT_0;
    if (!connection_observed) CancelSynchronousIo(server.native_handle());
    server.join();
    CloseHandle(connected_event);
    DisconnectNamedPipe(server_pipe);
    CloseHandle(server_pipe);

    std::vector<std::uint8_t> durable_frame;
    const bool encoded = EncodeBatchRequest(publisher.request, durable_frame);
    return Check(write_calls == 1, "connected write-failure injection ran") &&
        Check(connection_observed && server_connected, "write-failure server accepted the connection") &&
        Check(!received_byte, "failed write delivered no bytes") &&
        Check(result == DispatchResult::Deferred, "failed IPC send was not reported accepted") &&
        Check(publisher.calls == 1 && launcher.calls == 1, "failed IPC send used durable fallback") &&
        Check(publisher.request.paths == paths, "durable fallback preserved the paths") &&
        Check(encoded && attempted_frame == durable_frame, "durable fallback preserved the request ID and payload") &&
        Check(launcher.last_id == publisher.request.request_id, "launched host received the original request ID");
}

bool TestRealDurablePublisher() {
    using namespace dlsite::shell;
    wchar_t temporary_path[32768]{};
    const DWORD length = GetTempPathW(static_cast<DWORD>(std::size(temporary_path)), temporary_path);
    if (!Check(length != 0 && length < std::size(temporary_path), "handoff test temp root")) return false;
    const std::wstring root = std::wstring(temporary_path) + L"dlsite-handoff-" +
        std::to_wstring(GetCurrentProcessId()) + L"-" + std::to_wstring(GetTickCount64());
    if (!Check(CreateDirectoryW(root.c_str(), nullptr) != 0, "handoff test profile created")) return false;
    Win32HandoffPublisher publisher(root);
    BatchRequest request{"native-id", {L"C:\\作品\\A folder", L"D:\\資料\\B"}};
    const bool committed = publisher.Publish(request) == PublishResult::Committed;
    const bool identical = publisher.Publish(request) == PublishResult::Identical;
    request.paths.push_back(L"C:\\different");
    const bool conflict = publisher.Publish(request) == PublishResult::Conflict;
    const std::wstring prepared = root + L"\\handoff\\v1\\prepared\\native-id.json";
    const DWORD prepared_attributes = GetFileAttributesW(prepared.c_str());
    const bool prepared_visible = prepared_attributes != INVALID_FILE_ATTRIBUTES &&
        (prepared_attributes & FILE_ATTRIBUTE_DIRECTORY) == 0;
    HANDLE prepared_file = CreateFileW(prepared.c_str(), GENERIC_READ, FILE_SHARE_READ,
                                       nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    std::vector<std::uint8_t> expected_frame;
    request.paths.pop_back();
    const bool encoded = EncodeBatchRequest(request, expected_frame);
    std::vector<std::uint8_t> on_disk(encoded ? expected_frame.size() - kFrameHeaderSize : 0);
    DWORD read = 0;
    const bool correct_payload = prepared_file != INVALID_HANDLE_VALUE && encoded &&
        ReadFile(prepared_file, on_disk.data(), static_cast<DWORD>(on_disk.size()), &read, nullptr) &&
        read == on_disk.size() &&
        std::equal(on_disk.begin(), on_disk.end(), expected_frame.begin() + kFrameHeaderSize);
    if (prepared_file != INVALID_HANDLE_VALUE) CloseHandle(prepared_file);
    const bool promoted = publisher.MarkLaunchSucceeded("native-id");
    const std::wstring pending = root + L"\\handoff\\v1\\pending\\native-id.json";
    const DWORD attributes = GetFileAttributesW(pending.c_str());
    const bool pending_visible = attributes != INVALID_FILE_ATTRIBUTES &&
        (attributes & FILE_ATTRIBUTE_DIRECTORY) == 0;
    HANDLE pending_file = CreateFileW(pending.c_str(), GENERIC_READ, FILE_SHARE_READ,
                                      nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    std::vector<std::uint8_t> promoted_payload(on_disk.size());
    DWORD promoted_read = 0;
    const bool promoted_payload_same = pending_file != INVALID_HANDLE_VALUE &&
        ReadFile(pending_file, promoted_payload.data(),
                 static_cast<DWORD>(promoted_payload.size()), &promoted_read, nullptr) &&
        promoted_read == promoted_payload.size() && promoted_payload == on_disk;
    if (pending_file != INVALID_HANDLE_VALUE) CloseHandle(pending_file);
    const std::wstring successful_recoverable = root + L"\\handoff\\v1\\recoverable\\native-id.json";
    const bool no_success_failure_marker =
        GetFileAttributesW(successful_recoverable.c_str()) == INVALID_FILE_ATTRIBUTES;
    BatchRequest oversized{"oversized-id", std::vector<std::wstring>(kMaxQuickRenameItems,
        L"C:\\" + std::wstring(kMaxQuickRenamePathLength - 3, L'x'))};
    const bool oversized_rejected = publisher.Publish(oversized) == PublishResult::Failed;

    BatchRequest failed_request{"failed-id", {L"C:\\failed-launch"}};
    const bool failed_published = publisher.Publish(failed_request) == PublishResult::Committed;
    const bool moved = publisher.MarkLaunchFailed("failed-id");
    const std::wstring recoverable = root + L"\\handoff\\v1\\recoverable\\failed-id.json";
    const bool recoverable_exists = GetFileAttributesW(recoverable.c_str()) != INVALID_FILE_ATTRIBUTES;
    DeleteFileW(recoverable.c_str());
    RemoveDirectoryW((root + L"\\handoff\\v1\\recoverable").c_str());

    // A deterministic filesystem seam: block recoverable-directory creation.
    // The failure transition must leave its source in prepared/, never pending/.
    const std::wstring recoverable_directory = root + L"\\handoff\\v1\\recoverable";
    HANDLE blocker = CreateFileW(recoverable_directory.c_str(), GENERIC_WRITE, 0, nullptr,
                                 CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
    const bool blocker_created = blocker != INVALID_HANDLE_VALUE;
    if (blocker_created) CloseHandle(blocker);
    BatchRequest blocked_request{"blocked-id", {L"C:\\blocked-launch"}};
    const bool blocked_published = publisher.Publish(blocked_request) == PublishResult::Committed;
    const bool failed_transition = !publisher.MarkLaunchFailed("blocked-id");
    const std::wstring blocked_prepared = root + L"\\handoff\\v1\\prepared\\blocked-id.json";
    const std::wstring blocked_pending = root + L"\\handoff\\v1\\pending\\blocked-id.json";
    const bool source_preserved = GetFileAttributesW(blocked_prepared.c_str()) != INVALID_FILE_ATTRIBUTES;
    const bool not_pending = GetFileAttributesW(blocked_pending.c_str()) == INVALID_FILE_ATTRIBUTES;

    DeleteFileW(recoverable_directory.c_str());
    DeleteFileW(blocked_prepared.c_str());
    DeleteFileW((root + L"\\handoff\\v1\\prepared\\failed-id.json").c_str());
    DeleteFileW((root + L"\\handoff\\v1\\pending\\native-id.json").c_str());
    RemoveDirectoryW((root + L"\\handoff\\v1\\prepared").c_str());
    RemoveDirectoryW((root + L"\\handoff\\v1\\pending").c_str());
    RemoveDirectoryW((root + L"\\handoff\\v1\\recoverable").c_str());
    RemoveDirectoryW((root + L"\\handoff\\v1").c_str());
    RemoveDirectoryW((root + L"\\handoff").c_str());
    RemoveDirectoryW(root.c_str());
    return Check(committed && identical && conflict && prepared_visible && correct_payload &&
                 promoted && pending_visible && promoted_payload_same && no_success_failure_marker &&
                 GetFileAttributesW(prepared.c_str()) == INVALID_FILE_ATTRIBUTES &&
                 oversized_rejected && failed_published && moved && recoverable_exists &&
                 blocker_created && blocked_published && failed_transition && source_preserved && not_pending,
                 "prepared publication, launch-state transitions, payload preservation, and fail-closed transition failure");
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
    const LaunchResult result = launcher.LaunchSiblingApplication("test123");
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
            char content[64]{};
            DWORD read = 0;
            if (ReadFile(file, content, sizeof(content) - 1, &read, nullptr)) {
                marker_matches = std::string(content, read) ==
                                  "argc=4\nhost=--quick-rename-host\nid=test123\n";
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
    return Check(marker_matches, "sibling launch has only the safe host argv");
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
    FakePublisher publisher;
    LaunchCoordinator coordinator(client, launcher, publisher);
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
                marker_matches = std::string(content, read) ==
                                  "argc=4\nhost=--quick-rename-host\n";
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
        result == DispatchResult::Deferred && marker_matches && publisher.calls == 1,
        "no-primary publishes one batch without argv paths");
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

bool ReadPipeExact(HANDLE pipe, void* destination, DWORD length) {
    auto* bytes = static_cast<std::uint8_t*>(destination);
    DWORD offset = 0;
    while (offset < length) {
        DWORD read = 0;
        if (!ReadFile(pipe, bytes + offset, length - offset, &read, nullptr) || read == 0) return false;
        offset += read;
    }
    return true;
}

enum class ReplyScenario {
    Accepted,
    Duplicate,
    QueueFull,
    ShuttingDown,
    Rejected,
    UnsupportedVersion,
    LostAck,
    Timeout,
    Malformed,
    MismatchedId,
};

bool TestRealIpcReply(ReplyScenario scenario, dlsite::shell::DispatchResult expected) {
    using namespace dlsite::shell;
    static int sequence = 0;
    const std::wstring pipe_name = L"\\\\.\\pipe\\dlsite-handoff-reply-" +
        std::to_wstring(GetCurrentProcessId()) + L"-" + std::to_wstring(++sequence);
    HANDLE pipe = CreateNamedPipeW(pipe_name.c_str(), PIPE_ACCESS_DUPLEX,
        PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT, 1, 256 * 1024, 256 * 1024, 1000, nullptr);
    if (!Check(pipe != INVALID_HANDLE_VALUE, "reply test pipe created")) return false;
    HANDLE release = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (!Check(release != nullptr, "reply test event created")) {
        CloseHandle(pipe);
        return false;
    }
    std::atomic<bool> server_ok{false};
    std::thread server([&]() {
        const BOOL connected = ConnectNamedPipe(pipe, nullptr);
        if (!connected && GetLastError() != ERROR_PIPE_CONNECTED) return;
        std::uint8_t header[4]{};
        if (!ReadPipeExact(pipe, header, 4)) return;
        const DWORD length = (static_cast<DWORD>(header[0]) << 24) |
            (static_cast<DWORD>(header[1]) << 16) |
            (static_cast<DWORD>(header[2]) << 8) | static_cast<DWORD>(header[3]);
        if (length > kMaxRequestFrameSize - 4) return;
        std::vector<std::uint8_t> payload(length);
        if (!ReadPipeExact(pipe, payload.data(), length)) return;
        server_ok = true;
        if (scenario == ReplyScenario::LostAck) return;
        if (scenario == ReplyScenario::Timeout) {
            WaitForSingleObject(release, 1000);
            return;
        }
        const std::string id = scenario == ReplyScenario::MismatchedId ? "wrong-id" : "reply-test";
        std::string status = "ACCEPTED";
        if (scenario == ReplyScenario::Duplicate) status = "DUPLICATE";
        if (scenario == ReplyScenario::QueueFull) status = "QUEUE_FULL";
        if (scenario == ReplyScenario::ShuttingDown) status = "SHUTTING_DOWN";
        if (scenario == ReplyScenario::Rejected) status = "REJECTED";
        if (scenario == ReplyScenario::UnsupportedVersion) status = "UNSUPPORTED_VERSION";
        const std::string reply = scenario == ReplyScenario::Malformed
            ? "{broken-json" : "{\"version\":1,\"request_id\":\"" + id +
                "\",\"status\":\"" + status + "\"}";
        const DWORD reply_length = static_cast<DWORD>(reply.size());
        const std::uint8_t reply_header[4]{
            static_cast<std::uint8_t>(reply_length >> 24),
            static_cast<std::uint8_t>(reply_length >> 16),
            static_cast<std::uint8_t>(reply_length >> 8),
            static_cast<std::uint8_t>(reply_length),
        };
        DWORD written = 0;
        if (!WriteFile(pipe, reply_header, 4, &written, nullptr) || written != 4) return;
        if (!WriteFile(pipe, reply.data(), reply_length, &written, nullptr) ||
            written != reply_length) return;
        FlushFileBuffers(pipe);
    });
    Win32IpcClient client(pipe_name, 200, 75);
    const DispatchResult actual = client.SendToPipe(BatchRequest{"reply-test", {L"C:\\作品"}}, 200);
    SetEvent(release);
    server.join();
    CloseHandle(release);
    CloseHandle(pipe);
    return Check(server_ok && actual == expected, "real IPC reply classification");
}

bool TestRealIpcReplyClassifications() {
    using dlsite::shell::DispatchResult;
    using dlsite::shell::BatchRequest;
    using dlsite::shell::Win32IpcClient;
    const std::wstring missing_pipe = L"\\\\.\\pipe\\dlsite-handoff-absent-" +
        std::to_wstring(GetCurrentProcessId()) + L"-" + std::to_wstring(GetTickCount64());
    Win32IpcClient absent(missing_pipe, 25, 75);
    if (!Check(absent.SendToPipe(BatchRequest{"absent-id", {L"C:\\valid"}}, 25) ==
               DispatchResult::ConnectionFailed, "absent pipe is definitely not sent")) return false;
    return TestRealIpcReply(ReplyScenario::Accepted, DispatchResult::Accepted) &&
        TestRealIpcReply(ReplyScenario::Duplicate, DispatchResult::Duplicate) &&
        TestRealIpcReply(ReplyScenario::QueueFull, DispatchResult::QueueFull) &&
        TestRealIpcReply(ReplyScenario::ShuttingDown, DispatchResult::ShuttingDown) &&
        TestRealIpcReply(ReplyScenario::Rejected, DispatchResult::Rejected) &&
        TestRealIpcReply(ReplyScenario::UnsupportedVersion, DispatchResult::UnsupportedVersion) &&
        TestRealIpcReply(ReplyScenario::LostAck, DispatchResult::AmbiguousFailure) &&
        TestRealIpcReply(ReplyScenario::Timeout, DispatchResult::AmbiguousFailure) &&
        TestRealIpcReply(ReplyScenario::Malformed, DispatchResult::AmbiguousFailure) &&
        TestRealIpcReply(ReplyScenario::MismatchedId, DispatchResult::AmbiguousFailure);
}

bool TestBusyPipeClassification() {
    using namespace dlsite::shell;
    static int sequence = 0;
    const std::wstring pipe_name = L"\\\\.\\pipe\\dlsite-busy-pipe-" +
        std::to_wstring(GetCurrentProcessId()) + L"-" + std::to_wstring(++sequence);
    HANDLE server = CreateNamedPipeW(
        pipe_name.c_str(), PIPE_ACCESS_DUPLEX, PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
        1, 256 * 1024, 256 * 1024, 1000, nullptr);
    if (!Check(server != INVALID_HANDLE_VALUE, "busy-pipe server created")) return false;

    std::atomic<bool> connected{false};
    std::thread listener([&]() {
        const BOOL result = ConnectNamedPipe(server, nullptr);
        connected = result || GetLastError() == ERROR_PIPE_CONNECTED;
    });
    HANDLE occupant = CreateFileW(
        pipe_name.c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr,
        OPEN_EXISTING, 0, nullptr);
    if (occupant == INVALID_HANDLE_VALUE) {
        CancelSynchronousIo(listener.native_handle());
        listener.join();
        CloseHandle(server);
        return Check(false, "busy-pipe occupant connected");
    }
    listener.join();

    Win32IpcClient client(pipe_name, 25, 75);
    const DispatchResult result = client.SendToPipe(
        BatchRequest{"busy-pipe-id", {L"C:\\busy-pipe"}}, 25);
    DWORD available = 0;
    const bool no_request_bytes = PeekNamedPipe(server, nullptr, 0, nullptr, &available, nullptr) &&
        available == 0;
    CloseHandle(occupant);
    DisconnectNamedPipe(server);
    CloseHandle(server);
    return Check(connected && result == DispatchResult::ConnectionFailed && no_request_bytes,
                 "busy pipe is definitely not sent and reports connection failure");
}

bool TestAccessDeniedPipeClassification() {
    using namespace dlsite::shell;
    static int sequence = 0;
    const std::wstring pipe_name = L"\\\\.\\pipe\\dlsite-denied-pipe-" +
        std::to_wstring(GetCurrentProcessId()) + L"-" + std::to_wstring(++sequence);
    PSECURITY_DESCRIPTOR descriptor = nullptr;
    if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(
            L"D:P(D;;GA;;;WD)", SDDL_REVISION_1, &descriptor, nullptr)) {
        return Check(false, "access-denied pipe descriptor created");
    }
    SECURITY_ATTRIBUTES security{};
    security.nLength = sizeof(security);
    security.lpSecurityDescriptor = descriptor;
    HANDLE server = CreateNamedPipeW(
        pipe_name.c_str(), PIPE_ACCESS_DUPLEX, PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
        1, 256 * 1024, 256 * 1024, 1000, &security);
    LocalFree(descriptor);
    if (!Check(server != INVALID_HANDLE_VALUE, "access-denied pipe created")) return false;

    Win32IpcClient client(pipe_name, 25, 75);
    const DispatchResult result = client.SendToPipe(
        BatchRequest{"denied-pipe-id", {L"C:\\denied-pipe"}}, 25);
    CloseHandle(server);
    return Check(result == DispatchResult::ConnectionDenied,
                 "access denied is classified as definitely not sent");
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
        TestDefaultIdentityKnownFolderFallback() &&
        TestSelectionValidation() &&
        TestShellSelectionAttributes() &&
        TestComCommandOneSend() &&
        TestLaunchCoordinator() &&
        TestForcedWriteFailureHandoff() &&
        TestRealDurablePublisher() &&
        TestRealSiblingLauncher() &&
        TestRealNoPrimaryFlow() &&
        TestReplyValidation() &&
        TestRealIpcReplyClassifications() &&
        TestBusyPipeClassification() &&
        TestAccessDeniedPipeClassification();
    CoUninitialize();
    if (passed) {
        std::cout << "native shell helper tests: PASS\n";
        return 0;
    }
    return 1;
}
