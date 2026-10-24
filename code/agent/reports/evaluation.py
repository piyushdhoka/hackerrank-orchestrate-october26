"""evaluation_report.md: an auto-generated metrics section between markers; everything else is hand-written
and preserved across runs."""
from __future__ import annotations

from pathlib import Path

START = "<!-- AUTO-GENERATED:START -->"
END = "<!-- AUTO-GENERATED:END -->"

TEMPLATE = f"""# Evaluation report

Scores below are produced by `python code/main.py` (section between the markers is regenerated on every run;
the rest of this file is written by hand).

{START}
{END}

## Custom evaluation cases

_Hand-written: describe each participant-created case, why it was chosen and its expected answer._

## Failures found and improvements made

_Hand-written: what broke, how it was found, what changed._
"""


def update_report(path: Path, section_md: str) -> None:
    path = Path(path)
    text = path.read_text(encoding="utf-8") if path.exists() else TEMPLATE
    if START not in text or END not in text:
        text = text.rstrip() + "\n\n" + START + "\n" + END + "\n"
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{head}{START}\n{section_md.strip()}\n{END}{tail}", encoding="utf-8")


def gate_status(intent_report: dict) -> str:
    """What the intent gate actually did, not just whether it was configured."""
    n = intent_report.get("distinct_texts", 0)
    if not intent_report.get("llm_available"):
        return f"unavailable: all {n} texts decided by the deterministic pass"
    ok = n - intent_report.get("llm_failures", 0)
    return f"LLM answered {ok} of {n} distinct texts; {n - ok} fell back to the deterministic pass"


def _pct(v) -> str:
    return f"{100 * v:.1f}%"


def render_metrics(results: dict[str, dict], custom: dict | None = None, meta: dict | None = None,
                   attribution: dict | None = None, extra: list[str] | None = None) -> str:
    lines = ["## Scores", ""]
    if meta:
        lines += [f"- **{k}:** {v}" for k, v in meta.items()] + [""]
    names = list(results)
    lines += ["| Metric | " + " | ".join(names) + " |", "|---|" + "---:|" * len(names)]
    rows = [
        ("Cases", lambda r: str(r["cases"])),
        ("Status accuracy (case-level)", lambda r: _pct(r["status_accuracy"])),
        ("Exact case match (status + full steps_json)", lambda r: _pct(r["exact_case_match"])),
        ("Step precision", lambda r: _pct(r["steps"]["precision"])),
        ("Step recall", lambda r: _pct(r["steps"]["recall"])),
        ("Step F1", lambda r: _pct(r["steps"]["f1"])),
        ("Step F1 incl. evidence_id", lambda r: _pct(r["steps_with_evidence"]["f1"])),
        ("Field accuracy: operation", lambda r: _pct(r["field_accuracy"].get("operation", 0))),
        ("Field accuracy: state", lambda r: _pct(r["field_accuracy"].get("state", 0))),
        ("Field accuracy: evidence_id", lambda r: _pct(r["field_accuracy"].get("evidence_id", 0))),
        ("Field accuracy: step_no", lambda r: _pct(r["field_accuracy"].get("step_no", 0))),
        ("Sequence: pairwise order accuracy", lambda r: _pct(r["pairwise_order_accuracy"])),
    ]
    for label, fn in rows:
        lines.append(f"| {label} | " + " | ".join(fn(results[n]) for n in names) + " |")
    first = results[names[0]]
    lines += ["", f"### Remaining failures ({names[0]})", ""]
    lines += [f"- `{cid}`: " + "; ".join(fs) for cid, fs in first["failures"].items()] or ["- none"]
    if custom is not None:
        lines += ["", "### Custom evaluation cases (scored automatically)", "",
                  f"- Cases: {custom['cases']}; status accuracy {_pct(custom['status_accuracy'])}; "
                  f"exact match {_pct(custom['exact_case_match'])}; step F1 {_pct(custom['steps']['f1'])}"]
        lines += [f"- `{cid}`: " + "; ".join(fs) for cid, fs in custom["failures"].items()]
    if attribution is not None:
        a = attribution
        share = a["weak_steps"] / a["steps"] if a["steps"] else 0.0
        lines += ["", "### Weak attribution (eval output)", "",
                  f"- **{a['weak_steps']} of {a['steps']} steps ({_pct(share)})** in {a['cases_with_weak_steps']} of "
                  f"{a['cases']} cases rely on weak attribution: the resource was tied to the case only by "
                  "reported_status matching or a numeric case-order tie-break.",
                  "- Batch-attribution passes (steps): " + (", ".join(f"{k} {v}" for k, v in sorted(a["by_pass"].items())) or "none")]
    if extra:
        lines += [""] + extra
    lines += ["", "Field accuracy is computed on steps aligned by (phase, system_id, resource_id); step precision/recall "
              "count missing and extra steps, so omissions cannot hide behind aligned-field scores."]
    return "\n".join(lines)
