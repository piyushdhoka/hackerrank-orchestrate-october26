# Ghost Data Still Exists: Deletion-Audit Agent

HackerRank Orchestrate, October 2026.

A deletion workflow can report **Complete** while a person's data still sits in replicas, search indexes, support
tickets, analytics, logs or backups, and sync or restore jobs can bring deleted data back. This agent audits every
request in `dataset/deletion_requests.csv` and decides, from the supplied evidence, whether the deletion is really
done:

- **`NOT_APPLICABLE`**: the request does not ask for deletion (access, correction, export, marketing opt-out, ...).
- **`COMPLETE`**: every copy has the treatment the policy requires. The output lists evidence-backed verification
  checks.
- **`INCOMPLETE`**: data or a recreation path remains. The output lists the findings, an ordered remediation plan,
  and the checks that must pass afterwards.

The result is written to `output.csv` (`case_id,deletion_status,steps_json`, 518 rows).

**Design principle:** deterministic Python does everything that can be computed exactly (parsing, identity, time,
policy, ordering). An LLM is used only where it adds something, as a **second gate** over multilingual request text
and application screenshots.

---

## 1. Setup

### Requirements

- Python 3.11 or newer
- The packages in `code/requirements.txt` (`google-genai`, `pypdf`, `pytest`)

### Install

From the repository root, with `dataset/` next to `code/`:

```bash
pip install -r code/requirements.txt
```

### LLM credentials

The LLM gates use Gemini (`gemini-3.1-pro-preview`) on Vertex AI. Put these in an uncommitted `.env` file at the
repository root:

```
GHOST_GEMINI_BACKEND=vertex
GOOGLE_APPLICATION_CREDENTIALS=<path to a service-account key file, outside the repo>
GOOGLE_CLOUD_PROJECT=<project id>
GOOGLE_CLOUD_LOCATION=global
```

`GEMINI_API_KEY=<key>` with the default backend also works. The model can be overridden with `GHOST_INTENT_MODEL`
and `GHOST_DOC_MODEL`. Credentials are read from the environment only and are never logged.

Without credentials, the pipeline still runs end to end: every LLM gate falls back to the deterministic decision.
Request intent is unaffected (the rules and the LLM agree on all 398 texts), but the 11 cases that depend on
screenshot evidence (case_34, 35, 39, 45, 46, 50, 52, 53, 55, 61, 63) cannot be resolved without the screenshot
gate, so reproducing the submitted `output.csv` exactly requires credentials.

---

## 2. Run

One command produces everything:

```bash
python code/main.py
```

| Output | Contents |
|---|---|
| `output.csv` | 518 rows sorted by case number, validated against the output contract before it is written |
| `code/evaluation/evaluation_report.md` | Scores on the public samples (all, dev, held-out, strict mode), custom cases, weak-attribution count, failures and fixes |
| `code/evaluation/usage_report.md` | Models, call counts, input/output tokens, cost, per-request averages for the run that produced `output.csv` |
| `runs/eval/traces/<case>.json` | Per-case trace: identifiers, every resource with its provenance and state |
| `runs/eval/llm_calls.jsonl` | Token ledger: one line per model call |

### Other commands

```bash
python code/main.py --no-llm                     # deterministic pass only
python code/main.py --attribution strict         # evidence-bound mode (section 3.4)
python code/main.py --requests sample            # audit the 32 public samples instead
python code/evaluation/evaluate.py --details     # score runs/sample/output.csv against dataset/sample_output.csv
python code/evaluation/check_submission.py       # contract checks on ./output.csv
python code/evaluation/recall_audit.py           # linked-but-unused evidence per case, classified
python -m pytest code/tests -q                   # test suite (141 tests)
```

---

## 3. Approach

### 3.1 Pipeline

```
deletion_requests.csv
        |
        v
 1. Identity graph ------ request email -> user_id -> every current and historical email, hash, phone
 2. Evidence index ------ 94 files (SQL, JSON, logs, text, PDF, PNG) -> one table of observations
 3. LLM gates ----------- intent of each request text; screenshot reading
 4. Scope --------------- which resources belong to this case (with provenance)
 5. State at audit_at --- latest admissible observation per copy
 6. Policy -------------- data category -> action from deletion_rules.csv
 7. Planner ------------- findings -> remediation -> verification, citations, ordering
 8. Validation ---------- contract checks; output.csv written only if all pass
```

