# Removes Trading Agent for the current user (installed by TradingAgentSetup.exe).
Add-Type -AssemblyName System.Windows.Forms
$target = Split-Path -Parent $MyInvocation.MyCommand.Path
$answer = [System.Windows.Forms.MessageBox]::Show(
    "Remove Trading Agent from this computer?`n`nThis also deletes its settings, your saved account keys (.env) and practice-trade history in:`n$target",
    "Uninstall Trading Agent", "YesNo", "Question")
if ($answer -ne "Yes") { exit }

Get-Process TradingAgent -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500
$programs = [Environment]::GetFolderPath("Programs")
foreach ($p in @((Join-Path ([Environment]::GetFolderPath("Desktop")) "Trading Agent.lnk"),
                 (Join-Path $programs "Trading Agent.lnk"),
                 (Join-Path $programs "Uninstall Trading Agent.lnk"))) {
    # Only remove shortcuts that point into this installation.
    if (Test-Path $p) {
        $t = (New-Object -ComObject WScript.Shell).CreateShortcut($p)
        if ("$($t.TargetPath) $($t.Arguments)" -like "*$target*") { Remove-Item $p -Force }
    }
}
Remove-Item -Recurse -Force "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\TradingAgent" -ErrorAction SilentlyContinue
# The folder can't delete itself while this script runs from it; remove it right after we exit.
Start-Process cmd.exe -ArgumentList "/c timeout /t 2 /nobreak >nul & rmdir /s /q `"$target`"" -WindowStyle Hidden
[System.Windows.Forms.MessageBox]::Show("Trading Agent has been removed.", "Trading Agent", "OK", "Information") | Out-Null
