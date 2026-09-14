"""Duenner Anthropic-Wrapper: ein Aufruf, ein JSON-Objekt nach Schema.

Modell ist Haiku (im Plan so abgestimmt: viele kleine Urteile, Cent-Betraege
pro Tag). Structured Outputs ueber `output_config.format` — die Antwort ist
garantiert gueltiges JSON nach dem Schema, kein Parsen von Freitext.
Structured Outputs verbietet freies `additionalProperties`, deshalb sind alle
Schemata geschlossen (`additionalProperties: False`, alle Felder `required`).

Faellt der Aufruf aus (Netz, 429, Server), liefert `frage_json` None. Der
Aufrufer behandelt das wie „unklar" — es wird nie geraten.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import anthropic

log = logging.getLogger("schoepsmail.llm")

MODELL = os.getenv("ANTHROPIC_MODELL", "claude-haiku-4-5")
_client: anthropic.AsyncAnthropic | None = None


def aktiv() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def _klient() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic(max_retries=2, timeout=60.0)
    return _client


async def frage_json(system: str, nutzer: str, schema: dict[str, Any],
                     max_tokens: int = 1024) -> dict[str, Any] | None:
    """Systemprompt + Nutzertext -> Objekt nach `schema`, oder None bei Fehler."""
    if not aktiv():
        log.warning("ANTHROPIC_API_KEY fehlt — LLM-Stufe aus")
        return None
    try:
        r = await _klient().messages.create(
            model=MODELL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": nutzer}],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
    except anthropic.RateLimitError as exc:
        log.warning("Anthropic 429: %s", exc)
        return None
    except anthropic.APIStatusError as exc:
        # Bei 400 den Anlass mitloggen: im Trockenlauf kam je Lauf einmal
        # 'Invalid request data' ohne erkennbaren Grund — Laenge und Anfang des
        # Nutzertexts helfen, das Muster zu finden.
        log.error("Anthropic %s: %s | nutzer_len=%d anfang=%r", exc.status_code, exc.message,
                  len(nutzer), nutzer[:160])
        return None
    except anthropic.APIConnectionError as exc:
        log.warning("Anthropic nicht erreichbar: %s", exc)
        return None
    if r.stop_reason == "refusal":
        log.warning("Anthropic hat die Anfrage abgelehnt")
        return None
    text = next((b.text for b in r.content if b.type == "text"), None)
    if not text:
        return None
    try:
        daten = json.loads(text)
    except ValueError:
        log.error("Antwort kein JSON: %s", text[:200])
        return None
    daten["_tokens"] = {"in": r.usage.input_tokens, "out": r.usage.output_tokens}
    return daten
