"""Bounded map-reduce preprocessing for oversized LM Studio user requests.

This module is deliberately upstream of the normal conversation loop.  It does
not create child Hermes sessions, invoke tools, persist messages, or mutate the
main transcript.  Its only output is a compact task brief that can replace the
wire copy of one oversized user message; the normal agent retains ownership of
tools, writes, context pruning, and final execution.
"""

from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from agent.auxiliary_client import call_llm

logger = logging.getLogger(__name__)

DEFAULTS = {
    "enabled": True,
    "providers": ["lmstudio"],
    "min_input_chars": 24000,
    "max_workers": 4,
    "chunk_chars": 12000,
    "max_chunk_chars": 48000,
    "max_chunks": 16,
    "chunk_overlap_chars": 600,
    "worker_max_tokens": 700,
    "reducer_max_tokens": 1400,
    "timeout_seconds": 300,
}


def resolve_config(raw: Any) -> dict[str, Any]:
    """Return validated, bounded configuration without failing a user turn."""
    config = dict(DEFAULTS)
    if isinstance(raw, dict):
        config.update({key: value for key, value in raw.items() if value is not None})

    def integer(key: str, minimum: int, maximum: int) -> int:
        try:
            value = int(config[key])
        except (KeyError, TypeError, ValueError):
            return int(DEFAULTS[key])
        return max(minimum, min(maximum, value))

    enabled = config.get("enabled")
    config["enabled"] = (
        enabled
        if isinstance(enabled, bool)
        else str(enabled).strip().lower() not in {"", "0", "false", "no", "off"}
    )
    providers = config.get("providers")
    config["providers"] = (
        [str(item).strip().lower() for item in providers]
        if isinstance(providers, list)
        else ["lmstudio"]
    )
    config["min_input_chars"] = integer("min_input_chars", 1000, 500000)
    config["max_workers"] = integer("max_workers", 1, 4)
    config["chunk_chars"] = integer("chunk_chars", 2000, 48000)
    config["max_chunk_chars"] = integer("max_chunk_chars", config["chunk_chars"], 96000)
    config["max_chunks"] = integer("max_chunks", 2, 32)
    config["chunk_overlap_chars"] = integer("chunk_overlap_chars", 0, config["chunk_chars"] // 3)
    config["worker_max_tokens"] = integer("worker_max_tokens", 128, 2000)
    config["reducer_max_tokens"] = integer("reducer_max_tokens", 256, 4000)
    config["timeout_seconds"] = integer("timeout_seconds", 30, 1800)
    return config


def should_prepare(message: Any, agent: Any, config: dict[str, Any]) -> bool:
    """Return whether this request is eligible for bounded preprocessing."""
    if not config["enabled"] or not isinstance(message, str):
        return False
    if len(message) < config["min_input_chars"]:
        return False
    provider = str(getattr(agent, "provider", "") or "").strip().lower()
    return provider in config["providers"]


def split_request(text: str, config: dict[str, Any]) -> list[str] | None:
    """Split at paragraph/newline boundaries while retaining a small overlap.

    ``None`` means the request cannot be split safely within the configured
    per-chunk ceiling.  The caller then leaves the normal Hermes path intact.
    """
    overlap = config["chunk_overlap_chars"]
    chunk_size = config["chunk_chars"]
    needed = math.ceil(max(0, len(text) - overlap) / (chunk_size - overlap))
    if needed > config["max_chunks"]:
        chunk_size = math.ceil(
            (len(text) + overlap * (config["max_chunks"] - 1)) / config["max_chunks"]
        )
    if chunk_size > config["max_chunk_chars"]:
        return None

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + chunk_size)
        if end < len(text):
            boundary = max(text.rfind("\n\n", start + chunk_size // 2, end), text.rfind("\n", start + chunk_size // 2, end))
            if boundary > start:
                end = boundary
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks if 1 < len(chunks) <= config["max_chunks"] else None


def _response_text(response: Any) -> str:
    try:
        message = response.choices[0].message
        content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
        return str(content or "").strip()
    except (AttributeError, IndexError, TypeError):
        return ""


def _worker_messages(chunk: str, index: int, total: int) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are a read-only planning worker. Analyze only the assigned fragment. "
                "Do not call tools, execute commands, edit files, or answer the user. "
                "Return a concise structured brief with: objectives, requirements, "
                "constraints, exact paths/URLs/identifiers, dependencies, and open questions. "
                "Preserve literals exactly."
            ),
        },
        {
            "role": "user",
            "content": f"Fragment {index}/{total}:\n\n{chunk}",
        },
    ]


def _reduce_messages(briefs: list[str]) -> list[dict[str, str]]:
    joined = "\n\n".join(f"Worker {index}:\n{brief}" for index, brief in enumerate(briefs, start=1))
    return [
        {
            "role": "system",
            "content": (
                "You consolidate read-only planning briefs for an execution agent. "
                "Return a compact task specification: goal, ordered work phases, constraints, "
                "exact references to preserve, and unresolved questions. Do not call tools or "
                "claim work was completed. Remove duplication without dropping requirements."
            ),
        },
        {"role": "user", "content": joined},
    ]


def _call(agent: Any, messages: list[dict[str, str]], *, max_tokens: int, timeout: int) -> str:
    response = call_llm(
        task="long_input_fanout",
        provider=getattr(agent, "provider", None),
        model=getattr(agent, "model", None),
        base_url=getattr(agent, "base_url", None),
        api_key=getattr(agent, "api_key", None),
        api_mode=getattr(agent, "api_mode", None),
        messages=messages,
        max_tokens=max_tokens,
        timeout=timeout,
        tools=None,
    )
    return _response_text(response)


def prepare_request(message: Any, agent: Any, raw_config: Any = None) -> str | None:
    """Return a compact parallel brief, or ``None`` to use the normal request.

    Failures are intentionally isolated. A partial worker failure never
    contaminates the main transcript; if no usable briefs are returned, the
    caller falls back to the established pruning/compression path unchanged.
    """
    config = resolve_config(raw_config)
    if not should_prepare(message, agent, config):
        return None
    chunks = split_request(message, config)
    if not chunks:
        logger.info("Parallel context skipped: request cannot be safely chunked")
        return None

    results: list[str | None] = [None] * len(chunks)
    with ThreadPoolExecutor(max_workers=min(config["max_workers"], len(chunks))) as pool:
        futures = {
            pool.submit(
                _call,
                agent,
                _worker_messages(chunk, index + 1, len(chunks)),
                max_tokens=config["worker_max_tokens"],
                timeout=config["timeout_seconds"],
            ): index
            for index, chunk in enumerate(chunks)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:  # A worker must never fail the main turn.
                logger.warning("Parallel context worker %d/%d failed: %s", index + 1, len(chunks), exc)

    briefs = [brief for brief in results if brief]
    if not briefs:
        return None
    try:
        consolidated = _call(
            agent,
            _reduce_messages(briefs),
            max_tokens=config["reducer_max_tokens"],
            timeout=config["timeout_seconds"],
        )
    except Exception as exc:
        logger.warning("Parallel context reducer failed: %s", exc)
        return None
    if not consolidated:
        return None
    logger.info(
        "Parallel context prepared: chars=%d chunks=%d worker_briefs=%d",
        len(message), len(chunks), len(briefs),
    )
    return (
        "[The original user request was processed by bounded parallel planning. "
        "Use this consolidated specification as the source of truth. Execute the task normally, "
        "using tools only from the main Hermes loop.]\n\n"
        + consolidated
    )
