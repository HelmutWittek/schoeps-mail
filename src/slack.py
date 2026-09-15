"""Slack-Nachrichten in den Kanal `#mail-sortierer` (Bot-Token, `chat.postMessage`).

Nur senden, kein Webhook. Fehler beim Senden werden geloggt und geschluckt —
ein toter Slack darf den Worker nicht anhalten. `alarm()` drosselt gleiche
Meldungen auf einmal je `ALARM_PAUSE_MIN` Minuten, sonst kaeme bei einem
Dauerfehler alle zwei Minuten dieselbe Nachricht.
"""
from __future__ import annotations

import logging
import os
import time

import httpx

log = logging.getLogger("schoepsmail.slack")

TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
KANAL = os.getenv("SLACK_KANAL", "C0C18020FN3")
ALARM_PAUSE_MIN = int(os.getenv("SLACK_ALARM_PAUSE_MIN", "60"))

_zuletzt: dict[str, float] = {}


def aktiv() -> bool:
    return bool(TOKEN and KANAL)


async def sende(text: str) -> bool:
    if not aktiv():
        log.info("Slack aus (kein Token) — Nachricht: %s", text[:120])
        return False
    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
            r = await c.post("https://slack.com/api/chat.postMessage",
                             headers={"Authorization": f"Bearer {TOKEN}"},
                             json={"channel": KANAL, "text": text})
        daten = r.json()
        if not daten.get("ok"):
            log.warning("Slack-Fehler: %s", daten.get("error"))
            return False
        return True
    except httpx.HTTPError as exc:
        log.warning("Slack nicht erreichbar: %s", exc)
        return False


async def alarm(schluessel: str, text: str) -> None:
    """Wie `sende`, aber je Schluessel hoechstens einmal je ALARM_PAUSE_MIN."""
    jetzt = time.time()
    if jetzt - _zuletzt.get(schluessel, 0) < ALARM_PAUSE_MIN * 60:
        return
    _zuletzt[schluessel] = jetzt
    await sende(f":warning: {text}")
