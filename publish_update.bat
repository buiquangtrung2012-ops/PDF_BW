@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>&1 && (set "PY=py -3") || (set "PY=python")
if not exist keys\update_private.key (
    echo [Lan dau] Tao cap khoa ky so cho ban cap nhat...
    %PY% tools\publish_update.py keygen
    echo.
    echo !!! Khoa public vua doi: ban PHAI chay build.bat de dong khoa vao bo cai truoc khi phat hanh ban va dau tien !!!
    pause
    exit /b 0
)
set /p NOTES=Ghi chu phien ban (hien cho nguoi dung): 
%PY% tools\publish_update.py pack --notes "%NOTES%"
echo.
pause
