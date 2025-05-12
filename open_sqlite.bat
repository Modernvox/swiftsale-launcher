@echo off
:menu
cls
ECHO SwiftSale Subscription Database Manager
ECHO.
ECHO 1. View All Subscriptions
ECHO 2. Add New Subscription
ECHO 3. Delete Subscription
ECHO 4. Backup Database
ECHO 5. Optimize Database
ECHO 6. Exit
ECHO.
SET /P choice=Choose an option (1-6): 

if %choice%==1 goto view
if %choice%==2 goto add
if %choice%==3 goto delete
if %choice%==4 goto backup
if %choice%==5 goto optimize
if %choice%==6 goto end

:view
"C:\Users\lovei\SCD_SALES\sqlite-tools-win-x64-3490200\sqlite3.exe" "C:\Users\lovei\SCD_SALES\subscriptions.db" "SELECT * FROM subscriptions;"
pause
goto menu

:add
set /p email=Enter subscriber email: 
set /p tier=Enter subscription tier (Trial, Bronze, Silver, Gold): 
"C:\Users\lovei\SCD_SALES\sqlite-tools-win-x64-3490200\sqlite3.exe" "C:\Users\lovei\SCD_SALES\subscriptions.db" "INSERT INTO subscriptions (email, tier) VALUES ('%email%', '%tier%');"
pause
goto menu

:delete
set /p email=Enter subscriber email to delete: 
"C:\Users\lovei\SCD_SALES\sqlite-tools-win-x64-3490200\sqlite3.exe" "C:\Users\lovei\SCD_SALES\subscriptions.db" "DELETE FROM subscriptions WHERE email='%email%';"
pause
goto menu

:backup
copy "C:\Users\lovei\SCD_SALES\subscriptions.db" "C:\Users\lovei\SCD_SALES\subscriptions_backup_%date:~10,4%-%date:~4,2%-%date:~7,2%.db"
echo Backup created successfully.
pause
goto menu

:optimize
"C:\Users\lovei\SCD_SALES\sqlite-tools-win-x64-3490200\sqlite3.exe" "C:\Users\lovei\SCD_SALES\subscriptions.db" "VACUUM;"
echo Database optimized.
pause
goto menu

:end
exit