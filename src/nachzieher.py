"""Der Nachzieher: Helmuts Handablage wirkt rueckwaerts auf noch unsortierte Mails.

Anlass (2026-09-15): „Wenn ich einen neuen Ordner erstelle, soll der Automat
erkennen, dass dahin nun aehnliche Mails gehoeren — nicht nur die aus Move."

Nach jedem Voll-Sync werden die neuen Handbewegungen aus `mail_bewegung`
(Migration 003) abgearbeitet, deren Ziel ein Themenordner ist. Zu jeder
gesucht werden Geschwister, die noch in einem **Quell-Ordner** liegen —
`Move`, seinen Unterordnern (`Unbestimmt`) und den Sammelordnern
(`SCHOEPS intern`) — also dort, wo eine Mail nachweislich noch nicht
bewusst abgelegt ist:

- **Thread:** gleiche `conversationId` → nachziehen (`auto-thread`).
- **Absender:** gilt fuer den Absender jetzt die Juengste-Hand-Regel und zeigt
  sie auf genau diesen Ordner → dessen Mails aus den Quell-Ordnern nachziehen
  (`auto-regel`).

Geschwister in ANDEREN Themenordnern werden nicht angefasst — dort hat Helmut
sie einmal bewusst hingelegt; das wird ein Vorschlag in Phase 4. Posteingang
ist kein Quell-Ordner: was Helmut noch nicht gesehen hat, bleibt liegen
(dieselbe Regel wie in der privaten Automation).

`NACHZIEHER_DRY_RUN=1` (Default) protokolliert nur; `MAX_JE_LAUF` deckelt die
Bewegungen je Lauf, damit eine grosse Umsortierung nicht in einem Zug passiert.
"""
from __future__ import annotations

import logging
import os
from collections import Counter
from typing import Any

from sqlalchemy import text

from src import kaskade, regel, sortierer
from src.db import get_session
from src.graph import Graph
from src.index import SAMMELORDNER_PFADE

log = logging.getLogger("schoepsmail.nachzieher")

MOVE_PFAD = os.getenv("MOVE_PFAD", "Posteingang/Move")
MAX_JE_LAUF = int(os.getenv("NACHZIEHER_MAX_JE_LAUF", "150"))
DRY_RUN = os.getenv("NACHZIEHER_DRY_RUN", "1") != "0"

FELDER = ("m.id, m.ordner_id, m.conversation_id, m.von_adresse, m.von_name, m.von_domain, "
          "m.an, m.betreff, m.vorschau, m.empfangen_am, m.kategorien")


def _quell_bedingung() -> str:
    """SQL-Bedingung auf `ordner o`: Move mit Unterordnern und Sammelordner mit Unterordnern."""
    return "(o.pfad = ANY(:quellen) OR EXISTS (SELECT 1 FROM unnest(:quellen) q WHERE left(o.pfad, char_length(q) + 1) = q || '/'))"


def _quellen() -> list[str]:
    return [MOVE_PFAD, *sorted(SAMMELORDNER_PFADE)]


async def offene_handbewegungen() -> list[dict[str, Any]]:
    async with get_session() as s:
        r = await s.execute(text("""
            SELECT b.id, b.mail_id, b.nach_ordner_id, o.pfad, m.conversation_id, m.von_adresse, m.von_domain
              FROM mail_bewegung b
              JOIN ordner o ON o.id = b.nach_ordner_id
              JOIN mail m ON m.id = b.mail_id
             WHERE b.quelle = 'hand' AND b.verarbeitet_am IS NULL
               AND NOT o.ist_arbeitsordner AND o.verschwunden_am IS NULL
             ORDER BY b.am
        """))
        return [dict(row._mapping) for row in r.fetchall()]


async def geschwister(conversation_id: str | None, adresse: str | None, mit_adresse: bool
                      ) -> list[dict[str, Any]]:
    """Mails in Quell-Ordnern mit gleichem Thread bzw. (optional) gleichem Absender."""
    teile = []
    params: dict[str, Any] = {"quellen": _quellen()}
    if conversation_id:
        teile.append("m.conversation_id = :c")
        params["c"] = conversation_id
    if mit_adresse and adresse:
        teile.append("m.von_adresse = :a")
        params["a"] = adresse.lower()
    if not teile:
        return []
    async with get_session() as s:
        r = await s.execute(text(f"""
            SELECT {FELDER}, o.pfad AS quell_pfad
              FROM mail m JOIN ordner o ON o.id = m.ordner_id
             WHERE m.entfernt_am IS NULL AND ({' OR '.join(teile)}) AND {_quell_bedingung()}
             ORDER BY m.empfangen_am DESC NULLS LAST
        """), params)
        return [dict(row._mapping) for row in r.fetchall()]


async def lauf(graph: Graph, dry_run: bool = DRY_RUN) -> Counter:
    z: Counter = Counter()
    bewegungen = await offene_handbewegungen()
    z["handbewegungen"] = len(bewegungen)
    if not bewegungen:
        return z
    schon: set[str] = set()
    for b in bewegungen:
        if z["bewegt"] + z["dry"] >= MAX_JE_LAUF:
            log.info("Deckel %d erreicht, Rest im naechsten Lauf", MAX_JE_LAUF)
            break
        # Absender-Regel nur, wenn die juengste Hand jetzt auf genau diesen Ordner zeigt
        mit_adresse = False
        if b["von_adresse"] and not (b["von_domain"] and regel.ist_eigene(b["von_domain"])):
            async with get_session() as s:
                jh = await regel.nach_juengster_hand(s, b["von_adresse"])
            mit_adresse = bool(jh and jh["ordner_id"] == b["nach_ordner_id"])
        kandidaten = await geschwister(b["conversation_id"], b["von_adresse"], mit_adresse)
        for m in kandidaten:
            if m["id"] in schon or m["id"] == b["mail_id"]:
                continue
            schon.add(m["id"])
            stufe = "thread" if (b["conversation_id"] and m["conversation_id"] == b["conversation_id"]) else "adresse"
            e = {"stufe": stufe, "ordner_id": b["nach_ordner_id"], "ziel_pfad": b["pfad"],
                 "sicherheit": "sicher", "anteil": None,
                 "begruendung": (f"Nachzieher: Helmut legte {'den Thread' if stufe == 'thread' else 'den Absender'} "
                                 f"nach {b['pfad']} ({m['quell_pfad']})")}
            if dry_run:
                # Nur protokollieren, dedupliziert wie im Sortierer; die
                # Handbewegung bleibt offen, damit das Scharfschalten sie
                # nachholt statt sie als erledigt vorzufinden.
                if not await sortierer._schon_protokolliert(m["id"], e):
                    async with get_session() as s:
                        await kaskade.protokolliere(s, m["id"], e, dry_run=True)
                    log.info("(dry) %s → %s  [%s]  %r", m["quell_pfad"], b["pfad"], stufe, (m["betreff"] or "")[:60])
                z["dry"] += 1
                continue
            try:
                await sortierer.verschiebe(graph, m, e)
                z["bewegt"] += 1
                z[stufe] += 1
                log.info("%s → %s  [%s]  %r", m["quell_pfad"], b["pfad"], stufe, (m["betreff"] or "")[:60])
            except Exception as exc:  # noqa: BLE001
                log.error("Nachziehen fehlgeschlagen fuer %r: %s", (m["betreff"] or "")[:60], exc)
                z["fehler"] += 1
        if not dry_run:
            async with get_session() as s:
                await s.execute(text("UPDATE mail_bewegung SET verarbeitet_am = now() WHERE id = :id"),
                                {"id": b["id"]})
            z["verarbeitet"] += 1
    return z
