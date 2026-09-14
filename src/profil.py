"""Ordnerprofile: wofuer steht dieser Ordner? Zwei bis drei Saetze je Zielordner.

Ein Ordnername wie „Projekte" sagt dem Modell in Stufe 4 nichts. Das Profil
entsteht aus dem, was im Ordner liegt: Top-Absender, juengste Betreffe,
Kurzvorschauen. Haiku schreibt daraus eine Beschreibung; sie wird woechentlich
aufgefrischt (`PROFIL_TAGE`), ausser sie ist von Hand gesetzt
(`profil_manuell`) — Handtext ist Absicht des Nutzers und bleibt stehen.

Ordner mit weniger als `MIN_MAILS` Mails bekommen kein LLM-Profil, nur den
Namen — zu wenig Stoff, und das Modell wuerde raten.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy import text

from src import llm
from src.db import get_session

log = logging.getLogger("schoepsmail.profil")

PROFIL_TAGE = int(os.getenv("PROFIL_TAGE", "7"))
MIN_MAILS = int(os.getenv("PROFIL_MIN_MAILS", "3"))
MAX_JE_LAUF = int(os.getenv("PROFIL_MAX_JE_LAUF", "400"))

SYSTEM = (
    "Du beschreibst E-Mail-Ordner eines Mitarbeiters der SCHOEPS Mikrofone GmbH "
    "(Karlsruhe, Hersteller von Studiomikrofonen). Du bekommst den Ordnerpfad und "
    "eine Stichprobe seines Inhalts: haeufigste Absender, Betreffe, Textanfaenge. "
    "Schreibe in zwei bis drei Saetzen auf Deutsch, welche Art von Post hier liegt: "
    "Thema, typische Absender oder Partner, Zweck. Konkret, ohne Floskeln, ohne "
    "Aufzaehlung einzelner Mails. Wenn der Ordner mehrere Themen mischt, sag das. "
    "Nenne KEINE personenbezogenen Details ueber den Inhalt hinaus, die zur "
    "Einordnung nicht noetig sind."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "profil": {"type": "string", "description": "2-3 Saetze"},
        "hauptthema": {"type": "string", "description": "3-6 Woerter"},
    },
    "required": ["profil", "hauptthema"],
    "additionalProperties": False,
}


async def _stichprobe(s, ordner_id: str) -> dict[str, Any]:
    r = await s.execute(text("""
        SELECT von_adresse, coalesce(von_name, ''), count(*) AS n
          FROM mail WHERE ordner_id = :o AND entfernt_am IS NULL AND von_adresse IS NOT NULL
         GROUP BY 1, 2 ORDER BY n DESC LIMIT 8
    """), {"o": ordner_id})
    absender = [f"{name} <{adr}> ({n}x)" if name else f"{adr} ({n}x)" for adr, name, n in r.fetchall()]
    r = await s.execute(text("""
        SELECT betreff, vorschau, empfangen_am FROM mail
         WHERE ordner_id = :o AND entfernt_am IS NULL
         ORDER BY empfangen_am DESC NULLS LAST LIMIT 30
    """), {"o": ordner_id})
    zeilen = r.fetchall()
    betreffe = [b for b, _, _ in zeilen if b]
    vorschauen = [v.replace("\n", " ").strip()[:200] for _, v, _ in zeilen[:10] if v]
    zeitraum = None
    if zeilen:
        r2 = await s.execute(text("""
            SELECT min(empfangen_am), max(empfangen_am), count(*) FROM mail
             WHERE ordner_id = :o AND entfernt_am IS NULL
        """), {"o": ordner_id})
        von, bis, n = r2.fetchone()
        zeitraum = f"{n} Mails, {von:%Y-%m} bis {bis:%Y-%m}" if von and bis else f"{n} Mails"
    return {"absender": absender, "betreffe": betreffe, "vorschauen": vorschauen, "zeitraum": zeitraum}


def _prompt(pfad: str, st: dict[str, Any]) -> str:
    teile = [f"ORDNER: {pfad}", f"UMFANG: {st['zeitraum']}", "", "HAEUFIGSTE ABSENDER:"]
    teile += [f"- {a}" for a in st["absender"]] or ["- (keine)"]
    teile += ["", "JUENGSTE BETREFFE:"] + [f"- {b[:120]}" for b in st["betreffe"]]
    teile += ["", "TEXTANFAENGE (Stichprobe):"] + [f"- {v}" for v in st["vorschauen"]]
    return "\n".join(teile)


async def profil_lauf(nur_fehlende: bool = False) -> int:
    """Profile fuer Zielordner erzeugen/auffrischen. Liefert die Zahl geschriebener Profile."""
    async with get_session() as s:
        r = await s.execute(text("""
            SELECT o.id, o.pfad, o.profil IS NULL AS fehlt,
                   (SELECT count(*) FROM mail m WHERE m.ordner_id = o.id AND m.entfernt_am IS NULL) AS n
              FROM ordner o
             WHERE o.verschwunden_am IS NULL AND NOT o.ist_arbeitsordner AND NOT o.profil_manuell
               AND (o.profil IS NULL
                    OR o.profil_am < now() - make_interval(days => CAST(:tage AS integer)))
             ORDER BY o.profil_am NULLS FIRST, n DESC
             LIMIT :max
        """), {"tage": PROFIL_TAGE, "max": MAX_JE_LAUF})
        kandidaten = [(row[0], row[1], row[2], int(row[3])) for row in r.fetchall()]
    if nur_fehlende:
        kandidaten = [k for k in kandidaten if k[2]]
    geschrieben = 0
    tokens_in = tokens_out = 0
    for oid, pfad, _, n in kandidaten:
        if n < MIN_MAILS:
            profil = f"(nur {n} Mails — kein Profil, Ordnername: {pfad.rsplit('/', 1)[-1]})"
        else:
            async with get_session() as s:
                st = await _stichprobe(s, oid)
            antwort = await llm.frage_json(SYSTEM, _prompt(pfad, st), SCHEMA, max_tokens=400)
            if not antwort:
                log.warning("Kein Profil fuer %s (LLM-Fehler)", pfad)
                continue
            tokens_in += antwort["_tokens"]["in"]
            tokens_out += antwort["_tokens"]["out"]
            profil = f"{antwort['hauptthema'].strip()} — {antwort['profil'].strip()}"
        async with get_session() as s:
            await s.execute(text("""
                UPDATE ordner SET profil = :p, profil_am = now() WHERE id = :id AND NOT profil_manuell
            """), {"p": profil[:1200], "id": oid})
        geschrieben += 1
    log.info("Profile: %d geschrieben, Tokens in=%d out=%d", geschrieben, tokens_in, tokens_out)
    return geschrieben
