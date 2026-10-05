import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pyarrow as pa
import yaml
from pydantic import Field, field_validator

from crosstalk.common.artifacts import atomic_write, canonical_json, sha256_bytes, sha256_file
from crosstalk.common.errors import ConfigError, ValidationError
from crosstalk.contracts.config import StrictModel
from crosstalk.contracts.parquet import read_table, write_table
from crosstalk.workloads.tokens import TokenCounter

PAD_BYTES = 32
TOKEN_BUCKET = 8
MIN_FAMILIES = 40
TEMPLATES = 5

CORPUS_SCHEMA = pa.schema(
    [
        pa.field("prompt_id", pa.string()),
        pa.field("class", pa.string()),
        pa.field("family_id", pa.string()),
        pa.field("template_id", pa.string()),
        pa.field("role", pa.string()),
        pa.field("t2_sensitive", pa.bool_()),
        pa.field("text", pa.string()),
        pa.field("n_bytes", pa.int32()),
        pa.field("n_tokens", pa.int32()),
        pa.field("padded_text", pa.string()),
        pa.field("padded_bytes", pa.int32()),
        pa.field("padded_tokens", pa.int32()),
    ]
)

# Realistic-looking personal data must never enter a prompt (PRODUCT.md §5.2).
BANNED_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),
    "phone": re.compile(r"\+?\d[\d ().-]{6,}\d"),
    "long_number": re.compile(r"\d{5,}"),
    "url": re.compile(r"https?://|www\.", re.IGNORECASE),
}
# ASCII without characters that JSON escapes: request size then depends only on byte length.
_SAFE_TEXT = re.compile(r"[ -~]*")
_UNSAFE = frozenset('"\\')


class CorpusClass(StrictModel):
    determinism: list[str] = Field(min_length=1)
    subtopics: dict[str, list[str]] = Field(min_length=1)


class CorpusSource(StrictModel):
    version: str
    sensitive_class: str
    templates: dict[str, str] = Field(min_length=TEMPLATES, max_length=TEMPLATES)
    determinism_template: str
    classes: dict[str, CorpusClass] = Field(min_length=2)

    @field_validator("templates")
    @classmethod
    def check_templates(cls, value: dict[str, str]) -> dict[str, str]:
        for name, template in value.items():
            if template.count("{topic}") != 1:
                raise ValueError(f"template {name} must contain exactly one {{topic}} slot")
        return value


@dataclass(frozen=True, slots=True)
class Prompt:
    prompt_id: str
    klass: str
    family_id: str
    template_id: str
    role: str
    t2_sensitive: bool
    text: str


def pad_text(text: str, multiple: int = PAD_BYTES) -> str:
    """Pad with trailing spaces to the next multiple of ``multiple`` UTF-8 bytes."""
    size = len(text.encode())
    return text + " " * ((-size) % multiple)


def check_text(text: str) -> list[str]:
    problems = [name for name, pattern in BANNED_PATTERNS.items() if pattern.search(text)]
    if not _SAFE_TEXT.fullmatch(text) or _UNSAFE.intersection(text):
        problems.append("non_ascii_or_escaped")
    return problems