1. **Identity graph** (`agent/identity.py`, `agent/normalize.py`). Each `user_id` is linked to every current and
   historical email, email hash and phone, with the source (`user_identities.csv` or the `audit_id` that retired
   it) and a validity window. Matching is exact after normalisation (case, `mailto:`, phone punctuation, `sha256:`
   prefixes, full-width and zero-width characters). Names are never used for linking. All 550 request emails
   resolve to exactly one user; 100 evaluation requests use a historical email.
2. **Evidence index** (`agent/evidence/`). Every file becomes *observations*: one statement about one resource at
   one moment from one source. There is one parser per format:
   - SQL snapshots executed in SQLite (inventory tables and primary/replica tables);
   - JSON snapshot bundles and service exports, using their inner snapshot times;
   - logs in six formats, with `*_started` and `*_completed` lines joined by trace id;
   - the text handover, PDF text layers, and data-catalog rows from `system_activity.csv`.

   Each observation keeps two clocks: the **record time** (inner snapshot, readback or log line) and the
   **manifest time** (file capture time in `evidence_files.csv`).
3. **LLM gates** (`agent/llm/`). One Gemini model handles both:
   - **Intent:** classifies each distinct request text (398 texts in 16 batched calls). A deterministic multilingual
     cue matcher decides first; the LLM wins only on a disagreement, which is recorded in the run report.
   - **Screenshots:** reads the 11 console screenshots record by record into structured JSON (resource, identifiers,
     fields, status). PDFs use their text layer, so they need no model.

   Calls are retried on transient errors; if a call still fails, the deterministic decision stands.
4. **Scope** (`agent/audit/scope.py`, `agent/audit/lineage.py`). Decides which resources belong to the case. Each
   one carries a provenance tag (section 3.4).
5. **State** (`agent/audit/state.py`). For each copy (primary or replica), the latest admissible observation in
   `[requested_at, audit_at]`. Deletion-worker results (SUCCESS / FAILED / PARTIAL) are claims, never state.
6. **Policy** (`agent/audit/policy.py`). The data category comes from an explicit label (handover, ledger,
   `record_type`, collection), else the worker's attempted operation, else the resource prefix. The category
   selects the required action from `deletion_rules.csv`.
7. **Planner** (`agent/audit/planner.py`). Builds the steps, picks citations and sorts them.
8. **Validation and reports** (`agent/reports/`). Contract checks, scoring, and both Markdown reports.

### 3.2 Core rules

| Topic | Rule |
|---|---|
| Linking | A resource belongs to the case if a record dated inside `[requested_at, audit_at]` contains one of the subject's identifiers (exact, normalised) |
| Time | Content is usable if its record time is at or before `audit_at`. A file can be **cited** only if its manifest capture time is at or before `audit_at` |
| State | Present on any copy means present, so replica drift is a finding. Absence is a 404 / NOT_FOUND / zero rows, or omission from a full snapshot of that system |
| Recreation paths | An enabled connector is an active data flow. A backup with no observed restore block is a restore risk. `RESTORE_BACKUP` / `SYNC_RECORD` rows and `source_resource_id` fields link downstream copies to the case |
| Category to action | Profile, search, cache, export, marketing, support attachment, secondary identifier: **delete**. Support message, email delivery log, account tombstone: **redact** (a tombstone keeps only `user_id`). Analytics, telemetry: **anonymize**. Security audit, legal hold: **retain** and verify present. Backup: **block restore**. Connector: **disable** |
| Status | `NOT_APPLICABLE` if the text does not authorise deletion; otherwise `INCOMPLETE` if there is at least one finding, else `COMPLETE` |
| Steps | `COMPLETE`: verifications only, each citing the latest admissible file that shows the compliant state. `INCOMPLETE`: cited findings, then remediation, then verification of every in-scope resource (both with `evidence_id: ""`) |
| Order | Sort by (position of the operation in the operation table, `system_id`, `resource_id`), then number from 1. Flows are disabled and restores blocked before records change |

