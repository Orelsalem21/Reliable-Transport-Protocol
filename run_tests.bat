@echo off
REM Start server in a new CMD window
start cmd /k python server.py
REM Wait ~1 second for the server to initialize
timeout /t 1 >nul
REM Start client in a new CMD window
start cmd /k python client.py
```
