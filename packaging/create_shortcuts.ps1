# Creates "Trading Agent" shortcuts on the Desktop and in the Start menu.
# Usage: create_shortcuts.ps1 -Target <exe> [-Arguments <args>] -WorkDir <folder> -Icon <ico>
param(
    [Parameter(Mandatory)] [string] $Target,
    [string] $Arguments = "",
    [Parameter(Mandatory)] [string] $WorkDir,
    [Parameter(Mandatory)] [string] $Icon
)
$shell = New-Object -ComObject WScript.Shell
$places = @(
    [Environment]::GetFolderPath("Desktop"),
    (Join-Path ([Environment]::GetFolderPath("Programs")) "")
)
foreach ($dir in $places) {
    $lnk = $shell.CreateShortcut((Join-Path $dir "Trading Agent.lnk"))
    $lnk.TargetPath = $Target
    $lnk.Arguments = $Arguments
    $lnk.WorkingDirectory = $WorkDir
    $lnk.IconLocation = "$Icon,0"
    $lnk.Description = "Trading Agent: practice trading with plain-English trade ideas"
    $lnk.Save()
    Write-Output "Created $(Join-Path $dir 'Trading Agent.lnk')"
}
