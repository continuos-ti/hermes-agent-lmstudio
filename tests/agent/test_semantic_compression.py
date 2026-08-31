from types import SimpleNamespace

from agent import semantic_compression


def _agent():
    return SimpleNamespace(provider="lmstudio")


def _config():
    return {"min_input_chars": 10, "max_tool_results_per_call": 2}


def test_structured_user_content_is_never_sent_to_sidecar(monkeypatch):
    monkeypatch.setattr(semantic_compression, "_compress", lambda *_: (_ for _ in ()).throw(AssertionError("must not call")))

    assert semantic_compression.maybe_compress_user_message("```python\nprint('x')\n```" * 100, _agent(), _config()) is None
    assert semantic_compression.maybe_compress_user_message("G:\\Hermes\\file.txt " * 100, _agent(), _config()) is None


def test_plain_user_prose_uses_smaller_sidecar_result(monkeypatch):
    monkeypatch.setattr(semantic_compression, "_compress", lambda text, config: "resumo")

    assert semantic_compression.maybe_compress_user_message("texto corrido " * 100, _agent(), _config()) == "resumo"


def test_tool_compression_preserves_original_message_list(monkeypatch):
    monkeypatch.setattr(semantic_compression, "_compress", lambda text, config: "resultado curto")
    messages = [
        {"role": "system", "content": "sistema"},
        {"role": "tool", "content": "resultado textual " * 100},
        {"role": "tool", "content": "https://example.com " * 100},
    ]

    compressed = semantic_compression.compress_tool_messages(messages, _agent(), _config())

    assert compressed[1]["content"] == "resultado curto"
    assert compressed[2]["content"] == messages[2]["content"]
    assert messages[1]["content"] != "resultado curto"


def test_sidecar_result_without_token_improvement_falls_open(monkeypatch):
    monkeypatch.setattr(semantic_compression, "urlopen", lambda *_args, **_kwargs: _Response({"compressed_text": "x", "origin_tokens": 20, "compressed_tokens": 20}))

    assert semantic_compression.maybe_compress_user_message("texto corrido " * 100, _agent(), _config()) is None


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        import json

        return json.dumps(self.payload).encode("utf-8")
