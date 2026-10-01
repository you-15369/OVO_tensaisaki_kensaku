@echo off
rem Start backend (FastAPI) and frontend (Next.js), then open the browser.
rem Close the two windows to stop.

start "tensai backend" /d "%~dp0backend" cmd /k python -m uvicorn main:app --port 8000
start "tensai frontend" /d "%~dp0frontend" cmd /k npm run dev

echo Waiting for http://localhost:3000 ...
set /a tries=0
:wait
timeout /t 2 /nobreak >nul
curl -s -o nul http://localhost:3000
if not errorlevel 1 goto open
set /a tries+=1
if %tries% lss 60 goto wait
echo Frontend did not start. Check the "tensai frontend" window.
pause
exit /b 1

:open
start "" http://localhost:3000
