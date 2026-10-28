@echo off
chcp 65001 >nul
title Build PDF Black and White Studio
cd /d "%~dp0"

set "WORK=%SystemDrive%\pdfbw_build"

rem --- Lan dau: tao cap khoa ky so cho ban cap nhat (lam o thu muc goc, truoc khi chuyen sang noi khac) ---
if "%PDFBW_RELOCATED%"=="1" goto :skipkeys
if exist update_pubkey.txt goto :skipkeys
where py >nul 2>&1
if %errorlevel%==0 (set "PY0=py -3") else (set "PY0=python")
%PY0% tools\publish_update.py keygen
if errorlevel 1 goto :error
echo.
echo [!] Da tao khoa ky ban cap nhat trong thu muc keys\. HAY SAO LUU thu muc nay o noi an toan.
echo.
:skipkeys

rem --- Tranh build trong thu muc dong bo (OneDrive/Dropbox/Google Drive): de bi khoa file, loi ngau nhien ---
echo "%CD%" | findstr /i /c:"OneDrive" /c:"Dropbox" /c:"Google Drive" >nul
if not errorlevel 1 if not "%PDFBW_RELOCATED%"=="1" goto :relocate
goto :build

:relocate
echo.
echo [!] Thu muc nay nam trong noi dong bo (OneDrive/Dropbox...), de gay loi khoa file khi build.
echo     Tu dong sao chep sang %WORK% de build o do. Ket qua se nam trong thu muc do.
robocopy "%~dp0." "%WORK%" /E /XD .venv build dist installer_output release keys __pycache__ /NFL /NDL /NJH /NJS /NP >nul
set "PDFBW_RELOCATED=1"
pushd "%WORK%"
call build.bat
popd
exit /b

:build
echo [1/4] Tao moi truong ao Python...
where py >nul 2>&1
if %errorlevel%==0 (set "PY=py -3") else (set "PY=python")
%PY% --version >nul 2>&1
if errorlevel 1 (
    echo [LOI] Khong tim thay Python. Hay cai Python 3.10+ va tick "Add python.exe to PATH".
    goto :error
)
if not exist .venv (
    %PY% -m venv .venv
    if errorlevel 1 goto :error
)
call .venv\Scripts\activate.bat

echo [2/4] Cai thu vien...
python -m pip install --upgrade pip >nul
pip install -r requirements.txt "pyinstaller>=6.10,<7"
if errorlevel 1 goto :error
rem Go cac goi khong dung (pymupdf-layout keo theo mo hinh ONNX gay loi khi dong goi)
pip uninstall -y pymupdf-layout pymupdf4llm onnxruntime >nul 2>&1

echo [3/4] Dong goi ung dung bang PyInstaller...
if not exist icon.ico python make_icon.py
pyinstaller PDF_BW_Studio.spec --noconfirm --clean
if errorlevel 1 goto :error

for /f %%v in ('python -c "from bwapp.version import __version__ as v; print(v)"') do set "APPVER=%%v"
echo Phien ban ma ung dung: %APPVER%

echo [4/4] Tao bo cai Setup.exe bang Inno Setup (neu da cai)...
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" (
    "%ISCC%" /DMyAppVersion=%APPVER% installer.iss
    if errorlevel 1 goto :error
    echo.
    echo === XONG ===
    echo Bo cai : %CD%\installer_output\PDF_BW_Studio_Setup.exe
) else (
    echo.
    echo [!] Chua cai Inno Setup 6 nen chua tao duoc Setup.exe.
    echo     Tai mien phi tai https://jrsoftware.org/isdl.php roi chay lai build.bat
)
echo Ban chay thu: %CD%\dist\PDF_BW_Studio\PDF_BW_Studio.exe
if /i "%PDFBW_RELOCATED%"=="1" start "" explorer "%CD%"
pause
exit /b 0

:error
echo.
echo [LOI] Build that bai. Xem thong bao phia tren.
pause
exit /b 1
