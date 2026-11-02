# Builds TradingAgentSetup.exe, a one-file installer for any Windows 10/11 PC (no Python needed).
# Run from the project folder:  powershell -ExecutionPolicy Bypass -File packaging\build.ps1
# Output: build\TradingAgentSetup.exe
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root ".venv\Scripts\python.exe"
$build = Join-Path $root "build"
$dist = Join-Path $build "dist\TradingAgent"
$stage = Join-Path $build "installer"
Set-Location $root

Write-Output "1/4 Bundling the app (a few minutes)..."
& $py -m pip install -q pyinstaller pillow
& $py packaging\make_icon.py | Out-Null
& $py -m PyInstaller --noconfirm --windowed --name TradingAgent `
    --icon "$root\packaging\icon.ico" --paths "$root" `
    --add-data "$root\trader\dashboard\index.html;trader/dashboard" `
    --add-data "$root\trader\dashboard\vendor;trader/dashboard/vendor" `
    --collect-all curl_cffi --collect-submodules yfinance --collect-all bdshare `
    --distpath "$build\dist" --workpath "$build\work" --specpath "$build" `
    --log-level WARN packaging\launcher.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (app)" }

Write-Output "2/4 Adding default settings..."
Copy-Item packaging\config.default.yaml (Join-Path $dist "config.yaml") -Force
Copy-Item .env.example, packaging\icon.ico, packaging\README.txt, packaging\uninstall.ps1 $dist -Force

Write-Output "3/4 Compressing..."
Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
New-Item -ItemType Directory $stage | Out-Null
& $py -c "import shutil, sys; shutil.make_archive(sys.argv[1], 'zip', sys.argv[2])" (Join-Path $stage "TradingAgent") $dist

Write-Output "4/4 Creating the setup file..."
& $py -m PyInstaller --noconfirm --onefile --windowed --name TradingAgentSetup `
    --icon "$root\packaging\icon.ico" `
    --add-data "$stage\TradingAgent.zip;." --add-data "$root\packaging\install.ps1;." `
    --distpath "$build" --workpath "$build\work" --specpath "$build" `
    --log-level WARN packaging\setup_main.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (setup)" }
$setup = Join-Path $build "TradingAgentSetup.exe"
Write-Output ("Done: {0} ({1:N0} MB)" -f $setup, ((Get-Item $setup).Length / 1MB))
