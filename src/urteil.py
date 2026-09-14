"""Stufe 4: das Urteil. Haiku liest die Mail und waehlt einen Ordner — oder keinen.

Bekommt den Ordnerbaum MIT Profilen (siehe `profil.py`), die Kandidaten aus
Stufe 2/3 mit ihrer Begruendung, und die Mail selbst (Absender, Empfaenger,
Betreff, Text gekuerzt). Antwortet mit genau einem Pfad aus der Liste und
einer Sicherheit:

- `sicher`   — wird verschoben (Kategorie `auto-ki`)
- `unsicher` — bleibt liegen, Begruendung ins Protokoll
- `nirgends` — passt in keinen bestehenden Ordner (Kandidat fuer Vorschlaege)

Der Ordnerbaum steht im Systemprompt mit `cache_control`: er ist bei jedem
Urteil gleich und wird so nur einmal je fuenf Minuten voll bezahlt.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src import llm

log = logging.getLogger("schoepsmail.urteil")

MAX_TEXT = 3000

REGELN = (
    "Du sortierst eingehende E-Mails eines Mitarbeiters der SCHOEPS Mikrofone GmbH "
    "(Karlsruhe, Hersteller von Studiomikrofonen) in seine bestehenden Ordner. "
    "Unten steht sein Ordnerbaum, je Ordner mit einer Beschreibung, wofuer er steht. "
    "Du bekommst danach eine Mail und moeglicherweise Kandidaten-Ordner, die aus dem "
    "Thread oder der Absender-Historie stammen — die haben Vorrang, wenn sie inhaltlich "
    "passen.\n\n"
    "Regeln:\n"
    "- Waehle GENAU EINEN Pfad aus der Liste, buchstabengetreu. Erfinde keinen Ordner.\n"
    "- `sicher` nur, wenn Thema UND Kontext eindeutig zu diesem Ordner passen und kein "
    "anderer Ordner ernsthaft in Frage kommt. Im Zweifel `unsicher`.\n"
    "- Passt die Mail in keinen bestehenden Ordner, antworte `nirgends` mit leerem Pfad.\n"
    "- Sammelordner unter 'Posteingang/' (z.B. fuer Ticket-Systeme, Kalender-Einladungen, "
    "interne Post) sind nur richtig, wenn die Mail von genau diesem System oder dieser "
    "Art ist.\n"
    "- Newsletter und Werbung gehoeren in den Ordner, in dem gleichartige Post liegt; "
    "gibt es keinen, `nirgends`.\n"
    "- Begruendung: ein Satz, der den Anhaltspunkt nennt (Thema, Absender, Thread)."
)

SCHEMA = {
    "type": "object",
    "properties": {
        "pfad": {"type": "string", "description": "exakter Ordnerpfad aus der Liste oder leer"},
        "sicherheit": {"type": "string", "enum": ["sicher", "unsicher", "nirgends"]},
        "begruendung": {"type": "string"},
    },
    "required": ["pfad", "sicherheit", "begruendung"],
    "additionalProperties": False,
}


async def lade_ordnerliste(s: AsyncSession) -> list[dict[str, Any]]:
    """Zielordner mit Profil und Mailzahl, alphabetisch — der stabile Teil des Prompts."""
    r = await s.execute(text("""
        SELECT o.id, o.pfad, o.profil,
               (SELECT count(*) FROM mail m WHERE m.ordner_id = o.id AND m.entfernt_am IS NULL) AS n
          FROM ordner o
         WHERE o.verschwunden_am IS NULL AND NOT o.ist_arbeitsordner
         ORDER BY o.pfad
    """))
    return [{"id": row[0], "pfad": row[1], "profil": row[2] or "", "n": int(row[3])}
            for row in r.fetchall()]


def ordnerbaum_text(ordner: list[dict[str, Any]]) -> str:
    zeilen = []
    for o in ordner:
        if o["n"] == 0 and not o["profil"]:
            zeilen.append(f"- {o['pfad']}  (leer)")
        else:
            zeilen.append(f"- {o['pfad']}  [{o['n']} Mails] {o['profil']}".rstrip())
    return "\n".join(zeilen)


def _mail_text(mail: dict[str, Any], text_: str) -> str:
    an = ", ".join((mail.get("an") or [])[:4])
    kopf = [
        f"VON: {mail.get('von_name') or ''} <{mail.get('von_adresse') or ''}>".strip(),
        f"AN: {an}",
        f"DATUM: {mail.get('empfangen_am') or ''}",
        f"BETREFF: {mail.get('betreff') or ''}",
        "",
        (text_ or mail.get("vorschau") or "")[:MAX_TEXT],
    ]
    return "\n".join(kopf)


async def urteile(s: AsyncSession, mail: dict[str, Any], text_: str,
                  kandidaten: list[dict[str, Any]], ordner: list[dict[str, Any]] | None = None
                  ) -> dict[str, Any] | None:
    """Ein Haiku-Urteil. None bei LLM-Ausfall (= unklar, nie raten)."""
    if ordner is None:
        ordner = await lade_ordnerliste(s)
    nach_pfad = {o["pfad"]: o["id"] for o in ordner}

    system = [
        {"type": "text", "text": REGELN},
        {"type": "text", "text": "ORDNERBAUM:\n" + ordnerbaum_text(ordner),
         "cache_control": {"type": "ephemeral"}},
    ]
    teile = []
    if kandidaten:
        teile.append("KANDIDATEN (aus Thread/Absender-Historie, Vorrang wenn passend):")
        teile += [f"- {k['pfad']}  ({k.get('grund', '')}, Gewicht {k.get('gewicht', '')})" for k in kandidaten]
        teile.append("")
    teile.append("MAIL:")
    teile.append(_mail_text(mail, text_))
    antwort = await llm.frage_json(system, "\n".join(teile), SCHEMA, max_tokens=300)
    if not antwort:
        return None
    pfad = (antwort.get("pfad") or "").strip()
    sicherheit = antwort.get("sicherheit")
    begr = (antwort.get("begruendung") or "").strip()[:500]
    if sicherheit == "nirgends" or not pfad:
        return {"stufe": "ki", "ordner_id": None, "ziel_pfad": None, "sicherheit": "nirgends",
                "begruendung": begr, "tokens": antwort["_tokens"]}
    if pfad not in nach_pfad:
        # Erfundener oder verschriebener Pfad: zaehlt als unsicher, nie als Ziel.
        log.warning("Urteil nennt unbekannten Pfad %r", pfad)
        return {"stufe": "ki", "ordner_id": None, "ziel_pfad": pfad, "sicherheit": "unsicher",
                "begruendung": f"(unbekannter Pfad) {begr}", "tokens": antwort["_tokens"]}
    return {"stufe": "ki", "ordner_id": nach_pfad[pfad], "ziel_pfad": pfad,
            "sicherheit": sicherheit, "begruendung": begr, "tokens": antwort["_tokens"]}
