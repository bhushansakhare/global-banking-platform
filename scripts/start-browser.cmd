@echo off
REM Visible, user-controlled browser sessions use the installed Microsoft Edge on Windows.
set BROWSER_AUTOMATION_ENABLED=true
set BROWSER_CHANNEL=msedge
call "%~dp0start.cmd"
