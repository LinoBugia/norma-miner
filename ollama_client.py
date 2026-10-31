"""Lean streaming client for the Ollama chat API.

Yields tokens as (channel, text) pairs, where channel is "thinking" or
"content". Thinking models such as deepseek-r1 are covered twice: through
the native "think" field of newer Ollama versions, and as a fallback by
filtering <think>...</think> tags out of the content stream.
"""

from __future__ import annotations

import json
from typing import Generator, Iterable

import requests


class OllamaError(RuntimeError):
    pass


class Aborted(RuntimeError):
    pass


def ping(url: str, timeout: float = 3.0) -> bool:
    try:
        return requests.get(f"{url}/api/version", timeout=timeout).ok
    except requests.RequestException:
        return False


def list_models(url: str, timeout: float = 5.0) -> list[str]:
    try:
        r = requests.get(f"{url}/api/tags", timeout=timeout)
        r.raise_for_status()
        return sorted(m["name"] for m in r.json().get("models", []))
    except requests.RequestException:
        return []


class ThinkTagSplitter:
    """Splits a content stream containing <think> tags into thinking/content.

    A tag may be cut in half across streaming chunks, so a small buffer is
    held back at the end.
    """

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self.buf = ""
        self.in_think = False

    def feed(self, text: str) -> Iterable[tuple[str, str]]:
        self.buf += text
        while True:
            tag = self.CLOSE if self.in_think else self.OPEN
            idx = self.buf.find(tag)
            if idx >= 0:
                before = self.buf[:idx]
                if before:
                    yield ("thinking" if self.in_think else "content", before)
                self.buf = self.buf[idx + len(tag):]
                self.in_think = not self.in_think
                continue
            # emit everything except a possible partial tag at the end
            keep = 0
            for k in range(min(len(tag) - 1, len(self.buf)), 0, -1):
                if tag.startswith(self.buf[-k:]):
                    keep = k
                    break
            emit = self.buf[:len(self.buf) - keep]
            if emit:
                yield ("thinking" if self.in_think else "content", emit)
            self.buf = self.buf[len(self.buf) - keep:]
            return

    def flush(self) -> Iterable[tuple[str, str]]:
        if self.buf:
            yield ("thinking" if self.in_think else "content", self.buf)
            self.buf = ""


def _raw_stream(url, payload, stop_event, timeout) -> Generator[dict, None, None]:
    try:
        with requests.post(f"{url}/api/chat", json=payload, stream=True,
                           timeout=(10, timeout)) as r:
            if r.status_code != 200:
                raise OllamaError(f"Ollama HTTP {r.status_code}: {r.text[:500]}")
            for line in r.iter_lines():
                if stop_event is not None and stop_event.is_set():
                    raise Aborted("Aborted")
                if not line:
                    continue
                data = json.loads(line)
                if "error" in data:
                    raise OllamaError(data["error"])
                yield data
                if data.get("done"):
                    return
    except requests.RequestException as e:
        raise OllamaError(f"Connection to Ollama failed: {e}") from e


def chat_stream(url: str, model: str, messages: list[dict],
                options: dict | None = None, think: bool = False,
                stop_event=None, timeout: float = 900.0,
                ) -> Generator[tuple[str, str], None, None]:
    """Stream a chat response as (channel, text) pairs."""
    payload: dict = {"model": model, "messages": messages, "stream": True}
    if options:
        payload["options"] = options

    attempts = [dict(payload, think=True), payload] if think else [payload]
    last_err: OllamaError | None = None
    for attempt in attempts:
        splitter = ThinkTagSplitter()
        try:
            started = False
            for data in _raw_stream(url, attempt, stop_event, timeout):
                started = True
                msg = data.get("message", {})
                if msg.get("thinking"):
                    yield ("thinking", msg["thinking"])
                if msg.get("content"):
                    yield from splitter.feed(msg["content"])
            yield from splitter.flush()
            return
        except OllamaError as e:
            # older Ollama version / model without native think support:
            # retry without the think field, the splitter filters <think> tags
            if attempt is not attempts[-1] and not started and (
                    "think" in str(e).lower()):
                last_err = e
                continue
            raise
    raise last_err or OllamaError("Chat failed")
