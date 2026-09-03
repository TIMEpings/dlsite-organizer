cmake_minimum_required(VERSION 3.25)

project(dlsite_shell_helper VERSION 0.1.0 LANGUAGES CXX)

if(NOT WIN32)
    message(FATAL_ERROR "dlsite-shell-helper is a Windows-only x64 COM local server")
endif()
if(CMAKE_SIZEOF_VOID_P LESS 8)
    message(FATAL_ERROR "dlsite-shell-helper requires an x64 toolchain")
endif()

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
# The portable package must not depend on a developer-machine MSVC
# redistributable.  Keep the production helper (and test binaries) on the
# supported static MSVC runtime; the Windows system DLLs remain dynamic.
set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")

add_compile_options(/W4 /WX /permissive- /EHsc /utf-8)
add_compile_definitions(UNICODE _UNICODE WIN32_LEAN_AND_MEAN NOMINMAX)

set(SHELL_HELPER_INCLUDE_DIR "${CMAKE_CURRENT_SOURCE_DIR}/src")
set(SHELL_HELPER_SOURCES
    src/com_class_factory.cpp
    src/com_command.cpp
    src/com_server.cpp
    src/identity.cpp
    src/ipc_client.cpp
    src/launch_coordinator.cpp
    src/protocol.cpp
    src/selection_adapter.cpp
    src/utf.cpp
)

add_library(dlsite_shell_helper_core STATIC ${SHELL_HELPER_SOURCES})
target_include_directories(dlsite_shell_helper_core PUBLIC "${SHELL_HELPER_INCLUDE_DIR}")
target_link_libraries(dlsite_shell_helper_core PUBLIC
    advapi32
    bcrypt
    ole32
    shell32
)

add_executable(dlsite-shell-helper src/main.cpp)
target_link_libraries(dlsite-shell-helper PRIVATE dlsite_shell_helper_core)
set_target_properties(dlsite-shell-helper PROPERTIES
    OUTPUT_NAME "dlsite-shell-helper"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
)

add_executable(shell_helper_tests tests/native_tests.cpp)
target_link_libraries(shell_helper_tests PRIVATE dlsite_shell_helper_core)
set_target_properties(shell_helper_tests PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
)

add_executable(shell_helper_interop_client tests/interop_client.cpp)
target_link_libraries(shell_helper_interop_client PRIVATE dlsite_shell_helper_core)
set_target_properties(shell_helper_interop_client PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
)

# Test-only COM client used by the packaging gate.  It activates the packaged
# LocalServer32 class and supplies a real IShellItemArray; it is never copied
# into a production payload.
add_executable(shell_helper_com_probe tests/com_activation_probe.cpp)
target_include_directories(shell_helper_com_probe PRIVATE "${SHELL_HELPER_INCLUDE_DIR}")
target_link_libraries(shell_helper_com_probe PRIVATE ole32 shell32)
set_target_properties(shell_helper_com_probe PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
)

# Test-only sibling process used to prove CreateProcessW receives no selection
# paths on argv.  It is never copied into packaging.
add_executable(test_launch_stub tests/launch_stub.cpp)
set_target_properties(test_launch_stub PROPERTIES
    OUTPUT_NAME "dlsite-organizer"
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
)
add_dependencies(shell_helper_tests test_launch_stub)

include(CTest)
if(BUILD_TESTING)
    add_test(NAME shell_helper_tests COMMAND shell_helper_tests)
endif()

# Keep all development binaries below the repository's ignored build/ tree.
set_target_properties(dlsite_shell_helper_core PROPERTIES
    ARCHIVE_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/lib"
)
