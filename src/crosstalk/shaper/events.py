"""Classification of upstream SSE events into what a policy may see (§12.1)."""

import json
from dataclasses import dataclass
from typing import cast

from crosstalk.shaper.sse import SSEEvent

HEADER_FIELDS = ("id", "object", "created", "model")


@dataclass(frozen=True, slots=True)
class EventMeta:
    """Sizes and flags only: a policy never sees text, so it cannot time on content."""

    raw_len: int
    content_len: int
    mergeable: bool
    terminal: bool
    arrival_ns: int


@dataclass(frozen=True, slots=True)
class UpstreamEvent:
    meta: EventMeta
    raw: bytes
    content: str
    index: int
    header: dict[str, object]


def _object(value: object) -> dict[str, object] | None:
    return cast(dict[str, object], value) if isinstance(value, dict) else None


def classify(event: SSEEvent, arrival_ns: int) -> UpstreamEvent:
    """Mergeable means a content-only delta: no role, tool calls, logprobs, finish or usage."""
    content = ""
    index = 0
    mergeable = False
    terminal = False
    header: dict[str, object] = {}
    try:
        parsed = _object(json.loads(event.data))
    except ValueError:
        parsed = None
    if parsed is not None:
        header = {key: parsed[key] for key in HEADER_FIELDS if key in parsed}
        terminal = parsed.get("usage") is not None
        choices = parsed.get("choices")
        if isinstance(choices, list):
            items = cast(list[object], choices)
            if len(items) == 1 and (choice := _object(items[0])) is not None:
                delta = _object(choice.get("delta")) or {}
                value = delta.get("content")
                content = value if isinstance(value, str) else ""
                position = choice.get("index", 0)
                index = position if isinstance(position, int) else 0
                terminal = terminal or choice.get("finish_reason") is not None
                mergeable = (
                    not terminal
                    and set(delta) == {"content"}
                    and isinstance(value, str)
                    and choice.get("logprobs") is None
                )
    return UpstreamEvent(
        EventMeta(len(event.raw), len(content.encode()), mergeable, terminal, arrival_ns),
        event.raw,
        content,
        index,
        header,
    )
