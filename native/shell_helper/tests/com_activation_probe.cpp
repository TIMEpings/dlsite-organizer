#include "constants.h"

#include <objbase.h>
#include <shlobj.h>
#include <shobjidl_core.h>
#include <windows.h>

#include <iostream>
#include <string>
#include <utility>
#include <vector>

namespace {

bool ReadPathArgument(int& index, int argc, wchar_t** argv, std::wstring& path) {
    if (_wcsicmp(argv[index], L"--path") != 0 || index + 1 >= argc) {
        return false;
    }
    path = argv[++index];
    return !path.empty();
}

void FreePidls(std::vector<PIDLIST_ABSOLUTE>& pidls) {
    for (PIDLIST_ABSOLUTE pidl : pidls) {
        CoTaskMemFree(pidl);
    }
    pidls.clear();
}

int Probe(const std::vector<std::wstring>& paths) {
    GUID clsid{};
    if (FAILED(CLSIDFromString(dlsite::shell::kClassIdString, &clsid))) {
        return 2;
    }

    IExecuteCommand* execute = nullptr;
    HRESULT result = CoCreateInstance(
        clsid,
        nullptr,
        CLSCTX_LOCAL_SERVER,
        IID_PPV_ARGS(&execute));
    if (FAILED(result)) {
        std::wcerr << L"CoCreateInstance failed: 0x" << std::hex
                   << static_cast<unsigned long>(result) << L"\n";
        return 3;
    }

    IObjectWithSelection* with_selection = nullptr;
    result = execute->QueryInterface(IID_PPV_ARGS(&with_selection));
    if (FAILED(result)) {
        execute->Release();
        return 4;
    }

    std::vector<PIDLIST_ABSOLUTE> pidls;
    std::vector<LPCITEMIDLIST> item_lists;
    for (const std::wstring& path : paths) {
        PIDLIST_ABSOLUTE pidl = nullptr;
        SFGAOF attributes = SFGAO_FILESYSTEM | SFGAO_FOLDER;
        result = SHParseDisplayName(path.c_str(), nullptr, &pidl, attributes, &attributes);
        if (FAILED(result)) {
            FreePidls(pidls);
            with_selection->Release();
            execute->Release();
            return 5;
        }
        pidls.push_back(pidl);
        item_lists.push_back(pidl);
    }

    IShellItemArray* selection = nullptr;
    result = SHCreateShellItemArrayFromIDLists(
        static_cast<UINT>(pidls.size()),
        item_lists.data(),
        &selection);
    if (SUCCEEDED(result)) {
        result = with_selection->SetSelection(selection);
    }
    if (selection != nullptr) {
        selection->Release();
    }
    FreePidls(pidls);
    if (FAILED(result)) {
        with_selection->Release();
        execute->Release();
        return 6;
    }

    result = execute->Execute();
    std::wcout << L"COM activation and Execute HRESULT: 0x" << std::hex
               << static_cast<unsigned long>(result) << L"\n";
    with_selection->Release();
    execute->Release();
    return SUCCEEDED(result) ? 0 : 7;
}

}  // namespace

int wmain(int argc, wchar_t** argv) {
    std::vector<std::wstring> paths;
    for (int index = 1; index < argc; ++index) {
        std::wstring path;
        if (!ReadPathArgument(index, argc, argv, path)) {
            return 2;
        }
        paths.push_back(std::move(path));
    }
    if (paths.empty() || paths.size() > dlsite::shell::kMaxQuickRenameItems) {
        return 2;
    }

    const HRESULT initialize_result = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    if (FAILED(initialize_result)) {
        return 2;
    }
    const int exit_code = Probe(paths);
    CoUninitialize();
    return exit_code;
}
