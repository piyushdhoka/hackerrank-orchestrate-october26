# Evaluation report

Scores are produced by `python code/main.py`. The section between the markers is regenerated on every run;
the rest of this file is written by hand.

<!-- AUTO-GENERATED:START -->
## Scores

- **Mode:** gold-compatible
- **LLM gates:** on
- **Intent gate:** gemini:gemini-3.1-pro-preview (LLM answered 398 of 398 distinct texts; 0 fell back to the deterministic pass)
- **Document gate:** gemini:gemini-3.1-pro-preview (read 11 of 11 files)

| Metric | all 32 (gold-compatible) | dev (20) | held-out (12) | all 32 (strict) |
|---|---:|---:|---:|---:|
| Cases | 32 | 20 | 12 | 32 |
| Status accuracy (case-level) | 100.0% | 100.0% | 100.0% | 100.0% |
| Exact case match (status + full steps_json) | 87.5% | 95.0% | 75.0% | 28.1% |
| Step precision | 99.5% | 99.2% | 100.0% | 100.0% |
| Step recall | 97.9% | 99.2% | 95.4% | 81.9% |
| Step F1 | 98.7% | 99.2% | 97.6% | 90.0% |
| Step F1 incl. evidence_id | 98.7% | 99.2% | 97.6% | 90.0% |
| Field accuracy: operation | 97.9% | 99.2% | 95.4% | 81.9% |
| Field accuracy: state | 97.9% | 99.2% | 95.4% | 81.9% |
| Field accuracy: evidence_id | 97.9% | 99.2% | 95.4% | 81.9% |
| Field accuracy: step_no | 95.3% | 99.2% | 87.7% | 60.1% |
| Sequence: pairwise order accuracy | 100.0% | 100.0% | 100.0% | 100.0% |

### Remaining failures (all 32 (gold-compatible))

- `case_04`: missing_resource:account_004
- `case_18`: missing_resource:audit_record_6645
- `case_20`: missing_resource:audit_record_5569
- `case_23`: missing_resource:account_410; extra_resource:account_7015

### Custom evaluation cases (scored automatically)

- Cases: 7; status accuracy 100.0%; exact match 100.0%; step F1 100.0%

### Weak attribution (eval output)

- **237 of 3258 steps (7.3%)** in 237 of 518 cases rely on weak attribution: the resource was tied to the case only by reported_status matching or a numeric case-order tie-break.
- Batch-attribution passes (steps): coobserved 118, coobserved+status 21, status 237

Held-out caveat: the split was frozen after the attribution and citation rules had been reverse-engineered on all 32 samples, so the held-out column is an upper bound, not a clean generalisation estimate. Rule changes from now on are justified on dev only.

Field accuracy is computed on steps aligned by (phase, system_id, resource_id); step precision/recall count missing and extra steps, so omissions cannot hide behind aligned-field scores.
<!-- AUTO-GENERATED:END -->

## How we evaluate

- **Public samples (32):** scored against `dataset/sample_output.csv` on status, step precision/recall/F1 (with and
  without `evidence_id`), field accuracy on aligned steps, and sequence (pairwise order). Scored in both modes:
  gold-compatible (default) and `--attribution strict`.
- **Dev / held-out split:** `code/evaluation/splits.py` (seeded, stratified by status, 20 / 12), frozen on
  2026-10-24. **Caveat:** the attribution and citation rules were reverse-engineered on all 32 samples *before* the
  split was frozen, so the held-out column is an upper bound, not a clean generalisation estimate. From that date,
  rule changes are justified on dev only.
- **Custom cases (7, hand-derived from raw evidence):** `code/evaluation/custom_cases.py`, scored automatically
  against the eval run.
- **Adversarial independent solve:** Codex solved five eval cases from raw data without access to our code, output
  or design (see below).
- **Weak attribution:** the auto section counts steps whose resource is tied to the case only by reported_status
  matching or a numeric case-order tie-break.

## Custom evaluation cases

| Case | Era / evidence | What it tests | Expected |
|---|---|---|---|
| case_33 | Jul, PDF statement + catalog alias | PDF text layer; statement number `NST-2026-0718-1842` → `export_1842` | INCOMPLETE: remaining export |
| case_44 | Sep, readback log (ev94) | Batch shared with sample case_03; all six records compliant | COMPLETE, all cited to ev94 |
| case_54 | Sep, read-replica SQL (ev05), Portuguese | Uppercase request email; capture time = audit_at (inclusive) | INCOMPLETE: 4 findings + 4 remediations |
| case_417 | Mar, syslog archive (ev39), Korean | `match_key` lookup on a resource-less line; FAILED claim but account present | INCOMPLETE: account + search remaining |
| case_501 | Feb, inventory SQL (ev63) | Uppercase email; SUCCESS claim but record still `DELETION_PENDING` | INCOMPLETE: account + search remaining |
| case_511 | Feb, reporting replica (ev47) | Replica-only account (drift), historical email, LEGAL_HOLD retained | INCOMPLETE: replica account; verify legal hold + audit present |
| case_533 | eval, request text only | Correction request is not a deletion | NOT_APPLICABLE, `[]` |

