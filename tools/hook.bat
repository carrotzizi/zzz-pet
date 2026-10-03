@echo off
rem Forward an animation state to the Eous desktop pet.
rem Usage: hook.bat <state> [durationMs]
rem   idle | running | running-left | running-right | waving
rem   jumping | failed | review | waiting
rem durationMs is optional: with it the pet plays the state once and then
rem falls back to idle; without it the state is held until the next event.
rem Always exits 0 on purpose: when the pet is not running we stay quiet
rem instead of surfacing as a hook error inside ZCode.
setlocal
set "STATE=%~1"
set "MS=%~2"
if "%STATE%"=="" set "STATE=idle"
if "%MS%"=="" set "MS=0"
"%SystemRoot%\System32\curl.exe" -s -m 2 "http://127.0.0.1:7777/set?state=%STATE%&duration=%MS%" >nul 2>&1
endlocal & exit /b 0
