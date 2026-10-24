"""Does the request authorise deletion? (README §3.2: NOT_APPLICABLE)

First pass: deterministic multilingual cue matching (EN/FR/ES/PT/DE/IT/JA/KO/ZH/AR...).
Second gate: a cheap LLM reads every distinct text (batched, cached). Language understanding is the
LLM's job, so on disagreement the LLM decision wins; both are kept in the trace and the run report.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass

from .client import LLM, extract_json, intent_model, intent_provider

PROMPT_VERSION = "intent-v1"
BATCH = 25

# Explicit refusals of deletion, or "keep my account" statements. Any hit => not a deletion request.
NEGATIONS = [
    r"not asking for (?:account )?deletion", r"not a request to (?:erase|delete|remove)",
    r"\b(?:do not|don't|dont|please don't|never) (?:delete|close|remove|erase|deactivate)",
    r"without (?:removing|deleting|erasing)", r"leave (?:the|my) (?:account|profile)",
    r"keep (?:the|my) (?:account|profile)", r"continue using", r"\bi still (?:want|need|use)\b",
    r"(?:marketing|promotional)[^.]*\bonly\b", r"\bonly\b[^.]*(?:marketing|promotional)",
    r"ne (?:supprimez|fermez|effacez) pas", r"ne pas (?:supprimer|fermer|effacer)", r"sans supprimer", r"garder (?:mon|le) compte",
    r"no (?:eliminen|borren|cierren|quiero eliminar)", r"sin (?:eliminar|borrar)", r"mantener (?:mi|la) cuenta",
    r"não (?:excluam|apaguem|eliminem|quero excluir)", r"sem (?:excluir|apagar)", r"manter (?:a|minha) conta",
    r"nicht (?:löschen|entfernen)", r"ohne (?:zu )?löschen", r"non (?:cancellate|eliminate)",
    r"削除しないで", r"削除はしないで", r"삭제하지\s*(?:말|마)", r"不要删除", r"请勿删除", r"لا (?:تحذف|تحذفوا)",
    r"niet verwijderen", r"nie usuwaj", r"silmeyin", r"не удаляйте", r"radera inte",
]
# Requests for a different privacy/service action. Only decisive when no deletion cue is present.
OTHER_ACTIONS = [
    r"\bcopy of\b", r"\bdownload\b", r"portable format", r"what personal (?:information|data)",
    r"information request", r"\bcorrect\b", r"misspelled", r"\bupdate\b", r"\breplace\b", r"change the email",
    r"reset(?:ting)? the login", r"can't access", r"unsubscribe", r"marketing", r"promotional",
    r"copie de mes données", r"corriger", r"copia de mis datos", r"corregir", r"cópia dos meus dados", r"corrigir",
    r"kopie meiner daten", r"コピー", r"変更", r"사본", r"변경", r"副本", r"更改", r"نسخة",
]
DELETION = [
    r"\bdelet(?:e|ed|es|ing|ion)\b", r"\berase[ds]?\b|\berasure\b", r"\bremov(?:e|ed|al)\b", r"\bpurge\b",
    r"\bwipe\b", r"\bclose (?:my|this|the) account\b", r"account[- ]deletion", r"right to be forgotten",
    r"no longer (?:need|use|want) (?:this|my|the) account", r"\bis gone\b", r"cannot identify me",
    r"suppression", r"supprim", r"effac", r"eliminaci[oó]n", r"eliminar", r"borrar", r"supresi[oó]n",
    r"exclus[aã]o", r"excluir", r"apagar", r"elimina[cç][aã]o", r"l[öo]sch", r"cancellazione", r"cancellare",
    r"削除", r"消去", r"退会", r"삭제", r"탈퇴", r"删除", r"注销", r"حذف", r"मिटा", r"हटा",
    r"verwijder", r"usunię", r"usuń", r"\bsil(?:in|inmesi|me)", r"удал", r"\bradera", r"\bslett", r"\bslet\b",
]


@dataclass
class IntentDecision:
    authorizes_deletion: bool
    label: str
    basis: str                 # 'deterministic' | 'llm' | 'llm_override'
    deterministic: dict
    llm: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _hits(patterns: list[str], text: str) -> list[str]:
    return [p for p in patterns if re.search(p, text, re.I)]


def deterministic(text: str) -> dict:
    neg, other, dele = _hits(NEGATIONS, text), _hits(OTHER_ACTIONS, text), _hits(DELETION, text)
    if neg:
        return {"authorizes_deletion": False, "label": "non_deletion_explicit", "confidence": "high", "cues": neg[:3]}
    if dele and not other:
        return {"authorizes_deletion": True, "label": "deletion", "confidence": "high", "cues": dele[:3]}
    if dele and other:
        return {"authorizes_deletion": True, "label": "deletion_mixed", "confidence": "low", "cues": dele[:2] + other[:2]}
    if other:
        return {"authorizes_deletion": False, "label": "other_request", "confidence": "medium", "cues": other[:3]}
    return {"authorizes_deletion": True, "label": "no_cue_default", "confidence": "low", "cues": []}


SYSTEM = """You classify customer privacy requests sent to an online shop. Texts may be in any language.
Decide whether each text authorises DELETION of the person's account or personal data. Count as deletion:
explicit erase/delete/close-account requests, and follow-ups asking to verify, audit or finish a deletion
that was already requested. NOT deletion: access or copy requests, data portability/export, corrections or
changes to profile data, login help, marketing or promotional opt-outs, or any text that says not to
delete or to keep the account. Reply with JSON only."""

USER_TEMPLATE = """Classify each request. Return {{"results":[{{"id":<id>,"authorizes_deletion":true|false,
"intent":"deletion|deletion_followup|access|portability|correction|marketing_opt_out|account_support|other",
"language":"<ISO 639-1>","reason":"<10 words max>"}}]}} with one entry per id.

