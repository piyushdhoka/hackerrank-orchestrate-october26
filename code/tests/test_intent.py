"""Intent classification: deterministic first pass, LLM second gate (mocked + optional live)."""
import json
import os
import sys
from pathlib import Path

import pytest

CODE = Path(__file__).resolve().parents[1]
ROOT = CODE.parent
sys.path.insert(0, str(CODE))

from agent.llm import intent  # noqa: E402
from agent.data import read_csv  # noqa: E402
from agent.llm.client import LLM, extract_json  # noqa: E402
from tests.intent_corpus import CORPUS  # noqa: E402

# The ten eval requests whose text plainly declines deletion (see README §3.2). Every other
# dataset request asks for deletion or follows up on one.
NON_DELETION_CASES = {f"case_{n}" for n in range(533, 543)}


def dataset_texts():
    rows = read_csv(ROOT / "dataset" / "deletion_requests.csv") + read_csv(ROOT / "dataset" / "sample_deletion_requests.csv")
    return [(r["case_id"], r["request_text"], r["case_id"] not in NON_DELETION_CASES) for r in rows]


def gold_backend(labels: dict[str, bool], calls: list):
    """Fake Anthropic: answers from a label table, like a perfect classifier."""
    def backend(provider, model, payload):
        calls.append((provider, model))
        items = [json.loads(line) for line in payload["user"].split("\n") if line.startswith("{\"id\"")]
        results = [{"id": it["id"], "authorizes_deletion": labels[it["text"]], "intent": "x", "language": "xx"}
                   for it in items]
        return json.dumps({"results": results}), 100, 50
    return backend


# ------------------------------------------------------------------ deterministic first pass
def test_deterministic_is_exact_on_all_dataset_requests():
    wrong = [(cid, t[:80]) for cid, t, gold in dataset_texts() if intent.deterministic(t)["authorizes_deletion"] != gold]
    assert wrong == []


@pytest.mark.parametrize("text,gold,lang,tier", [c for c in CORPUS if c[3] == "core"])
def test_deterministic_core_multilingual(text, gold, lang, tier):
    assert intent.deterministic(text)["authorizes_deletion"] is gold, (lang, text)


def test_hard_cases_are_genuinely_hard_for_rules():
    """If rules start passing every hard case, the hard tier no longer measures the LLM gate."""
    hard = [c for c in CORPUS if c[3] == "hard"]
    assert any(intent.deterministic(t)["authorizes_deletion"] is not g for t, g, _, _ in hard)


def test_negation_beats_deletion_words():
    d = intent.deterministic("Please do not delete my account, I only want marketing emails to stop.")
    assert d["authorizes_deletion"] is False and d["label"] == "non_deletion_explicit"


# ------------------------------------------------------------------ LLM gate (mocked)
def test_llm_gate_corrects_every_hard_case(tmp_path):
    labels = {t: g for t, g, _, _ in CORPUS}
    calls = []
    llm = LLM(tmp_path, backend=gold_backend(labels, calls))
    decisions, report = intent.classify_all(list(labels), llm)
    assert all(decisions[t].authorizes_deletion is g for t, g in labels.items())
    hard_wrong = {t for t, g, _, tier in CORPUS if tier == "hard" and intent.deterministic(t)["authorizes_deletion"] is not g}
    assert {d["text"] for d in report["disagreements"]} == {t[:160] for t in hard_wrong}
    assert all(decisions[t].basis == "llm_override" for t in hard_wrong)
    assert report["llm_failures"] == 0


