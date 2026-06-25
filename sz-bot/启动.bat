@echo off
chcp 65001 >nul
cd /d "%~dp0"
title SZ-Bot

echo ============================================
echo   SZ-Bot - AI Chat Bot for Preternatural
echo ============================================
echo.
echo [1] DeepSeek (free, fast)
echo [2] Claude  (better, paid API key)
echo [3] Custom
echo [0] Exit
echo.
set /p choice=Select [0-3]: 

if "%choice%"=="0" exit
if "%choice%"=="1" goto deepseek
if "%choice%"=="2" goto claude
if "%choice%"=="3" goto custom
echo Invalid, press any key...
pause >nul
exit

:deepseek
echo.
echo Starting DeepSeek mode (8s interval)...
echo Press Ctrl+C to stop
echo.
python ai_bot.py --model deepseek --interval 8
goto end

:claude
echo.
echo Starting Claude mode (8s interval)...
echo Press Ctrl+C to stop
echo.
python ai_bot.py --model claude --interval 8
goto end

:custom
echo.
set /p model=Model (deepseek/claude): 
set /p interval=Interval seconds (default 8): 
if "%model%"=="" set model=deepseek
if "%interval%"=="" set interval=8
echo.
echo Starting: --model %model% --interval %interval%
echo Press Ctrl+C to stop
echo.
python ai_bot.py --model %model% --interval %interval%
goto end

:end
echo.
echo Bot stopped.
pause
