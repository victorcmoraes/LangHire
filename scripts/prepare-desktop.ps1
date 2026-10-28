# Release sidecar files left locked after a previous desktop exited.
# Never stop another checkout, a CLI worker, Chrome, or a live desktop.
$ErrorActionPreference = 'Stop'
$ProjectDir = Split-Path -Parent $PSScriptRoot
$SidecarPaths = @('debug', 'release') | ForEach-Object {
    [IO.Path]::GetFullPath((Join-Path $ProjectDir "src-tauri\target\$_\langhire-backend.exe"))
}
$Processes = @(Get-CimInstance Win32_Process)
$Sidecars = @($Processes | Where-Object { $_.ExecutablePath -and $SidecarPaths -contains $_.ExecutablePath })
$SidecarIds = @($Sidecars | ForEach-Object { $_.ProcessId })
foreach ($Sidecar in $Sidecars) {
    if ($SidecarIds -contains $Sidecar.ParentProcessId) { continue }
    $Parent = $Processes | Where-Object { $_.ProcessId -eq $Sidecar.ParentProcessId }
    if ($Parent -and $Parent.CreationDate -le $Sidecar.CreationDate) {
        throw 'LangHire is still open. Close its desktop window before rebuilding; no process was stopped.'
    }
}
# Children first: PyInstaller has a bootloader and a separate backend process.
foreach ($Sidecar in ($Sidecars | Sort-Object CreationDate -Descending)) {
    $Current = Get-CimInstance Win32_Process -Filter "ProcessId=$($Sidecar.ProcessId)"
    if ($Current -and $Current.ExecutablePath -eq $Sidecar.ExecutablePath -and $Current.CreationDate -eq $Sidecar.CreationDate) {
        Write-Host "Closing orphaned desktop backend (PID $($Current.ProcessId))..."
        Stop-Process -Id $Current.ProcessId -Force -ErrorAction SilentlyContinue
    }
}
foreach ($Sidecar in $Sidecars) {
    $Remaining = Get-Process -Id $Sidecar.ProcessId -ErrorAction SilentlyContinue
    if ($Remaining) { $Remaining.WaitForExit(5000) | Out-Null }
}
