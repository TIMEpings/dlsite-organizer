#include "helper_runtime.h"

#include <windows.h>
#include <shellapi.h>

int WINAPI wWinMain(
    HINSTANCE /*instance*/,
    HINSTANCE /*previous_instance*/,
    PWSTR /*command_line*/,
    int /*show_command*/) {
    int argc = 0;
    LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (argv == nullptr) {
        return 1;
    }

    const int exit_code = dlsite::shell::RunShellHelper(argc, argv);
    LocalFree(argv);
    return exit_code;
}
