@echo off
chcp 65001 >nul
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 goto sans_uv
uv sync --no-dev
if errorlevel 1 goto erreur
uv run --no-dev playwright install chromium
if errorlevel 1 goto erreur
if not exist config\accounts.yaml copy config\accounts.example.yaml config\accounts.yaml >nul
echo.
echo Installation terminée. Renseignez config\accounts.yaml, puis lancez "Lancer cfeauto.bat".
pause
exit /b 0
:sans_uv
echo uv n'est pas installé. Dans un terminal, lancez : winget install --id=astral-sh.uv -e
echo Fermez ensuite ce terminal, puis relancez cette installation.
pause
exit /b 1
:erreur
echo.
echo L'installation a échoué : voir le message ci-dessus.
pause
exit /b 1