### 3.3 Attributing records that no identifier names

Some primary accounts appear only in a deletion-worker row with no identifier. Within the worker batch for the case
(`reported_at` plus or minus 2 hours), they are matched in this order:

1. read back in the same evidence file as the case's identifier-linked records;
2. the workflow's `reported_status` mirrors the account's delete result (COMPLETE/SUCCESS, PARTIALLY_COMPLETE/PARTIAL,
   NOT_STARTED/FAILED);
3. numeric case order, as a last resort.

These links are inference. They are only ever used for **verification** steps, never for findings, and they are
counted in the evaluation report (237 of 3,258 steps, 7.3%).

### 3.4 Modes and provenance

- **Gold-compatible (default):** reproduces the public answers, including their inferred links.
- **Strict (`--attribution strict`):** uses only evidence-proven links (sample precision 1.0, step F1 0.90).
- Every step records its provenance in the case trace: `identifier_link`, `catalog_link`, `lineage_*`,
  `inner_time_link`, `owner_batch`, `batch_attribution` with `batch_pass:*`, `assumed_state`, `omission_citation`.
  A test checks over all 518 traces that inferred links never produce findings.

---

## 4. Evaluation

Full details are in `code/evaluation/evaluation_report.md`.

| | Status | Exact cases | Step F1 |
|---|---|---|---|
| Public samples, all 32 | 100% | 28/32 | 0.987 |
| Dev split (20) | 100% | 19/20 | 0.992 |
| Held-out split (12) | 100% | 9/12 | 0.976 |
| Strict mode, all 32 | 100% | 9/32 | 0.900 |
| Custom cases (7, hand-derived) | 100% | 7/7 | 1.000 |

- Sequence (pairwise order) accuracy is 100% on the samples. The held-out split is an upper bound, because the rules
  were fitted on all 32 samples before the split was frozen.
- Three of the four sample misses are resources that appear in no participant file; the fourth is a tie between two
  unlabelled accounts in one batch.
- **Independent check:** Codex solved five evaluation cases from raw data, one per evidence era, without access to
  this code. Every identifier-grounded step matched; the only differences were our inferred verification steps.
- **Recall audit:** every resource linked to an evaluation subject but missing from that case's steps was
  classified (36 pairs). All were correct exclusions (captured after `audit_at`, owned by the person's other case,
  or outside the request window).
- **Final output:** 153 COMPLETE, 355 INCOMPLETE, 10 NOT_APPLICABLE; 3,258 steps; all contract checks pass; two
  runs produce byte-identical output.

### Token usage

The final full run made 27 calls to `gemini-3.1-pro-preview` on Vertex AI (16 intent batches, 11 screenshots):
82,111 tokens (158.5 per request), estimated $0.61 in total and $0.0012 per request. Details are in
`code/evaluation/usage_report.md`; prices are in `code/config/pricing.json`.

---

## 5. Project structure

```
README.md
code/
  main.py                  entry point: output.csv, traces, token ledger, both reports
  requirements.txt
  config/pricing.json      per-model prices for the cost estimate
  agent/
    data.py                CSV loading
    identity.py            identity graph
    normalize.py           identifier normalisation
    utils.py               timestamps and resource conventions
    pipeline.py            extract -> LLM gates -> scope -> state -> plan
    evidence/              observation.py, extractor.py, catalog.py, sql/json/log/pdf/handover parsers
    audit/                 scope.py, lineage.py, state.py, policy.py, planner.py
    llm/                   client.py (Gemini/Vertex, token ledger), intent.py, documents.py (prompts)
    reports/               validation.py, scoring.py, attribution.py, usage.py, evaluation.py
  evaluation/
    evaluation_report.md   accuracy, custom cases, failures and fixes
    usage_report.md        token and cost report for the final run
    evaluate.py            local scorer against the public samples
    check_submission.py    output contract checks
    recall_audit.py        linked-but-unused evidence audit
    splits.py              frozen dev / held-out split
    custom_cases.py        hand-derived evaluation cases
  tests/                   pytest suite
```
