#define UNICODE
#define _UNICODE
#include <windows.h>
#include <wchar.h>

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR args, int show) {
    (void)instance;(void)previous;(void)args;(void)show;
    wchar_t base[32768], python[32768], script[32768], command[65536];
    DWORD count=GetModuleFileNameW(NULL,base,32768);
    if (!count || count>=32768) return 1;
    wchar_t *slash=wcsrchr(base,L'\\');if (!slash) return 1;*slash=0;
    _snwprintf(python,32768,L"%ls\\runtime\\pythonw.exe",base);
    _snwprintf(script,32768,L"%ls\\app\\launch.py",base);
    if (GetFileAttributesW(python)==INVALID_FILE_ATTRIBUTES || GetFileAttributesW(script)==INVALID_FILE_ATTRIBUTES) {
        MessageBoxW(NULL,L"运行文件不完整。请先完整解压 GKM 压缩包，再双击 GKM.exe。无需安装 Python。请勿单独移动 EXE。",L"GKM 启动诊断",MB_OK|MB_ICONERROR);return 2;
    }
    _snwprintf(command,65536,L"\"%ls\" -s -E \"%ls\"",python,script);
    STARTUPINFOW startup={0};startup.cb=sizeof(startup);PROCESS_INFORMATION process={0};
    if (!CreateProcessW(python,command,NULL,NULL,FALSE,CREATE_UNICODE_ENVIRONMENT,NULL,base,&startup,&process)) {
        wchar_t error[512];_snwprintf(error,512,L"无法启动内置运行时（Windows 错误 %lu）。请确认已完整解压、文件未被安全软件隔离，并具有读取权限。",GetLastError());
        MessageBoxW(NULL,error,L"GKM 启动诊断",MB_OK|MB_ICONERROR);return 3;
    }
    CloseHandle(process.hThread);WaitForSingleObject(process.hProcess,INFINITE);
    DWORD exit_code=0;GetExitCodeProcess(process.hProcess,&exit_code);CloseHandle(process.hProcess);
    if (exit_code)MessageBoxW(NULL,L"程序未正常退出。请保留数据目录，检查 Windows 安全软件和文件权限。下次启动可恢复未完成任务。请向技术支持提供数据目录中的启动诊断日志（不含密钥）。",L"GKM 运行诊断",MB_OK|MB_ICONWARNING);
    return (int)exit_code;
}
