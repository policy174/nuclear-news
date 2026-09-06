@echo off
rem Morning briefing on time. GitHub cron lags 33m-2h42m (measured 2026-09 wk1),
rem while workflow_dispatch starts within seconds. A local scheduled task runs
rem this at 07:45 KST; the send step lands ~15-20min in, so delivery ~08:05.
rem The cron (5 23 * * * = 08:05 KST) stays as fallback for PC-off days:
rem daily_brief reuses today's outbox and skips already-sent briefings
rem (daily_brief.py:847,889), so double-triggering is safe.
rem NOTE: keep this file ASCII-only - cmd.exe parses Korean UTF-8 comments as
rem broken cp949 tokens and executes garbage (observed 2026-09-06).
rem Register:
rem   schtasks /create /tn \nuclens-brief-dispatch /sc DAILY /st 07:45 ^
rem     /tr "cmd /c \"C:\Users\USER\.claude\my-projects\nuclear-news-bot\tools\brief_dispatch.cmd\""
set GH=C:\Program Files\GitHub CLI\gh.exe
set LOG=%USERPROFILE%\.config\kakao-read\brief_dispatch.log
for /L %%i in (1,1,3) do (
  "%GH%" api -X POST repos/policy174/nuclear-news/actions/workflows/daily-brief.yml/dispatches -f ref=main >> "%LOG%" 2>&1 && (
    echo %date% %time% dispatch ok >> "%LOG%"
    exit /b 0
  )
  echo %date% %time% dispatch fail %%i/3 - retry >> "%LOG%"
  timeout /t 20 /nobreak > nul
)
echo %date% %time% dispatch failed 3x - cron fallback >> "%LOG%"
exit /b 1
