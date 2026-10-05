from typing import Protocol


class TokenCounter(Protocol):
    name: str
    revision: str | None

    def count(self, text: str) -> int: ...


class WhitespaceCounter:
    """Development-only counter for mock sessions; never valid for a vLLM workload."""

    name = "whitespace"
    revision: str | None = None

    def count(self, text: str) -> int:
        return len(text.split())


class HuggingFaceCounter:
    """Counts tokens with the served model's tokenizer at a pinned revision."""

    def __init__(self, model: str, revision: str) -> None:
        from tokenizers import Tokenizer

        self.name = model
        self.revision: str | None = revision
        self._tokenizer = Tokenizer.from_pretrained(model, revision=revision)

    def count(self, text: str) -> int:
        return len(self._tokenizer.encode(text, add_special_tokens=False).ids)
