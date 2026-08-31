# NPU semantic compressor

This optional local sidecar runs LLMLingua-2 through OpenVINO on Intel NPU and
listens only on `127.0.0.1:8765`. It is used by `agent.semantic_compression`.

Install it in the same Hermes root, so every generated file stays under that
root:

```powershell
cd G:\Hermes\_Agente\npu-compressor
py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe bootstrap.py
.\Start-NpuCompressor.ps1
```

Verify `http://127.0.0.1:8765/health`. The response must contain `"device":
"NPU"`. The `models/`, `cache/`, `logs/`, and `venv/` directories are runtime
artifacts and must not be committed.
