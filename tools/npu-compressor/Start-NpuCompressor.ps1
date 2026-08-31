$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root 'venv\Scripts\python.exe'
$stdout = Join-Path $root 'logs\server.out.log'
$stderr = Join-Path $root 'logs\server.err.log'
New-Item -ItemType Directory -Force -Path (Join-Path $root 'logs'), (Join-Path $root 'cache\pip'), (Join-Path $root 'cache\huggingface'), (Join-Path $root 'cache\ov') | Out-Null
if (Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) { exit 0 }
$env:HF_HOME = Join-Path $root 'cache\huggingface'
Start-Process -FilePath $python -ArgumentList 'server.py' -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
