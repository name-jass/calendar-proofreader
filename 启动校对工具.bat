@echo off
chcp 65001 >nul
setlocal enableextensions
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
title 台历日期校对

rem ---- 找一个能用的 Python：优先用自带的运行环境，不依赖电脑上装没装 Python ----
set "PYRUN="
call :try_exe "%~dp0runtime\python\python.exe"
call :try_cmd "py -3"
call :try_cmd "python"
call :try_exe "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
call :try_exe "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
call :try_exe "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
call :try_exe "%ProgramFiles%\Python313\python.exe"
call :try_exe "%ProgramFiles%\Python312\python.exe"
call :try_exe "%ProgramFiles%\Python311\python.exe"
call :try_exe "%ProgramFiles(x86)%\Python312\python.exe"
call :try_exe "%ProgramFiles(x86)%\Python311\python.exe"
if not defined PYRUN goto nodeps
goto run

:try_exe
if defined PYRUN goto :eof
if not exist "%~1" goto :eof
"%~1" -c "import pymupdf,numpy,PIL" >nul 2>&1
if errorlevel 1 goto :eof
set PYRUN="%~1"
goto :eof

:try_cmd
if defined PYRUN goto :eof
%~1 -c "import pymupdf,numpy,PIL" >nul 2>&1
if errorlevel 1 goto :eof
set PYRUN=%~1
goto :eof

:run
echo.
echo   正在启动，稍后会弹出浏览器页面（这个黑窗口先别关）
echo.
%PYRUN% app.py
if errorlevel 1 (
    echo.
    echo   程序运行时报错，错误信息见上方。请把这个窗口截图发给维护的人。
    pause
)
goto :eof

:nodeps
echo.
echo   ==========================================================
echo    启动失败：没有找到能用的 Python 运行环境
echo   ==========================================================
echo.
echo    这个工具需要电脑上有 Python 3.11+ 和 pymupdf / numpy / pillow。
    echo    两种做法：
    echo.
    echo    1. 双击 install_requirements.bat（需要联网，约 100MB，只用一次）
    echo       它会自动下载 Python 并把依赖装好。
    echo.
    echo    2. 自己装好 Python 后，双击 install_requirements.bat 只补依赖库。
    echo.
pause
