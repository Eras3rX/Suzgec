@echo off
title SiftGuard - demo link
cd /d "%~dp0"
echo.
echo  ==============================================
echo   SiftGuard: starting the site and the link
echo   Do NOT close the two small windows that open.
echo  ==============================================
echo.

rem 1. start the site if it is not running yet
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri http://127.0.0.1:8000/api/config -UseBasicParsing -TimeoutSec 3 | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
  echo  Starting the site, please wait 15 seconds...
  start "SiftGuard server" /min ".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips=*
  timeout /t 15 /nobreak >nul
) else (
  echo  The site is already running.
)

rem 2. open the public link (the address appears in a few seconds)
del tunnel.log 2>nul
echo  Creating the public link...
start "SiftGuard tunnel" /min cmd /c "ssh -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=30 -R 80:127.0.0.1:8000 nokey@localhost.run > tunnel.log 2>&1"

set /a tries=0
:wait
set /a tries+=1
if %tries% gtr 30 goto failed
timeout /t 2 /nobreak >nul
powershell -NoProfile -Command "$m = Select-String -Path 'tunnel.log' -Pattern 'https://[a-z0-9-]+\.lhr\.life' -ErrorAction SilentlyContinue | Select-Object -First 1; if ($m) { $m.Matches[0].Value | Set-Content -NoNewline 'demo-link.txt'; exit 0 } else { exit 1 }"
if errorlevel 1 goto wait

set /p LINK=<demo-link.txt
echo.
echo  ==============================================
echo   YOUR LINK (already copied, just paste it):
echo.
echo   %LINK%
echo  ==============================================
echo.
echo  Admin panel: add /admin to the link, the password is
echo  ADMIN_TOKEN from the file .env
echo.
echo  Keep this PC awake and the two small windows open
echo  while the jury looks at the site.
echo.
echo %LINK%| clip
start "" "%LINK%"
pause
exit /b 0

:failed
echo.
echo  The link did not appear. Check the internet and run this file again.
pause
exit /b 1