def test_llm_gate_batches_and_caches(tmp_path):
    texts = [t for _, t, _ in dataset_texts()]
    labels = {t: g for _, t, g in dataset_texts()}
    calls = []
    llm = LLM(tmp_path, backend=gold_backend(labels, calls))
    decisions, report = intent.classify_all(texts, llm)
    distinct = len(set(texts))
    assert report["distinct_texts"] == distinct
    assert len(calls) == -(-distinct // intent.BATCH)          # ceil: one call per batch
    assert sum(not d.authorizes_deletion for d in decisions.values()) == 10
    calls.clear()
    intent.classify_all(texts, LLM(tmp_path, backend=gold_backend(labels, calls)))
    assert calls == []                                          # warm cache: zero model calls


def test_cache_key_includes_model(tmp_path, monkeypatch):
    labels = {t: g for t, g, _, _ in CORPUS[:3]}
    calls = []
    intent.classify_all(list(labels), LLM(tmp_path, backend=gold_backend(labels, calls)))
    monkeypatch.setenv("GHOST_INTENT_MODEL", "some-other-model")
    intent.classify_all(list(labels), LLM(tmp_path, backend=gold_backend(labels, calls)))
    assert len(calls) == 2 and calls[1][1] == "some-other-model"


def test_malformed_reply_falls_back_to_deterministic(tmp_path):
    llm = LLM(tmp_path, backend=lambda p, m, x: ("sorry, I cannot help with that", 10, 5))
    texts = [t for t, _, _, _ in CORPUS]
    decisions, report = intent.classify_all(texts, llm)
    assert report["llm_failures"] == len(set(texts))
    assert all(d.basis == "deterministic" for d in decisions.values())


def test_partial_reply_only_fills_returned_ids(tmp_path):
    def backend(p, m, payload):
        return '```json\n{"results":[{"id":0,"authorizes_deletion":false,"intent":"access"}]}\n```', 10, 5
    texts = sorted({t for t, _, _, _ in CORPUS[:4]})
    decisions, report = intent.classify_all(texts, LLM(tmp_path, backend=backend))
    assert decisions[texts[0]].authorizes_deletion is False
    assert report["llm_failures"] == len(texts) - 1


def test_no_key_means_deterministic_only(tmp_path, monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY",
              "GHOST_GEMINI_BACKEND", "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT"):
        monkeypatch.delenv(k, raising=False)
    decisions, report = intent.classify_all(["Please delete my account."], LLM(tmp_path))
    assert report["llm_available"] is False
    assert decisions["Please delete my account."].basis == "deterministic"


def test_extract_json_variants():
    assert extract_json('{"a": 1}') == {"a": 1}
    assert extract_json('text before ```json\n{"a": [1,2]}\n``` after') == {"a": [1, 2]}
    assert extract_json("no json here") is None


# ------------------------------------------------------------------ provider routing (Gemini by default)
def test_gates_default_to_gemini(monkeypatch):
    for k in ("GHOST_INTENT_PROVIDER", "GHOST_INTENT_MODEL", "GHOST_DOC_PROVIDER", "GHOST_DOC_MODEL"):
        monkeypatch.delenv(k, raising=False)
    from agent.llm import client as llm
    assert (llm.intent_provider(), llm.intent_model()) == ("gemini", "gemini-3.1-pro-preview")
    assert (llm.doc_provider(), llm.doc_model()) == ("gemini", "gemini-3.1-pro-preview")


def test_no_flash_model_anywhere_by_default(monkeypatch):
    for k in ("GHOST_INTENT_PROVIDER", "GHOST_INTENT_MODEL", "GHOST_DOC_PROVIDER", "GHOST_DOC_MODEL",
              "GHOST_DOC_FALLBACK_MODEL"):
        monkeypatch.delenv(k, raising=False)
    from agent.llm import client as llm
    assert llm.doc_models() == ["gemini-3.1-pro-preview"]          # no fallback model unless explicitly configured
    assert not any("flash" in m for gate in llm.DEFAULT_MODELS.values() for m in gate.values())


def test_gemini_retries_transient_errors_with_backoff():
    from agent.llm import client as llm
    r = llm.gemini_retry_options()
    assert r.attempts >= 5 and r.initial_delay and r.max_delay >= 30


def test_intent_gate_calls_the_configured_provider(tmp_path, monkeypatch):
    monkeypatch.delenv("GHOST_INTENT_PROVIDER", raising=False)
    monkeypatch.delenv("GHOST_INTENT_MODEL", raising=False)
    labels = {t: g for t, g, _, _ in CORPUS[:2]}
    calls = []
    intent.classify_all(list(labels), LLM(tmp_path, backend=gold_backend(labels, calls)))
    assert calls == [("gemini", "gemini-3.1-pro-preview")]
    monkeypatch.setenv("GHOST_INTENT_PROVIDER", "anthropic")
    calls.clear()
    intent.classify_all(list(labels), LLM(tmp_path, backend=gold_backend(labels, calls)))
    assert calls == [("anthropic", "claude-sonnet-5-5")]


def test_gemini_availability_needs_its_key(tmp_path, monkeypatch):
    monkeypatch.setenv("GHOST_GEMINI_BACKEND", "studio")
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert LLM(tmp_path).available("gemini") is False
    monkeypatch.setenv("GEMINI_API_KEY", "test-not-a-real-key")
    assert LLM(tmp_path).available("gemini") is True


# ------------------------------------------------------------------ live gate (needs a key)
def _live_key() -> bool:
    from agent.llm.client import load_dotenv
    load_dotenv(ROOT)
    from agent.llm import client as llm
    return bool(os.environ.get({"gemini": "GEMINI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
                                "openai": "OPENAI_API_KEY"}[llm.intent_provider()]))


@pytest.mark.skipif(not _live_key(), reason="no key for the configured intent provider")
def test_live_llm_gate_multilingual(tmp_path):
    texts = [t for t, _, _, _ in CORPUS] + [t for _, t, _ in dataset_texts()]
    gold = {t: g for t, g, _, _ in CORPUS} | {t: g for _, t, g in dataset_texts()}
    decisions, report = intent.classify_all(texts, LLM(tmp_path))
    assert report["llm_failures"] == 0
    wrong = [t for t in gold if decisions[t].authorizes_deletion is not gold[t]]
    dataset_wrong = [t for t in wrong if t not in {c[0] for c in CORPUS}]
    assert dataset_wrong == [], dataset_wrong
    assert len(wrong) <= 1, wrong   # allow one miss on the 41 hand-written multilingual cases


# ------------------------------------------------------------------ Gemini on Vertex AI (service account)
def test_vertex_backend_uses_project_location_and_adc(monkeypatch):
    from agent.llm import client
    monkeypatch.setenv("GHOST_GEMINI_BACKEND", "vertex")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj-123")
    monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
    assert client.gemini_client_kwargs() == {"vertexai": True, "project": "proj-123", "location": "global"}
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    assert client.gemini_client_kwargs()["location"] == "us-central1"


def test_studio_backend_is_the_default_and_uses_the_api_key(monkeypatch):
    from agent.llm import client
    monkeypatch.delenv("GHOST_GEMINI_BACKEND", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert client.gemini_client_kwargs() == {"api_key": "k"}


def test_vertex_availability_needs_credentials_and_project(tmp_path, monkeypatch):
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GHOST_GEMINI_BACKEND", "vertex")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "proj-123")
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    assert LLM(tmp_path).available("gemini") is False
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(tmp_path / "sa.json"))
    assert LLM(tmp_path).available("gemini") is True


# ------------------------------------------------------------------ graders rerun from the committed cache
def _no_credentials(monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY",
              "GHOST_GEMINI_BACKEND", "GOOGLE_APPLICATION_CREDENTIALS", "GOOGLE_CLOUD_PROJECT"):
        monkeypatch.delenv(k, raising=False)


def test_warm_cache_answers_without_any_credentials(tmp_path, monkeypatch):
    labels = {t: g for t, g, _, _ in CORPUS}
    intent.classify_all(list(labels), LLM(tmp_path, backend=gold_backend(labels, [])))   # someone filled the cache
    _no_credentials(monkeypatch)
    decisions, report = intent.classify_all(list(labels), LLM(tmp_path))                 # grader: no keys at all
    assert report["llm_failures"] == 0
    hard = [t for t, g, _, tier in CORPUS if tier == "hard"]
    assert all(decisions[t].basis in ("llm", "llm_override") for t in hard)


def test_cold_cache_without_credentials_falls_back_quietly(tmp_path, monkeypatch):
    _no_credentials(monkeypatch)
    decisions, report = intent.classify_all(["Please delete my account."], LLM(tmp_path))
    assert decisions["Please delete my account."].basis == "deterministic"
