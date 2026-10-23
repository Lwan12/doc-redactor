/* Launch tesseract-bin.exe with an ASCII tessdata path.

   Tesseract 5.5.3 on Windows feeds TESSDATA_PREFIX to std::filesystem.
   A Chinese absolute path fails with "Illegal byte sequence" before it
   reads a picture. A relative "tessdata" is safe when the working
   directory is this folder. The real binary stays tesseract-bin.exe so
   its own path is not required on the command line.
*/

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>

#define CMD_MAX 32768

static int is_ascii(const wchar_t *text) {
    for (; *text != L'\0'; text++) {
        if (*text > 127) {
            return 0;
        }
    }
    return 1;
}

static wchar_t *skip_argv0(wchar_t *command) {
    if (*command == L'"') {
        command++;
        while (*command != L'\0' && *command != L'"') {
            command++;
        }
        if (*command == L'"') {
            command++;
        }
    } else {
        while (*command != L'\0' && *command != L' ' && *command != L'\t') {
            command++;
        }
    }
    while (*command == L' ' || *command == L'\t') {
        command++;
    }
    return command;
}

int wmain(void) {
    wchar_t self[CMD_MAX];
    wchar_t app[CMD_MAX];
    wchar_t line[CMD_MAX];
    wchar_t prefix[CMD_MAX];
    DWORD length = GetModuleFileNameW(NULL, self, CMD_MAX);
    wchar_t *slash;
    DWORD prefix_len;
    wchar_t *rest;
    STARTUPINFOW startup;
    PROCESS_INFORMATION process;
    DWORD code = 1;

    if (length == 0 || length >= CMD_MAX) {
        fwprintf(stderr, L"tesseract launcher: cannot read its own path\n");
        return 1;
    }
    slash = wcsrchr(self, L'\\');
    if (slash == NULL) {
        fwprintf(stderr, L"tesseract launcher: cannot find its folder\n");
        return 1;
    }
    *slash = L'\0';

    prefix_len = GetEnvironmentVariableW(L"TESSDATA_PREFIX", prefix, CMD_MAX);
    if (prefix_len == 0 || prefix_len >= CMD_MAX || !is_ascii(prefix)) {
        if (!SetEnvironmentVariableW(L"TESSDATA_PREFIX", L"tessdata")) {
            fwprintf(stderr, L"tesseract launcher: cannot set TESSDATA_PREFIX\n");
            return 1;
        }
        if (!SetCurrentDirectoryW(self)) {
            fwprintf(stderr, L"tesseract launcher: cannot use its own folder\n");
            return 1;
        }
    }

    if (_snwprintf(app, CMD_MAX, L"%s\\tesseract-bin.exe", self) < 0) {
        fwprintf(stderr, L"tesseract launcher: path is too long\n");
        return 1;
    }
    rest = skip_argv0(GetCommandLineW());
    if (rest[0] == L'\0') {
        wcscpy_s(line, CMD_MAX, L"tesseract-bin.exe");
    } else if (_snwprintf(line, CMD_MAX, L"tesseract-bin.exe %s", rest) < 0) {
        fwprintf(stderr, L"tesseract launcher: command is too long\n");
        return 1;
    }

    ZeroMemory(&startup, sizeof startup);
    startup.cb = sizeof startup;
    ZeroMemory(&process, sizeof process);
    if (!CreateProcessW(app, line, NULL, NULL, TRUE, 0, NULL, NULL, &startup, &process)) {
        fwprintf(stderr, L"tesseract launcher: cannot start tesseract-bin.exe (%lu)\n", GetLastError());
        return 1;
    }
    WaitForSingleObject(process.hProcess, INFINITE);
    GetExitCodeProcess(process.hProcess, &code);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return (int)code;
}