Each case's evidence walk-through is in the `why` field of `custom_cases.py`. Two steps rest on our own heuristics
rather than on identifier evidence, so they are not independent confirmation: `analytics_5589` in case_44 and
`account_6829` in case_54.

## Adversarial check: Codex vs our output (five eval cases from different evidence eras)

Codex (independent harness; no access to `code/`, `runs/`, `output.csv` or the design notes) solved case_526 (Jan bundle +
export), case_404 (Mar regional replicas), case_302 (May inventory SQL), case_209 (Jul read replica) and case_90
(Sep search diagnostic log).

- **All five statuses agree** (INCOMPLETE). **Every finding, remediation, citation and the step order agree** on all
  shared steps; case_90 matches step for step (14/14).
- **Every disagreement is one of our inferred steps:** four primary accounts we attach through batch attribution
  (`batch_pass:status`, cases 526/404/302/209), which Codex declined to infer, and one audit record in case_526 that we
  discover through a snapshot dated before audit_at inside a file captured after it (`inner_time_link`), which Codex
  excluded. These are exactly the gold-compatible vs strict choices documented in README §3.3; Codex found no error
  in identifier-grounded steps.

## Failures found and improvements made

| # | Failure | How it was found | Fix |
|---|---|---|---|
| 1 | A trace's `cache_lookup result=miss` line made an audit record look absent (sample case_15) | Sample diff | Resource-less log lines contribute lookup context only, never state |
| 2 | Orphan accounts handed to the wrong case in shared batches (cases 21–23) | Sample diff | Matching passes: co-observation + reported_status ↔ delete result, then case order |
| 3 | COMPLETE step emitted without evidence (case_274 `legal_hold_1745`) | New output validator refused to write output.csv | Uncitable COMPLETE targets are dropped and noted in the trace |
| 4 | `match_key` / `owner_key` lookups ignored, so 30 eval cases missed search hits; orphan logic then mis-assigned those hits to other cases | Hand-deriving custom case_417 | Any field whose value holds an identifier carries over within a trace |
| 5 | Screenshot identifiers not treated as exposure; display ids (`user_17`) and file names (`delivery_confirmation_19.pdf`) not mapped | Hand check of the Gemini document reads | Identifiers become explicit fields; catalog display ids/aliases resolve rows; prompt v2 asks for records without ids |
| 6 | Screenshot row dates (e.g. "created 26 Apr") used as observation time | Live vision smoke test | Artifacts are observed at capture time |
| 7 | Provider errors (429 quota) crashed the run | Live Gemini smoke test | Errors are logged in the ledger and the deterministic decision stands |
| 8 | French infinitive negation ("ne pas supprimer") read as deletion | Multilingual intent tests | Negation pattern added; LLM gate remains the authority |
| 9 | Non-account orphans silently labelled as status-matched | Attribution audit tests | reported_status matching applies to primary accounts only |
| 10 | Scoring crashed on an empty case set | Test of the report renderer | Guarded |
| 11 | Reports overstated LLM use when every provider call failed (quota errors) | Reading the run ledger | Gate status line counts answered vs fallen-back texts; usage table has a failed-calls column; Gemini runs on Vertex AI |
| 12 | The document model invented ids `object_1`…`object_8` for unlabeled storage rows | Hand check of the document reads (case_61) | A catalog alias printed in the row beats a model-supplied id; prompt v3 forbids invented ids |
| 13 | Page-header records emitted only as `other_records` (support ticket CS-5034, registry user_17) | Hand check (case_53, case_46) | Prompt v3 requires the header/detail record as a row |
| 14 | Connector shown as "Healthy" read as unknown; present backup with no restore control assumed blocked; omission snapshots cited for non-absence states | Hand check (case_39, case_55) + new citation invariant test | Status words read for connectors; a present archive is restorable unless a block is observed (affects case_55 only); omission citations limited to absence |
| 15 | Normaliser couldn't recognise a hash of a known email that the audit log never recorded, or a phone field written without `+` (neither occurs in this data) | Recall audit + near-match identifier scan | Hashes derived for every known email; phone-typed fields read without `+`; lookalike emails stay distinct (tests). Dev unchanged, output unchanged |

Remaining known misses on the samples are the three expected resources that appear in no participant file
(`account_004`, `audit_record_6645`, `audit_record_5569`) and the case_23 tie (two FAILED orphan accounts for three
NOT_STARTED cases in one batch).
