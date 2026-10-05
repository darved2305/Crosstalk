"""Session planning (IMPLEMENTATION_PLAN §13).

Uses only the standard library so the loadgen image needs no ML dependencies.
"""

import json
import random
from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from crosstalk.common.artifacts import canonical_json, sha256_bytes
from crosstalk.common.errors import ConfigError
from crosstalk.common.ids import new_ulid
from crosstalk.contracts.config import Regime, SessionPlan
from crosstalk.workloads.corpus import TOKEN_BUCKET, Corpus

SEED_LOW = 100_000_000
SEED_HIGH = 999_999_999


@dataclass(frozen=True, slots=True)
class PlannedSession:
    rows: list[dict[str, object]]
    texts: dict[str, str]

    def sha256(self) -> str:
        """Hash of the deterministic plan content; request IDs are excluded on purpose."""
        content = [{k: v for k, v in row.items() if k != "request_id"} for row in self.rows]
        return sha256_bytes(canonical_json({"rows": content}))


def request_body(row: dict[str, object], text: str, model: str) -> dict[str, object]:
    """Request body with a fixed key order and fixed-width numeric fields."""
    body: dict[str, object] = {
        "model": model,
        "messages": [{"role": "user", "content": text}],
        "stream": True,
        "stream_options": {"include_usage": True},
        "temperature": row["temperature"],
        "max_completion_tokens": row["max_completion_tokens"],
        "seed": row["seed"],
        "cache_salt": row["request_id"],
    }
    if row["min_tokens"] is not None:
        body["min_tokens"] = row["min_tokens"]
    return body


def encode_body(body: dict[str, object]) -> bytes:
    return json.dumps(body, separators=(",", ":")).encode()


def _cycle[T](items: Sequence[T], rng: random.Random) -> Iterator[T]:
    if not items:
        raise ConfigError("Cannot draw from an empty pool")
    while True:
        order = list(items)
        rng.shuffle(order)
        yield from order


def _spread(
    prompts: Sequence[dict[str, object]], rng: random.Random
) -> Iterator[dict[str, object]]:
    """Cycle prompts so every family appears once before any family repeats.

    Each pass visits the families in a fresh random order and takes the next unused
    template of each, which maximises distinct families (the CV grouping unit) in short
    sessions.
    """
    if not prompts:
        raise ConfigError("Cannot draw from an empty pool")
    by_family: dict[str, list[dict[str, object]]] = defaultdict(list)
    for prompt in sorted(prompts, key=lambda p: str(p["prompt_id"])):
        by_family[str(prompt["family_id"])].append(prompt)
    templates = {family: _cycle(members, rng) for family, members in sorted(by_family.items())}
    families = sorted(by_family)
    while True:
        order = list(families)
        rng.shuffle(order)
        for family in order:
            yield next(templates[family])


def _matched_cells(
    by_class: dict[str, list[dict[str, object]]],
) -> dict[tuple[int, int], int]:
    counts: dict[str, dict[tuple[int, int], int]] = {}
    for klass, prompts in by_class.items():
        cells: dict[tuple[int, int], int] = defaultdict(int)
        for prompt in prompts:
            cells[_cell(prompt)] += 1
        counts[klass] = cells
    keys = [set(cells) for cells in counts.values()]
    common: set[tuple[int, int]] = keys[0].intersection(*keys[1:])
    return {cell: min(cells[cell] for cells in counts.values()) for cell in sorted(common)}


def _cell(prompt: dict[str, object]) -> tuple[int, int]:
    return int(str(prompt["padded_bytes"])), int(str(prompt["padded_tokens"])) // TOKEN_BUCKET


