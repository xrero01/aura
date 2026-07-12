@echo off
REM Aura one-click launcher (Windows): server + HTTPS tunnel.
cd /d "%~dp0server"

if not "%OPENROUTER_API_KEY%"=="" if "%AURA_PROVIDER%"=="" set AURA_PROVIDER=openrouter
if not "%GEMINI_API_KEY%"=="" if "%AURA_PROVIDER%"=="" set AURA_PROVIDER=gemini
if "%OPENAI_API_KEY%"=="" if "%GEMINI_API_KEY%"=="" if "%OPENROUTER_API_KEY%"=="" (
  echo !! Set an API key first, e.g.:  set OPENROUTER_API_KEY=sk-or-...
  echo    ^(or OPENAI_API_KEY / GEMINI_API_KEY^)
  pause
  exit /b 1
)
echo ^>^>^> AI provider: %AURA_PROVIDER%

pip install -q -r requirements.txt

where ngrok >nul 2>nul
if %errorlevel%==0 (
  start "aura-tunnel" ngrok http 8000
  echo ^>^>^> A ngrok window opened - open its https URL on your phone.
) else (
  echo !! ngrok not found. Install from ngrok.com for phone access.
)

uvicorn main:app --host 0.0.0.0 --port 8000
