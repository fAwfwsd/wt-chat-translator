@echo off
setlocal
set "HERE=%~dp0"
set "PYW="

rem 1) DSH 自带的 Python（本机现在只有这个）
if exist "%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\pythonw.exe" (
    set "PYW=%USERPROFILE%\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\pythonw.exe"
    goto run
)

rem 2) 系统 PATH 里的 pythonw
where pythonw >nul 2>nul && (
    set "PYW=pythonw"
    goto run
)

rem 3) py 启动器
where py >nul 2>nul && (
    start "" py -3 "%HERE%main.py" %*
    exit /b
)

echo Python not found. Install Python 3.9+ and try again.
pause
exit /b

:run
start "" "%PYW%" "%HERE%main.py" %*
exit /b
