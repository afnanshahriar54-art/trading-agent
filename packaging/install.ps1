# Trading Agent installer (runs from TradingAgentSetup.exe).
# Installs for the current user only, so no administrator rights are needed.
param(
    [string] $Target = (Join-Path $env:LOCALAPPDATA "Programs\TradingAgent"),
    [switch] $NoShortcuts,   # tests: skip shortcuts and the Apps & features entry
    [switch] $NoLaunch
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

try {
    Get-Process TradingAgent -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep -Milliseconds 500

    # Your settings, keys and history survive reinstalls and updates.
    $keep = @("config.yaml", ".env", "paper_accounts", "data_cache", "reports", "KILL_SWITCH")
    $backup = Join-Path $env:TEMP ("TradingAgentKeep_" + [guid]::NewGuid())
    if (Test-Path $Target) {
        New-Item -ItemType Directory $backup | Out-Null
        foreach ($k in $keep) { if (Test-Path (Join-Path $Target $k)) { Move-Item (Join-Path $Target $k) $backup } }
        Remove-Item -Recurse -Force $Target
    }
    New-Item -ItemType Directory -Force $Target | Out-Null
    Expand-Archive -Path (Join-Path $here "TradingAgent.zip") -DestinationPath $Target -Force
    if (Test-Path $backup) {
        foreach ($item in Get-ChildItem -Force $backup) {
            $dest = Join-Path $Target $item.Name
            if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
            Move-Item $item.FullName $Target
        }
        Remove-Item -Recurse -Force $backup
    }

    $exe = Join-Path $Target "TradingAgent.exe"
    $icon = Join-Path $Target "icon.ico"
    if (-not $NoShortcuts) {
        $shell = New-Object -ComObject WScript.Shell
        $programs = [Environment]::GetFolderPath("Programs")
        foreach ($lnkPath in @((Join-Path ([Environment]::GetFolderPath("Desktop")) "Trading Agent.lnk"),
                               (Join-Path $programs "Trading Agent.lnk"))) {
            $lnk = $shell.CreateShortcut($lnkPath)
            $lnk.TargetPath = $exe; $lnk.WorkingDirectory = $Target; $lnk.IconLocation = "$icon,0"
            $lnk.Description = "Trading Agent: practice trading with plain-English trade ideas"
            $lnk.Save()
        }
        $uninstall = Join-Path $Target "uninstall.ps1"
        $unCmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$uninstall`""
        $lnk = $shell.CreateShortcut((Join-Path $programs "Uninstall Trading Agent.lnk"))
        $lnk.TargetPath = "powershell.exe"
        $lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$uninstall`""
        $lnk.IconLocation = "$icon,0"; $lnk.Save()

        # Show in Settings > Apps so it can be uninstalled the normal way.
        $key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\TradingAgent"
        New-Item -Force $key | Out-Null
        Set-ItemProperty $key DisplayName "Trading Agent"
        Set-ItemProperty $key DisplayIcon $icon
        Set-ItemProperty $key Publisher "Trading Agent"
        Set-ItemProperty $key InstallLocation $Target
        Set-ItemProperty $key UninstallString $unCmd
        Set-ItemProperty $key NoModify 1 -Type DWord
        Set-ItemProperty $key NoRepair 1 -Type DWord
    }

    if (-not $NoLaunch) {
        [System.Windows.Forms.MessageBox]::Show(
            "Trading Agent is installed.`n`nOpen it any time from the 'Trading Agent' icon on your Desktop or in the Start menu.`n`nIt will open now.",
            "Trading Agent", "OK", "Information") | Out-Null
        Start-Process $exe -WorkingDirectory $Target
    }
    Write-Output "Installed to $Target"
}
catch {
    if (-not $NoLaunch) {
        [System.Windows.Forms.MessageBox]::Show("Installation failed:`n`n$($_.Exception.Message)",
            "Trading Agent", "OK", "Error") | Out-Null
    }
    throw
}