def plan_session(plan: SessionPlan, corpus: Corpus | None) -> PlannedSession:
    if plan.workload is None or corpus is None:
        return _synthetic(plan)
    if plan.per_class is None:
        raise ConfigError("workload sessions require per_class")
    rng = random.Random(plan.seed)
    classes = plan.classes or corpus.classes
    unknown = set(classes) - set(corpus.classes)
    if unknown:
        raise ConfigError(f"Unknown classes: {sorted(unknown)}")
    arms = plan.session_arms
    matched = plan.regime is not Regime.NAT
    main: dict[str, list[dict[str, object]]] = defaultdict(list)
    determinism: list[dict[str, object]] = []
    for row in corpus.rows:
        if row["class"] not in classes:
            continue
        if row["role"] == "main":
            main[str(row["class"])].append(row)
        else:
            determinism.append(row)
    draws: dict[str, list[dict[str, object]]] = {}
    if matched:
        cells = _matched_cells(main)
        if not cells:
            raise ConfigError("No prompt cell is shared by every class; MATCH-P is impossible")
        shared = [cell for cell, count in cells.items() for _ in range(count)]
        cell_sequence = _cycle(shared, rng)
        sequence = [next(cell_sequence) for _ in range(plan.per_class)]
        for klass in classes:
            pools: dict[tuple[int, int], Iterator[dict[str, object]]] = {}
            by_cell: dict[tuple[int, int], list[dict[str, object]]] = defaultdict(list)
            for prompt in main[klass]:
                by_cell[_cell(prompt)].append(prompt)
            for cell in cells:
                pools[cell] = _spread(by_cell[cell], rng)
            draws[klass] = [next(pools[cell]) for cell in sequence]
    else:
        for klass in classes:
            pool = _spread(main[klass], rng)
            draws[klass] = [next(pool) for _ in range(plan.per_class)]
    targets: list[int | None] = [None] * plan.per_class
    if plan.regime is Regime.MATCH_PR:
        length_pool = _cycle(sorted(plan.target_lengths), rng)
        targets = [next(length_pool) for _ in range(plan.per_class)]
    rows: list[dict[str, object]] = []
    texts: dict[str, str] = {}
    probe = _cycle(sorted(determinism, key=lambda p: str(p["prompt_id"])), rng)
    probe_target = min(plan.target_lengths) if plan.regime is Regime.MATCH_PR else None

    def add(prompt: dict[str, object], arm: str, role: str, block: int, target: int | None) -> None:
        request_id = new_ulid()
        tokens = prompt["padded_tokens"] if matched else prompt["n_tokens"]
        row: dict[str, object] = {
            "slot": len(rows),
            "block": block,
            "block_concurrency": plan.concurrency,
            "request_id": request_id,
            "prompt_id": prompt["prompt_id"],
            "arm": arm,
            "stream_interval": plan.stream_interval,
            "regime": plan.regime.value,
            "role": role,
            "temperature": plan.temperature,
            "max_completion_tokens": target if target is not None else plan.max_completion_tokens,
            "min_tokens": target,
            "seed": rng.randint(SEED_LOW, SEED_HIGH),
            "class": prompt["class"],
            "family_id": prompt["family_id"],
            "template_id": prompt["template_id"],
            "prompt_bucket": int(str(tokens)) // TOKEN_BUCKET,
            "target_len": target,
            "t2_sensitive": prompt["t2_sensitive"],
        }
        rows.append(row)
        texts[request_id] = str(prompt["padded_text"] if matched else prompt["text"])

    for _ in range(plan.warmup):
        add(next(probe), arms[0], "warmup", 0, probe_target)
    for block in range(plan.per_class):
        combos = [(klass, arm) for klass in classes for arm in arms]
        rng.shuffle(combos)
        for klass, arm in combos:
            every = plan.determinism_every
            if every and (len(rows) - plan.warmup + 1) % every == 0:
                add(next(probe), arms[0], "determinism", block, probe_target)
            add(draws[klass][block], arm, "main", block, targets[block])
    return PlannedSession(rows, texts)


def _synthetic(plan: SessionPlan) -> PlannedSession:
    if plan.requests is None:
        raise ConfigError("synthetic sessions require requests")
    rows: list[dict[str, object]] = []
    texts: dict[str, str] = {}
    for slot in range(plan.requests):
        request_id = new_ulid()
        rows.append(
            {
                "slot": slot,
                "block": 0,
                "block_concurrency": plan.concurrency,
                "request_id": request_id,
                "prompt_id": f"synthetic-{slot:06d}",
                "arm": plan.arm,
                "stream_interval": plan.stream_interval,
                "regime": Regime.NAT.value,
                "role": "main",
                "temperature": plan.temperature,
                "max_completion_tokens": plan.max_completion_tokens,
                "min_tokens": None,
                "seed": SEED_LOW + plan.seed + slot,
                "class": "synthetic",
                "family_id": f"synthetic-{slot:06d}",
                "template_id": "t0",
                "prompt_bucket": 0,
                "target_len": None,
                "t2_sensitive": False,
            }
        )
        texts[request_id] = f"Explain a simple synthetic topic {slot}."
    return PlannedSession(rows, texts)
