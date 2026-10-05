from collections import Counter, defaultdict
from pathlib import Path

import pytest
import yaml
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError

from crosstalk.common.errors import ConfigError, ValidationError
from crosstalk.common.ids import is_ulid
from crosstalk.contracts.config import Regime, SessionPlan
from crosstalk.workloads.corpus import (
    MIN_FAMILIES,
    Corpus,
    CorpusSource,
    build,
    check_text,
    load_corpus,
    pad_text,
    render,
)
from crosstalk.workloads.planner import SEED_HIGH, SEED_LOW, encode_body, plan_session, request_body
from crosstalk.workloads.tokens import WhitespaceCounter

SOURCE = Path(__file__).parents[1] / "workloads" / "corpus" / "v1" / "corpus.yaml"


@pytest.fixture(scope="module")
def corpus(tmp_path_factory: pytest.TempPathFactory) -> Corpus:
    output = tmp_path_factory.mktemp("corpus")
    build(SOURCE, output, WhitespaceCounter())
    return load_corpus(output)


def _plan(**overrides: object) -> SessionPlan:
    values: dict[str, object] = {
        "experiment_id": "e0",
        "workload": "workloads/v1",
        "per_class": 12,
        "concurrency": 1,
        "seed": 7,
        "warmup": 3,
        "validation": False,
    }
    values.update(overrides)
    return SessionPlan.model_validate(values)


def test_corpus_shape_and_hash(corpus: Corpus, tmp_path: Path) -> None:
    manifest = corpus.manifest
    assert manifest["prompts"] == 1200
    assert manifest["families"] == 240
    assert manifest["determinism_prompts"] == 12
    main = [row for row in corpus.rows if row["role"] == "main"]
    per_class = Counter(str(row["class"]) for row in main)
    assert set(per_class.values()) == {200}
    families = defaultdict(set)
    for row in main:
        families[row["family_id"]].add(row["template_id"])
    assert all(len(templates) == 5 for templates in families.values())
    assert all(row["t2_sensitive"] == (row["class"] == "medical") for row in corpus.rows)
    assert all(int(str(row["padded_bytes"])) % 32 == 0 for row in corpus.rows)
    again = build(SOURCE, tmp_path / "again", WhitespaceCounter())
    assert again["workload_sha256"] == manifest["workload_sha256"]


def test_corpus_rejects_tampered_parquet(corpus: Corpus, tmp_path: Path) -> None:
    build(SOURCE, tmp_path, WhitespaceCounter())
    with (tmp_path / "corpus.parquet").open("ab") as handle:
        handle.write(b"x")
    with pytest.raises(ValidationError):
        load_corpus(tmp_path)


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("contact me at someone@example.org please", "email"),
        ("call +1 (555) 010-9999 today", "phone"),
        ("account 123456789", "long_number"),
        ("see https://example.org", "url"),
        ('a "quoted" topic', "non_ascii_or_escaped"),
        ("café culture", "non_ascii_or_escaped"),
        ("back\\slash", "non_ascii_or_escaped"),
    ],
)
def test_banned_patterns(text: str, problem: str) -> None:
    assert problem in check_text(text)


def test_clean_text_passes() -> None:
    assert check_text("What are the key points about index fund investing?") == []


def _source(**changes: object) -> CorpusSource:
    data = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    data.update(changes)
    return CorpusSource.model_validate(data)


def test_corpus_rejects_duplicate_family() -> None:
    data = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    first = next(iter(data["classes"]["legal"]["subtopics"].values()))
    first.append("type two diabetes")
    with pytest.raises(ConfigError, match="duplicate"):
        render(CorpusSource.model_validate(data))


def test_corpus_rejects_small_class() -> None:
    data = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    groups = data["classes"]["travel"]["subtopics"]
    groups[next(iter(groups))] = groups[next(iter(groups))][:1]
    with pytest.raises(ConfigError, match=str(MIN_FAMILIES)):
        render(CorpusSource.model_validate(data))


