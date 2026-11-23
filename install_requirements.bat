@echo off
chcp 65001 >nul
setlocal enableextensions
cd /d "%~dp0"
title 安装 / 修复运行环境

set "RT=%~dp0runtime"
set "PY=%~dp0runtime\python\python.exe"
set "MIRROR=-i https://pypi.tuna.tsinghua.edu.cn/simple"
set "PYURL=https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"

echo.
echo   检查运行环境（自带 Python + pymupdf / numpy / pillow）
echo.

if exist "%PY%" goto havepython

echo   本机没有 runtime\python，开始自动下载（约 11 MB）...
where curl >nul 2>&1 || goto nocurl
if not exist "%RT%" mkdir "%RT%"
if not exist "%RT%\python" mkdir "%RT%\python"
curl -L --fail --retry 2 -o "%RT%\python-embed.zip" "%PYURL%"
if errorlevel 1 goto dlfail
tar -xf "%RT%\python-embed.zip" -C "%RT%\python"
if errorlevel 1 goto dlfail
copy /y "%~dp0tools\python312._pth" "%RT%\python\python312._pth" >nul
if not exist "%RT%\python\Lib\site-packages" mkdir "%RT%\python\Lib\site-packages"
del /q "%RT%\python-embed.zip" 2>nul
echo   Python 已就位。

:havepython
echo   检查依赖库...
"%PY%" -c "import pymupdf,numpy,PIL" >nul 2>&1
if not errorlevel 1 goto ok

echo   依赖缺失，开始安装（需要联网，等 1~3 分钟）...
"%PY%" -c "import pip" >nul 2>&1
if not errorlevel 1 goto pipok
where curl >nul 2>&1 || goto nocurl
curl -L --fail --retry 2 -o "%RT%\get-pip.py" https://bootstrap.pypa.io/get-pip.py
if errorlevel 1 goto pipfail
"%PY%" "%RT%\get-pip.py" %MIRROR% --no-warn-script-location
if errorlevel 1 goto pipfail
del /q "%RT%\get-pip.py" 2>nul

:pipok
"%PY%" -m pip install --no-warn-script-location %MIRROR% pymupdf numpy pillow
if errorlevel 1 goto pipfail

:ok
"%PY%" -c "import pymupdf,numpy,PIL" >nul 2>&1
if errorlevel 1 goto pipfail
echo.
echo   全部就绪。现在可以双击「启动校对工具.bat」了。
pause
goto :eof

:nocurl
echo.
echo   这台电脑找不到 curl 命令，无法自动下载。
echo   请手动安装 Python 3.12（勾选 Add to PATH），然后重新运行本脚本。
echo.
pause
goto :eof

:dlfail
echo.
echo   下载失败。多半是网络受限（公司代理 / 需要 VPN）。
echo   可以换个网络再试一次。
echo.
pause
goto :eof

:pipfail
echo.
echo   依赖安装失败，请检查上面的报错信息。
echo   常见原因：网络不通、被代理拦截。
echo   也可以换国内镜像：在 install_requirements.bat 里改 MIRROR。
echo.
pause
goto :eof