{items}"""


def _text_key(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def classify_all(texts: list[str], llm: LLM | None) -> tuple[dict[str, IntentDecision], dict]:
    """Classify distinct texts. Returns decisions keyed by text and a gate report."""
    distinct = sorted(set(texts))
    det = {t: deterministic(t) for t in distinct}
    llm_out: dict[str, dict] = {}
    report = {"distinct_texts": len(distinct), "llm_available": False, "llm_batches": 0,
              "llm_failures": 0, "disagreements": []}
    provider = intent_provider()
    # consult the gate whenever it is enabled: cached answers replay without credentials (graders rerun)
    if llm is not None and llm.enabled:
        report["llm_available"] = llm.available(provider)
        model = intent_model()
        for i in range(0, len(distinct), BATCH):
            chunk = distinct[i:i + BATCH]
            items = "\n".join(json.dumps({"id": n, "text": t}, ensure_ascii=False) for n, t in enumerate(chunk))
            payload = {"system": SYSTEM, "user": USER_TEMPLATE.format(items=items), "max_tokens": 4000}
            reply = llm.call(provider, model, PROMPT_VERSION, payload, purpose="intent")
            report["llm_batches"] += 1
            parsed = extract_json(reply)
            results = parsed.get("results", []) if isinstance(parsed, dict) else []
            got = {r.get("id"): r for r in results if isinstance(r, dict)}
            for n, t in enumerate(chunk):
                r = got.get(n)
                if r is None or not isinstance(r.get("authorizes_deletion"), bool):
                    report["llm_failures"] += 1
                    continue
                llm_out[t] = r
    report["llm_available"] = report["llm_available"] or bool(llm_out)
    decisions: dict[str, IntentDecision] = {}
    for t in distinct:
        d, l = det[t], llm_out.get(t)
        if l is None:
            decisions[t] = IntentDecision(d["authorizes_deletion"], d["label"], "deterministic", d)
        elif l["authorizes_deletion"] == d["authorizes_deletion"]:
            decisions[t] = IntentDecision(l["authorizes_deletion"], l.get("intent", d["label"]), "llm", d, l)
        else:
            decisions[t] = IntentDecision(l["authorizes_deletion"], l.get("intent", "?"), "llm_override", d, l)
            report["disagreements"].append({"text_key": _text_key(t), "text": t[:160],
                                            "deterministic": d["label"], "llm": l.get("intent")})
    return decisions, report
