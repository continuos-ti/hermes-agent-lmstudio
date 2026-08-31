"""Local LLMLingua-2 service backed by an Intel OpenVINO NPU."""

from __future__ import annotations

import json
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import openvino as ov
import torch
from llmlingua import PromptCompressor
from transformers import AutoTokenizer

ROOT = Path(__file__).resolve().parent
MODEL_DIR = ROOT / "models" / "llmlingua2-ov"
NPU_MODEL = ROOT / "models" / "llmlingua2-ov-npu-fp16" / "model.xml"
SEQUENCE_LENGTH = 128
MAX_INPUT_CHARS = 200_000


class NpuTokenClassifier:
    def __init__(self):
        self._compiled = ov.Core().compile_model(NPU_MODEL, "NPU", {"CACHE_DIR": str(ROOT / "cache" / "ov")})
        self.device = str(self._compiled.get_property("EXECUTION_DEVICES"))
        self._lock = threading.Lock()

    def __call__(self, *, input_ids, attention_mask):
        ids = input_ids.detach().cpu().numpy()
        mask = attention_mask.detach().cpu().numpy().astype("int64")
        with self._lock:
            outputs = self._compiled({"input_ids": ids, "attention_mask": mask, "token_type_ids": ids * 0})
        return SimpleNamespace(loss=None, logits=torch.from_numpy(next(iter(outputs.values()))))


class NpuPromptCompressor(PromptCompressor):
    def load_model(self, model_name, device_map="NPU", model_config=None):
        self.device = "cpu"
        self.model_name = model_name
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL_DIR)
        self.tokenizer.model_max_length = 1_000_000
        self.model = NpuTokenClassifier()
        self.max_position_embeddings = SEQUENCE_LENGTH

    def init_llmlingua2(self, max_batch_size=1, max_force_token=0):
        self.max_batch_size = 1
        self.max_seq_len = SEQUENCE_LENGTH
        self.max_force_token = 0
        self.special_tokens = set(self.tokenizer.special_tokens_map.values())
        self.added_tokens = []


COMPRESSOR = NpuPromptCompressor("microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank", use_llmlingua2=True, llmlingua2_config={"max_batch_size": 1, "max_force_token": 0})
COMPRESSION_LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send(HTTPStatus.OK, {"status": "ok", "device": COMPRESSOR.model.device}) if self.path == "/health" else self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self):
        if self.path != "/compress":
            self._send(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length).decode("utf-8"))
            text, rate = request.get("text"), float(request.get("rate", 0.55))
            if not isinstance(text, str) or not text.strip() or len(text) > MAX_INPUT_CHARS:
                raise ValueError("text must be a non-empty string within the input limit")
            if not 0.1 <= rate <= 0.9:
                raise ValueError("rate must be between 0.1 and 0.9")
            with COMPRESSION_LOCK:
                result = COMPRESSOR.compress_prompt(text, rate=rate, force_tokens=[])
            self._send(HTTPStatus.OK, {"compressed_text": result["compressed_prompt"], "origin_tokens": result["origin_tokens"], "compressed_tokens": result["compressed_tokens"], "device": COMPRESSOR.model.device})
        except Exception as exc:
            self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    def log_message(self, _format, *_args):
        return


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8765), Handler).serve_forever()
