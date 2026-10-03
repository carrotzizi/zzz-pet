@echo off
rem Ensure the desktop pet is running, then set its animation state.
rem Usage: ensure_running.bat [state] [durationMs]
rem   idle | running | running-left | running-right | waving
rem   jumping | failed | review | waiting
rem durationMs is optional: with it the pet plays the state once and then falls
rem back to idle; without it the state is held until the next event.
rem Always exits 0 on purpose: when the pet is not running we stay quiet instead
rem of surfacing as a hook error.
setlocal
set "STATE=%~1"
set "MS=%~2"
if "%STATE%"=="" set "STATE=jumping"
if "%MS%"=="" set "MS=0"

rem 找 pythonw：先看 PATH，再在 LOCALAPPDATA 下扫任意 Python 3.x 安装目录
rem （别把版本号写死，别人的机器上未必是同一个小版本）
set "PYW="
for /f "delims=" %%P in ('where pythonw 2^>nul') do if not defined PYW set "PYW=%%P"
if not defined PYW for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
  if not defined PYW if exist "%%D\pythonw.exe" set "PYW=%%D\pythonw.exe"
)
if not defined PYW set "PYW=pythonw"

rem 已经在跑？那就没什么要启动的
"%SystemRoot%\System32\curl.exe" -s -m 1 -o nul "http://127.0.0.1:7777/health" 2>nul
if not errorlevel 1 goto setstate

rem 没在跑：拉起来，然后等桥开始应答
start "" "%PYW%" "%~dp0..\eous_pet.py"
for /l %%i in (1,1,8) do (
  "%SystemRoot%\System32\curl.exe" -s -m 1 -o nul "http://127.0.0.1:7777/health" 2>nul
  if not errorlevel 1 goto setstate
  "%SystemRoot%\System32\ping.exe" -n 2 127.0.0.1 >nul 2>&1
)

:setstate
"%SystemRoot%\System32\curl.exe" -s -m 2 "http://127.0.0.1:7777/set?state=%STATE%&duration=%MS%" >nul 2>&1
endlocal & exit /b 0