def load_source(path: Path) -> CorpusSource:
    return CorpusSource.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def render(source: CorpusSource) -> list[Prompt]:
    if source.sensitive_class not in source.classes:
        raise ConfigError("sensitive_class must name a corpus class")
    if source.determinism_template not in source.templates:
        raise ConfigError("determinism_template must name a template")
    prompts: list[Prompt] = []
    seen_topics: set[str] = set()
    for klass, spec in source.classes.items():
        topics = [topic for group in spec.subtopics.values() for topic in group]
        if len(topics) < MIN_FAMILIES:
            raise ConfigError(f"class {klass} has {len(topics)} families; {MIN_FAMILIES} needed")
        sensitive = klass == source.sensitive_class
        for index, topic in enumerate(topics):
            normalized = topic.strip().lower()
            if normalized in seen_topics:
                raise ConfigError(f"duplicate family topic: {topic}")
            seen_topics.add(normalized)
            family = f"{klass}-f{index:03d}"
            for template_id, template in source.templates.items():
                prompts.append(
                    Prompt(
                        f"{family}-{template_id}",
                        klass,
                        family,
                        template_id,
                        "main",
                        sensitive,
                        template.format(topic=topic),
                    )
                )
        for index, topic in enumerate(spec.determinism):
            if topic.strip().lower() in seen_topics:
                raise ConfigError(f"determinism topic duplicates a family: {topic}")
            seen_topics.add(topic.strip().lower())
            family = f"{klass}-d{index:03d}"
            template_id = source.determinism_template
            prompts.append(
                Prompt(
                    f"{family}-{template_id}",
                    klass,
                    family,
                    template_id,
                    "determinism",
                    sensitive,
                    source.templates[template_id].format(topic=topic),
                )
            )
    problems = {p.prompt_id: issues for p in prompts if (issues := check_text(p.text))}
    if problems:
        raise ValidationError(f"Banned patterns in corpus: {problems}")
    return prompts


def build(source_path: Path, output: Path, counter: TokenCounter) -> dict[str, object]:
    source = load_source(source_path)
    prompts = render(source)
    rows: list[dict[str, object]] = []
    for prompt in prompts:
        padded = pad_text(prompt.text)
        rows.append(
            {
                "prompt_id": prompt.prompt_id,
                "class": prompt.klass,
                "family_id": prompt.family_id,
                "template_id": prompt.template_id,
                "role": prompt.role,
                "t2_sensitive": prompt.t2_sensitive,
                "text": prompt.text,
                "n_bytes": len(prompt.text.encode()),
                "n_tokens": counter.count(prompt.text),
                "padded_text": padded,
                "padded_bytes": len(padded.encode()),
                "padded_tokens": counter.count(padded),
            }
        )
    content_sha = sha256_bytes(
        canonical_json({"rows": rows, "tokenizer": counter.name, "revision": counter.revision})
    )
    output.mkdir(parents=True, exist_ok=True)
    corpus_path = output / "corpus.parquet"
    if corpus_path.exists():
        raise ValidationError(f"Artifact already exists: {corpus_path}")
    temporary = output / ".corpus.parquet.partial"
    write_table(pa.Table.from_pylist(rows, schema=CORPUS_SCHEMA), temporary)
    temporary.replace(corpus_path)
    main = [row for row in rows if row["role"] == "main"]
    manifest: dict[str, object] = {
        "workload_version": source.version,
        "workload_sha256": content_sha,
        "source_sha256": sha256_file(source_path),
        "corpus_parquet_sha256": sha256_file(corpus_path),
        "tokenizer": counter.name,
        "tokenizer_revision": counter.revision,
        "classes": sorted(source.classes),
        "sensitive_class": source.sensitive_class,
        "prompts": len(main),
        "families": len({row["family_id"] for row in main}),
        "determinism_prompts": len(rows) - len(main),
    }
    atomic_write(output / "manifest.json", json.dumps(manifest, indent=2).encode())
    return manifest


@dataclass(frozen=True, slots=True)
class Corpus:
    rows: list[dict[str, object]]
    manifest: dict[str, object]

    @property
    def classes(self) -> list[str]:
        classes = self.manifest["classes"]
        if not isinstance(classes, list):
            raise ValidationError("Corpus manifest lists no classes")
        return [str(value) for value in cast(list[object], classes)]


def load_corpus(directory: Path) -> Corpus:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    corpus_path = directory / "corpus.parquet"
    if sha256_file(corpus_path) != manifest["corpus_parquet_sha256"]:
        raise ValidationError("Corpus parquet does not match its manifest hash")
    table = read_table(corpus_path)
    if table.schema != CORPUS_SCHEMA:
        raise ValidationError("Unexpected corpus schema")
    return Corpus(table.to_pylist(), manifest)
