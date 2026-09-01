# Start Schneider VFD Workbench locally on port 8001
Set-Location $PSScriptRoot
if (-not (Test-Path .\.venv\Scripts\python.exe)) {
  python -m venv .venv
  .\.venv\Scripts\python.exe -m pip install -r requirements.txt
  .\.venv\Scripts\python.exe -m pip install pillow
}
if (-not $env:LLM_ENABLED) {
  $env:LLM_ENABLED = "true"
}
Write-Host "Open http://127.0.0.1:8001  (LLM_ENABLED=$env:LLM_ENABLED)"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001 --reload
