@echo off
echo Stopping Apache...
sc stop Apache2.4

echo Stopping Flask...
taskkill /F /IM python.exe

echo All services stopped!