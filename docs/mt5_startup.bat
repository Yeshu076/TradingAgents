@echo off
:: ==============================================================================
:: Windows VPS Auto-Boot Resilience Script for MetaTrader 5 & Python Bridge
:: ==============================================================================
:: 
:: INSTRUCTIONS FOR VPS DEPLOYMENT:
:: 1. Press Win + R, type `shell:startup`, and hit Enter.
:: 2. Copy this file into that Startup folder.
:: 3. When the VPS reboots, Windows will automatically run this script upon login.
:: ==============================================================================

echo [System] Starting Trading Firm Autonomous Recovery...
echo [System] Launching MetaTrader 5 Terminal...

:: UPDATE THIS PATH TO YOUR EXACT MT5 INSTALLATION FOLDER
start "" "C:\Program Files\MetaTrader 5\terminal64.exe"

echo [System] Waiting 30 seconds for MT5 to connect to broker servers...
timeout /t 30 /nobreak

echo [System] Booting up Python MT5 Bridge...
:: UPDATE THIS PATH TO YOUR FOREX BRIDGE LOCATION
cd "C:\Users\Yeshw\OneDrive\Documents\GitHub\TradingAgents\forex_mt5_bridge"

:: Ensure you are using the correct Python virtual environment if applicable.
:: e.g.: call venv\Scripts\activate.bat
python main.py

echo [System] Bridge launched. Window will remain open for logs.
pause