def test_corpus_rejects_banned_topic() -> None:
    data = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    next(iter(data["classes"]["finance"]["subtopics"].values())).append("email a@b.co")
    with pytest.raises(ValidationError, match="Banned"):
        render(CorpusSource.model_validate(data))


def test_template_requires_one_slot() -> None:
    data = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    data["templates"]["t0"] = "No slot here"
    with pytest.raises(PydanticValidationError):
        CorpusSource.model_validate(data)


@given(st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), max_size=200))
def test_pad_text(text: str) -> None:
    padded = pad_text(text)
    assert padded.startswith(text)
    assert len(padded.encode()) % 32 == 0
    assert len(padded) - len(text) < 32


def test_session_plan_source_is_exclusive() -> None:
    with pytest.raises(PydanticValidationError):
        SessionPlan(experiment_id="x", concurrency=1, seed=1)
    with pytest.raises(PydanticValidationError):
        SessionPlan(experiment_id="x", requests=5, workload="w", per_class=3, concurrency=1, seed=1)
    with pytest.raises(PydanticValidationError):
        SessionPlan(experiment_id="x", workload="w", concurrency=1, seed=1)
    with pytest.raises(PydanticValidationError):
        _plan(target_lengths=[0])
    with pytest.raises(PydanticValidationError):
        _plan(target_lengths=[600])
    with pytest.raises(PydanticValidationError):
        _plan(arms=["a0", "b1"])


def test_block_randomization(corpus: Corpus) -> None:
    plan = _plan(arms=["a0", "a1"], determinism_every=25)
    planned = plan_session(plan, corpus)
    rows = planned.rows
    assert [row["slot"] for row in rows] == list(range(len(rows)))
    assert [row["role"] for row in rows[:3]] == ["warmup"] * 3
    main = [row for row in rows if row["role"] == "main"]
    assert len(main) == 6 * 2 * 12
    for block in range(12):
        combos = Counter((row["class"], row["arm"]) for row in main if row["block"] == block)
        assert len(combos) == 12
        assert set(combos.values()) == {1}
        # Within a block every arm of a class sees the same prompt (paired design).
        by_class = defaultdict(set)
        for row in main:
            if row["block"] == block:
                by_class[row["class"]].add(row["prompt_id"])
        assert all(len(prompts) == 1 for prompts in by_class.values())
    after_warmup = rows[3:]
    probes = [index for index, row in enumerate(after_warmup) if row["role"] == "determinism"]
    assert (
        probes
        == [index for index in range(len(after_warmup)) if (index + 1) % 25 == 0][: len(probes)]
    )
    assert len(probes) >= len(main) // 25
    assert all(is_ulid(str(row["request_id"])) for row in rows)
    assert len({row["request_id"] for row in rows}) == len(rows)
    assert all(SEED_LOW <= int(str(row["seed"])) <= SEED_HIGH for row in rows)
    assert all(row["min_tokens"] is None for row in rows)


def test_plan_is_deterministic(corpus: Corpus) -> None:
    first = plan_session(_plan(), corpus)
    second = plan_session(_plan(), corpus)
    third = plan_session(_plan(seed=8), corpus)
    assert first.sha256() == second.sha256() != third.sha256()
    assert [r["request_id"] for r in first.rows] != [r["request_id"] for r in second.rows]


def test_nat_uses_distinct_families_first(corpus: Corpus) -> None:
    planned = plan_session(_plan(per_class=40, warmup=0, determinism_every=0), corpus)
    families = Counter(str(row["family_id"]) for row in planned.rows)
    assert len(families) == 6 * 40
    assert set(families.values()) == {1}


def _body_lengths(corpus: Corpus, plan: SessionPlan) -> dict[str, list[int]]:
    planned = plan_session(plan, corpus)
    lengths: dict[str, list[int]] = defaultdict(list)
    for row in planned.rows:
        if row["role"] != "main":
            continue
        body = request_body(
            row, planned.texts[str(row["request_id"])], "Qwen/Qwen2.5-1.5B-Instruct"
        )
        lengths[str(row["class"])].append(len(encode_body(body)))
    return {klass: sorted(values) for klass, values in lengths.items()}


