"""LLM access for the second-gate checks: disk cache + token ledger, two providers.

* intent gate (multilingual request text): a lighter Gemini model (GHOST_INTENT_PROVIDER / GHOST_INTENT_MODEL)
* document gate (screenshots / text-less PDFs): a stronger Gemini model (GHOST_DOC_PROVIDER / GHOST_DOC_MODEL),
  with an optional GHOST_DOC_FALLBACK_MODEL used only if the primary model returns an error
Default model ids are in DEFAULT_MODELS. The final run uses Gemini on Vertex AI (GHOST_GEMINI_BACKEND=vertex).

Keys come only from the environment (GEMINI_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY, optionally via an
uncommitted .env). Without a key the gate reports itself unavailable and the deterministic first
pass stands. Responses are cached by sha256(provider, model, prompt version, payload) so a rerun is
byte-identical and free.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_MODELS = {
    "intent": {"gemini": "gemini-3.1-pro-preview", "anthropic": "claude-sonnet-5-5", "openai": "gpt-sol"},
    "document": {"gemini": "gemini-3.1-pro-preview", "anthropic": "claude-opus-5-5", "openai": "gpt-sol"},
}
KEY_VARS = {"gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "anthropic": ("ANTHROPIC_API_KEY",),
            "openai": ("OPENAI_API_KEY",)}


def gemini_client_kwargs() -> dict:
    """Gemini via Google AI Studio (API key, default) or Vertex AI (GHOST_GEMINI_BACKEND=vertex: service-account
    credentials from GOOGLE_APPLICATION_CREDENTIALS, project GOOGLE_CLOUD_PROJECT, location GOOGLE_CLOUD_LOCATION)."""
    if os.environ.get("GHOST_GEMINI_BACKEND", "studio").lower() == "vertex":
        return {"vertexai": True, "project": os.environ.get("GOOGLE_CLOUD_PROJECT"),
                "location": os.environ.get("GOOGLE_CLOUD_LOCATION", "global")}
    return {"api_key": os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")}


def intent_provider() -> str:
    return os.environ.get("GHOST_INTENT_PROVIDER", "gemini")


def intent_model() -> str:
    return os.environ.get("GHOST_INTENT_MODEL", DEFAULT_MODELS["intent"][intent_provider()])


def doc_provider() -> str:
    return os.environ.get("GHOST_DOC_PROVIDER", "gemini")


def doc_model() -> str:
    return os.environ.get("GHOST_DOC_MODEL", DEFAULT_MODELS["document"][doc_provider()])


def doc_models() -> list[str]:
    """The document model, plus a fallback model only if GHOST_DOC_FALLBACK_MODEL is explicitly set. By default a
    failed read (after retries) falls back to the deterministic pass, never to another model."""
    fallback = os.environ.get("GHOST_DOC_FALLBACK_MODEL")
    return [doc_model()] + ([fallback] if fallback and fallback != doc_model() else [])


def gemini_retry_options():
    """Transient Vertex errors (429 'resource exhausted, try again later', 5xx) are retried with exponential backoff."""
    from google.genai import types
    return types.HttpRetryOptions(attempts=6, initial_delay=2.0, max_delay=60.0)


def load_dotenv(root: Path) -> None:
    env = root / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$", line)
        if m and m.group(1) not in os.environ:
            os.environ[m.group(1)] = m.group(2).strip().strip('"').strip("'")


@dataclass
class Ledger:
    calls: list[dict] = field(default_factory=list)

    def add(self, **kw) -> None:
        self.calls.append(kw)

    def totals(self) -> dict:
        out: dict = {}
        for c in self.calls:
            key = f"{c['provider']}:{c['model']}"
            t = out.setdefault(key, {"calls": 0, "cached": 0, "input_tokens": 0, "output_tokens": 0})
            t["cached" if c["cached"] else "calls"] += 1
            if not c["cached"]:
                t["input_tokens"] += c.get("input_tokens", 0)
                t["output_tokens"] += c.get("output_tokens", 0)
        return out


class LLM:
    """Small provider wrapper. ``backend`` can be injected for tests: callable(provider, model, payload) ->
    (text, input_tokens, output_tokens)."""

    def __init__(self, cache_dir: Path, enabled: bool = True, backend=None):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.enabled = enabled
        self.backend = backend
        self.ledger = Ledger()

    def available(self, provider: str) -> bool:
        if not self.enabled:
            return False
        if self.backend is not None:
            return True
        if provider == "gemini" and gemini_client_kwargs().get("vertexai"):
            return bool(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") and os.environ.get("GOOGLE_CLOUD_PROJECT"))
        return any(os.environ.get(k) for k in KEY_VARS.get(provider, ()))

    def _key(self, provider: str, model: str, version: str, payload) -> str:
        blob = json.dumps({"p": provider, "m": model, "v": version, "x": payload}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def call(self, provider: str, model: str, version: str, payload: dict, purpose: str) -> str | None:
        # documents are keyed by content hash ('sha'), never by their machine-specific path
        key = self._key(provider, model, version, {k: v for k, v in payload.items() if k != "path"})
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            cached = json.loads(path.read_text(encoding="utf-8"))
            # keep what the response originally cost so reports can show replayed tokens
            self.ledger.add(provider=provider, model=model, purpose=purpose, cached=True,
                            input_tokens=cached.get("input_tokens", 0), output_tokens=cached.get("output_tokens", 0))
            return cached["text"]
        if not self.available(provider):
            return None
        started = time.time()
        try:
            if self.backend is not None:
                text, tin, tout = self.backend(provider, model, payload)
            elif provider == "gemini":
                text, tin, tout = self._gemini(model, payload)
            elif provider == "anthropic":
                text, tin, tout = self._anthropic(model, payload)
            else:
                text, tin, tout = self._openai(model, payload)
        except Exception as exc:  # noqa: BLE001 - quota/network errors degrade to the deterministic pass
            self.ledger.add(provider=provider, model=model, purpose=purpose, cached=False, input_tokens=0,
                            output_tokens=0, seconds=round(time.time() - started, 2), error=str(exc)[:300])
            return None
        self.ledger.add(provider=provider, model=model, purpose=purpose, cached=False, input_tokens=tin,
                        output_tokens=tout, seconds=round(time.time() - started, 2))
        path.write_text(json.dumps({"provider": provider, "model": model, "version": version, "purpose": purpose,
                                    "text": text, "input_tokens": tin, "output_tokens": tout},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
        return text

    def call_with_fallback(self, provider: str, models: list[str], version: str, payload: dict, purpose: str):
        for model in models:
            text = self.call(provider, model, version, payload, purpose)
            if text is not None:
                return text
        return None

    # ------------------------------------------------------------------ providers
    @staticmethod
    def _gemini(model: str, payload: dict):
        from google import genai
        from google.genai import types
        client = genai.Client(**gemini_client_kwargs(),
                              http_options=types.HttpOptions(retry_options=gemini_retry_options()))
        if "path" in payload:  # document gate: the file itself + extraction instructions
            contents = [types.Part.from_bytes(data=Path(payload["path"]).read_bytes(), mime_type=payload["mime"]),
                        payload["prompt"]]
            config = types.GenerateContentConfig(response_mime_type="application/json")
        else:
            contents = payload["user"]
            config = types.GenerateContentConfig(system_instruction=payload["system"],
                                                 response_mime_type="application/json")
        resp = client.models.generate_content(model=model, contents=contents, config=config)
        u = resp.usage_metadata
        tin = (u.prompt_token_count or 0) if u else 0
        # thinking tokens are billed as output
        tout = ((u.candidates_token_count or 0) + (getattr(u, "thoughts_token_count", 0) or 0)) if u else 0
        return resp.text or "", tin, tout

    @staticmethod
    def _anthropic(model: str, payload: dict):
        from anthropic import Anthropic
        client = Anthropic()
        if "path" in payload:  # document gate on Claude: image or PDF block + instructions
            data = base64.b64encode(Path(payload["path"]).read_bytes()).decode("ascii")
            kind = "document" if payload["mime"] == "application/pdf" else "image"
            content = [{"type": kind, "source": {"type": "base64", "media_type": payload["mime"], "data": data}},
                       {"type": "text", "text": payload["prompt"]}]
            system = "Return only JSON."
        else:
            content, system = payload["user"], payload["system"]
        resp = client.messages.create(model=model, max_tokens=payload.get("max_tokens", 4000),
                                      system=system, messages=[{"role": "user", "content": content}])
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return text, resp.usage.input_tokens, resp.usage.output_tokens

    @staticmethod
    def _openai(model: str, payload: dict):
        from openai import OpenAI
        client = OpenAI()
        content = [{"type": "text", "text": payload["prompt"]}]
        data = base64.b64encode(Path(payload["path"]).read_bytes()).decode("ascii")
        if payload["mime"] == "application/pdf":
            content.append({"type": "file", "file": {"filename": Path(payload["path"]).name,
                                                     "file_data": f"data:application/pdf;base64,{data}"}})
        else:
            content.append({"type": "image_url", "image_url": {"url": f"data:{payload['mime']};base64,{data}"}})
        resp = client.chat.completions.create(model=model, messages=[{"role": "user", "content": content}],
                                              response_format={"type": "json_object"})
        return (resp.choices[0].message.content or "", resp.usage.prompt_tokens, resp.usage.completion_tokens)


def extract_json(text: str | None):
    """Parse the first JSON object/array in a model reply; None if there isn't a valid one."""
    if not text:
        return None
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = text.find(opener), text.rfind(closer)
        if i != -1 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except json.JSONDecodeError:
                continue
    return None
