@echo off
rem Sync G HUB's Roblox path to the newest installed version.
rem Works from any location: resolves its own folder first.
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel%==0 (
    python auto_update.py
) else (
    py -3 auto_update.py
)

echo.
pause
