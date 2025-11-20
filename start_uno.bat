@echo off
title UNO UNION
color 0A

:menu
cls
echo =========================================
echo        UNO UNION
echo =========================================
echo.
echo  1) Start BROKER (master)
echo  2) Start WORKER (node)
echo  3) Start CLIENT
echo  4) Start FULL LOCAL TEST (Broker + 2 Workers + 2 Clients)
echo  5) Exit
echo.
set /p choice=Choose an option: 

if "%choice%"=="1" goto start_broker
if "%choice%"=="2" goto start_worker
if "%choice%"=="3" goto start_client
if "%choice%"=="4" goto start_full_test
if "%choice%"=="5" exit

goto menu

:start_broker
cls
echo Starting Broker on port 6000...
start cmd /k python broker.py
goto menu

:start_worker
cls
set /p brokerip=Enter Broker IP (default = 127.0.0.1): 
if "%brokerip%"=="" set brokerip=127.0.0.1
set /p port=Worker port (default = 5555): 
if "%port%"=="" set port=5555
echo Starting Worker on port %port% pointing to broker %brokerip%...
start cmd /k python worker_node.py --broker-host %brokerip% --broker-port 6000 --port %port%
goto menu

:start_client
cls
set /p brokerip=Enter Broker IP (default = 127.0.0.1): 
if "%brokerip%"=="" set brokerip=127.0.0.1
echo Starting Client pointing to broker %brokerip%...
start cmd /k python unoclient.py
goto menu

:start_full_test
cls
echo Launching FULL LOCAL TEST setup...
echo Broker + Worker1 + Worker2 + Client1 + Client2
echo.

echo Starting Broker...
start cmd /k python broker.py

timeout /t 1 >nul

echo Starting Worker 1...
start cmd /k python worker_node.py --broker-host 127.0.0.1 --broker-port 6000 --port 5555

timeout /t 1 >nul

echo Starting Worker 2...
start cmd /k python worker_node.py --broker-host 127.0.0.1 --broker-port 6000 --port 5556

timeout /t 1 >nul

echo Starting Client 1...
start cmd /k python unoclient.py

timeout /t 1 >nul

echo Starting Client 2...
start cmd /k python unoclient.py


goto menu
