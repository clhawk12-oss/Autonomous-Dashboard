# setup_scheduler.ps1
# -------------------
# One-time setup to register Windows Task Scheduler tasks for both agents.
# Run as Administrator in PowerShell:
#   Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
#   .\setup_scheduler.ps1
#
# Tasks created:
#   AutonomousPortfolio\SwingAgent      — once daily at 3:30 PM ET, Mon–Fri
#   AutonomousPortfolio\LongTermAgent   — once daily at 4:15 PM ET, Mon–Fri

$ErrorActionPreference = "Stop"

# ── Detect Python executable ───────────────────────────────────────────────────
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) {
    Write-Error "Python not found in PATH. Install Python 3.11+ and try again."
    exit 1
}
Write-Host "Using Python: $python"

# ── Project directory ──────────────────────────────────────────────────────────
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$mainPy    = Join-Path $scriptDir "main.py"

if (-not (Test-Path $mainPy)) {
    Write-Error "main.py not found at: $mainPy"
    exit 1
}

# ── Create task folder ─────────────────────────────────────────────────────────
$taskFolder = "AutonomousPortfolio"
$scheduler  = New-Object -ComObject "Schedule.Service"
$scheduler.Connect()
$root = $scheduler.GetFolder("\")

try {
    $root.GetFolder($taskFolder) | Out-Null
    Write-Host "Task folder '\$taskFolder' already exists."
} catch {
    $root.CreateFolder($taskFolder) | Out-Null
    Write-Host "Created task folder '\$taskFolder'."
}

# ── Helper: delete existing task if present ────────────────────────────────────
function Remove-TaskIfExists($name) {
    $fullName = "\$taskFolder\$name"
    schtasks /Delete /TN $fullName /F 2>$null | Out-Null
}

# ── Task 1: Swing Agent (once daily at 3:30 PM ET) ────────────────────────────
Remove-TaskIfExists "SwingAgent"

schtasks /Create `
    /TN "\$taskFolder\SwingAgent" `
    /TR "`"$python`" `"$mainPy`" --agent swing" `
    /SC DAILY `
    /ST 15:30 `
    /D MON,TUE,WED,THU,FRI `
    /SD (Get-Date -Format "MM/dd/yyyy") `
    /RL HIGHEST `
    /F

if ($LASTEXITCODE -eq 0) {
    Write-Host "✓ SwingAgent task created (daily at 3:30 PM ET, Mon–Fri)"
} else {
    Write-Warning "SwingAgent task creation returned exit code $LASTEXITCODE"
}

# ── Task 2: Long-Term Agent (once daily at 4:15 PM) ───────────────────────────
Remove-TaskIfExists "LongTermAgent"

schtasks /Create `
    /TN "\$taskFolder\LongTermAgent" `
    /TR "`"$python`" `"$mainPy`" --agent long_term" `
    /SC DAILY `
    /ST 16:15 `
    /D MON,TUE,WED,THU,FRI `
    /SD (Get-Date -Format "MM/dd/yyyy") `
    /RL HIGHEST `
    /F

if ($LASTEXITCODE -eq 0) {
    Write-Host "✓ LongTermAgent task created (daily at 4:15 PM ET, Mon–Fri)"
} else {
    Write-Warning "LongTermAgent task creation returned exit code $LASTEXITCODE"
}

# ── Summary ────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "Setup complete. Tasks registered under '\$taskFolder\':"
schtasks /Query /TN "\$taskFolder\SwingAgent"    /FO LIST 2>$null | Select-String "Task Name|Status|Next Run"
schtasks /Query /TN "\$taskFolder\LongTermAgent" /FO LIST 2>$null | Select-String "Task Name|Status|Next Run"
Write-Host ""
Write-Host "To test immediately (bypasses schedule/market-hours guard):"
Write-Host "  python `"$mainPy`" --agent both --force"
Write-Host ""
Write-Host "To remove tasks later:"
Write-Host "  schtasks /Delete /TN `"\$taskFolder\SwingAgent`" /F"
Write-Host "  schtasks /Delete /TN `"\$taskFolder\LongTermAgent`" /F"