def test_match_p_request_size_parity(corpus: Corpus) -> None:
    lengths = _body_lengths(corpus, _plan(regime=Regime.MATCH_P, per_class=60))
    reference = next(iter(lengths.values()))
    assert all(values == reference for values in lengths.values())
    nat = _body_lengths(corpus, _plan(regime=Regime.NAT, per_class=60))
    assert len({tuple(values) for values in nat.values()}) > 1


def test_match_pr_length_forcing(corpus: Corpus) -> None:
    plan = _plan(regime=Regime.MATCH_PR, per_class=30)
    planned = plan_session(plan, corpus)
    targets: dict[str, list[int]] = defaultdict(list)
    for row in planned.rows:
        assert row["min_tokens"] == row["max_completion_tokens"] == row["target_len"]
        assert row["target_len"] in (96, 160, 224)
        if row["role"] == "main":
            targets[str(row["class"])].append(int(str(row["target_len"])))
    distributions = {tuple(sorted(v)) for v in targets.values()}
    assert len(distributions) == 1
    assert set(Counter(next(iter(distributions))).values()) == {10}
    assert _body_lengths(corpus, plan) == _body_lengths(corpus, plan)


def test_unknown_class_rejected(corpus: Corpus) -> None:
    with pytest.raises(ConfigError):
        plan_session(_plan(classes=["medical", "astrology"]), corpus)


def test_synthetic_plan() -> None:
    plan = SessionPlan(experiment_id="v", requests=5, concurrency=1, seed=3)
    planned = plan_session(plan, None)
    assert len(planned.rows) == 5
    assert all(row["role"] == "main" and row["arm"] == "a0" for row in planned.rows)
    assert (
        planned.texts[str(planned.rows[2]["request_id"])] == "Explain a simple synthetic topic 2."
    )


@settings(
    max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    per_class=st.integers(1, 30),
    seed=st.integers(0, 10_000),
    arms=st.lists(st.sampled_from(["a0", "a1", "a2", "a3"]), min_size=1, max_size=4, unique=True),
    regime=st.sampled_from(list(Regime)),
    every=st.integers(0, 30),
    warmup=st.integers(0, 5),
)
def test_plan_invariants(
    corpus: Corpus,
    per_class: int,
    seed: int,
    arms: list[str],
    regime: Regime,
    every: int,
    warmup: int,
) -> None:
    plan = _plan(
        per_class=per_class,
        seed=seed,
        arms=arms,
        regime=regime,
        determinism_every=every,
        warmup=warmup,
    )
    planned = plan_session(plan, corpus)
    main = [row for row in planned.rows if row["role"] == "main"]
    assert Counter((row["class"], row["arm"]) for row in main) == {
        (klass, arm): per_class for klass in corpus.classes for arm in arms
    }
    assert sum(row["role"] == "warmup" for row in planned.rows) == warmup
    assert set(planned.texts) == {row["request_id"] for row in planned.rows}
    if regime is not Regime.NAT:
        assert all(len(text.encode()) % 32 == 0 for text in planned.texts.values())


@pytest.mark.parametrize("path", sorted((SOURCE.parents[3] / "experiments").glob("*.yaml")))
def test_experiment_configs_parse(path: Path) -> None:
    from crosstalk.contracts.config import ExperimentConfig

    config = ExperimentConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    assert all(session.experiment_id == config.name for session in config.sessions)


@pytest.mark.parametrize("name", ["e1-pilot.yaml", "e1.yaml"])
def test_experiment_policies_match_the_shaper_policy_file(name: str) -> None:
    from crosstalk.contracts.config import ExperimentConfig, PolicyConfig

    experiments = SOURCE.parents[3] / "experiments"
    config = ExperimentConfig.model_validate(
        yaml.safe_load((experiments / name).read_text(encoding="utf-8"))
    )
    shaper = [
        PolicyConfig.model_validate(item)
        for item in yaml.safe_load(
            (experiments / "policies" / "arms.yaml").read_text(encoding="utf-8")
        )
    ]
    assert sorted(config.policy_set, key=lambda p: p.arm) == sorted(shaper, key=lambda p: p.arm)
