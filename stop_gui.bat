@echo off
REM Kill anything still listening on the app's port. Use this if a server was
REM orphaned (console window closed uncleanly, machine slept mid-run, etc).
powershell -NoProfile -Command ^
  "$c = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue;" ^
  "if (-not $c) { 'Nothing is running on port 5000.'; exit }" ^
  "$c | ForEach-Object { $p = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue;" ^
  "  if ($p) { \"Stopping PID $($p.Id) ($($p.ProcessName))\"; Stop-Process -Id $p.Id -Force } }"
echo.
pause
