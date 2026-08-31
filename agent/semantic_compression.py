"""Optional request-only semantic compression through a local LLMLingua service."""

from __future__ import annotations

import copy
import json
import logging
import re
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

DEFAULTS = {
    "enabled": True, "providers": ["lmstudio"], "endpoint": "http://127.0.0.1:8765",
    "min_input_chars": 6000, "rate": 0.55, "timeout_seconds": 30,
    "compress_user_prose": True, "compress_tool_prose": True, "max_tool_results_per_call": 2,
}

_STRUCTURED_PATTERNS = (
    re.compile(r"```"), re.compile(r"https?://|\bwww\.", re.IGNORECASE),
    re.compile(r"(?:[A-Za-z]:\\|/(?:[\w.-]+/)+)"), re.compile(r"^\s*[\[{]", re.MULTILINE),
    re.compile(r"^\s*(?:curl|wget|python|powershell|cmd|npm|pip|git)\b", re.IGNORECASE | re.MULTILINE),
)


def resolve_config(raw: Any) -> dict[str, Any]:
    config = dict(DEFAULTS)
    if isinstance(raw, dict):
        config.update({key: value for key, value in raw.items() if value is not None})

    def integer(key: str, minimum: int, maximum: int) -> int:
        try:
            return max(minimum, min(maximum, int(config[key])))
        except (KeyError, TypeError, ValueError):
            return int(DEFAULTS[key])

    def flag(key: str) -> bool:
        value = config.get(key)
        return value if isinstance(value, bool) else str(value).strip().lower() not in {"", "0", "false", "no", "off"}

    providers = config.get("providers")
    config["enabled"] = flag("enabled")
    config["compress_user_prose"] = flag("compress_user_prose")
    config["compress_tool_prose"] = flag("compress_tool_prose")
    config["providers"] = [str(item).strip().lower() for item in providers] if isinstance(providers, list) else ["lmstudio"]
    config["min_input_chars"] = integer("min_input_chars", 1000, 200000)
    config["timeout_seconds"] = integer("timeout_seconds", 1, 120)
    config["max_tool_results_per_call"] = integer("max_tool_results_per_call", 0, 8)
    try:
        config["rate"] = max(0.1, min(0.9, float(config["rate"])))
    except (KeyError, TypeError, ValueError):
        config["rate"] = DEFAULTS["rate"]
    config["endpoint"] = str(config.get("endpoint") or DEFAULTS["endpoint"]).rstrip("/")
    return config


def _eligible(text: Any, config: dict[str, Any], agent: Any) -> bool:
    if not config["enabled"] or not isinstance(text, str) or len(text) < config["min_input_chars"]:
        return False
    provider = str(getattr(agent, "provider", "") or "").strip().lower()
    return provider in config["providers"] and not any(pattern.search(text) for pattern in _STRUCTURED_PATTERNS)


def _compress(text: str, config: dict[str, Any]) -> str | None:
    request = Request(
        f"{config['endpoint']}/compress",
        data=json.dumps({"text": text, "rate": config["rate"]}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urlopen(request, timeout=config["timeout_seconds"]) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, ValueError) as exc:
        logger.info("Semantic compression unavailable: %s", exc)
        return None
    compressed = data.get("compressed_text")
    try:
        improved = int(data["compressed_tokens"]) < int(data["origin_tokens"])
    except (KeyError, TypeError, ValueError):
        improved = False
    if not improved or not isinstance(compressed, str) or not compressed.strip():
        return None
    logger.info("Semantic compression applied: device=%s tokens=%s->%s", data.get("device", "unknown"), data.get("origin_tokens"), data.get("compressed_tokens"))
    return compressed.strip()


def maybe_compress_user_message(message: Any, agent: Any, raw_config: Any = None) -> str | None:
    """Return a smaller wire copy of a plain-prose user request, or ``None``."""
    config = resolve_config(raw_config)
    if not config["compress_user_prose"] or not _eligible(message, config, agent):
        return None
    return _compress(message, config)


def compress_tool_messages(messages: list[dict[str, Any]], agent: Any, raw_config: Any = None) -> list[dict[str, Any]]:
    """Compress a bounded number of eligible tool-text results on the API copy."""
    config = resolve_config(raw_config)
    if not config["compress_tool_prose"] or config["max_tool_results_per_call"] == 0:
        return messages
    result = copy.deepcopy(messages)
    compressed_count = 0
    for message in result:
        if compressed_count >= config["max_tool_results_per_call"]:
            break
        if message.get("role") != "tool" or not _eligible(message.get("content"), config, agent):
            continue
        compressed = _compress(message["content"], config)
        if compressed:
            message["content"] = compressed
            compressed_count += 1
    return result
