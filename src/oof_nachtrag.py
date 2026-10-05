"""Abwesenheitsnotizen aus dem eigenen Tenant in den Index nachtragen.

Befund 2026-10-05 (Helmuts Frage, warum der Thread-Automat eine Mail nicht
mitnahm): Abwesenheitsnotizen, die Exchange fuer Kollegen im selben Tenant
erzeugt, haben die Nachrichtenklasse `IPM.Note.Rules.OofTemplate.Microsoft`.
**Graph laesst sie im Delta UND in der gewoehnlichen Ordnerliste weg** — nur
die Suche (`$search`) findet sie. Der Index sah sie also nie: in `Move` blieben
sie fuer immer liegen, in `SCHOEPS intern` zog der Thread sie nie mit.
Abwesenheitsnotizen externer Server sind gewoehnliche `IPM.Note` und kommen
normal ueber das Delta.

Je Voll-Sync: in `Move` und den Sammelordnern nach den beiden Betreff-Formen
suchen, jeden Treffer einzeln holen (die Suche liefert NICHT die ImmutableId,
der Einzelabruf mit `Prefer: IdType` schon), die Klasse pruefen und ueber
`index._schreibe_seite` eintragen — damit greifen Bewegungslog, Thread und
Auffang-Regel wie bei jeder anderen Mail. Weil auch das Verschwinden dieser
Mails im Delta nicht auftaucht, gilt eine nachgetragene Notiz, die die Suche
im Ordner nicht mehr findet, als entfernt.
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from sqlalchemy import text

from src import index
from src.db import get_session
from src.graph import MAIL_FELDER, Graph

log = logging.getLogger("schoepsmail.oof")

SUCHEN = ('"Automatische Antwort"', '"Automatic reply"')
KLASSE = "IPM.Note.Rules.OofTemplate.Microsoft"
TYP = "oofTemplate"  # Wert in mail.nachrichtentyp


async def _ordner_id(pfad: str) -> str | None:
    async with get_session() as s:
        r = await s.execute(text("SELECT id FROM ordner WHERE pfad = :p AND verschwunden_am IS NULL"),
                            {"p": pfad})
        return r.scalar()


async def _mit_immutable_id(graph: Graph, ordner_id: str, rest_id: str) -> dict[str, Any] | None:
    """Mail zur REST-ID der Suche — mit ImmutableId und der Nachrichtenklasse.

    Der Einzelabruf gibt die ID zurueck, mit der man fragt (also wieder die
    REST-ID), `translateExchangeIds` verweigert der App den Zugriff (403). Ein
    `$filter` auf die internetMessageId im Ordner liefert dagegen die
    ImmutableId — und findet, anders als die Ordnerliste, auch die
    OOF-Notizen. Getestet 2026-10-05.
    """
    x = await graph.get(f"/messages/{rest_id}", **{"$select": "internetMessageId"})
    imid = (x.get("internetMessageId") or "").replace("'", "''")
    if not imid:
        return None
    d = await graph.get(f"/mailFolders/{ordner_id}/messages", **{
        "$filter": f"internetMessageId eq '{imid}'",
        "$select": MAIL_FELDER,
        "$expand": "singleValueExtendedProperties($filter=id eq 'String 0x001A')"})
    werte = d.get("value", [])
    return werte[0] if werte else None


async def nachtragen(graph: Graph, pfade: list[str]) -> Counter:
    z: Counter = Counter()
    for pfad in pfade:
        oid = await _ordner_id(pfad)
        if not oid:
            continue
        gefunden: dict[str, dict[str, Any]] = {}
        such_ids: set[str] = set()
        for q in SUCHEN:
            async for treffer in graph.alle_seiten(f"/mailFolders/{oid}/messages",
                                                   **{"$search": q, "$top": "100", "$select": "id"}):
                such_ids.add(treffer["id"])
        # Die Suche liefert die REST-ID (`AAMk…`), die sich beim Verschieben
        # aendert — `Prefer: IdType` wirkt dort nicht. (Erster Versuch am
        # 2026-10-05 schrieb die REST-ID in den Index.)
        for rest_id in sorted(such_ids):
            m = await _mit_immutable_id(graph, oid, rest_id)
            if m is None:
                continue  # Suchindex von Exchange hinkt nach: liegt nicht mehr hier
            klasse = [p.get("value") for p in m.get("singleValueExtendedProperties", [])]
            if KLASSE not in klasse:
                continue
            m["@odata.type"] = f"#microsoft.graph.{TYP}"
            gefunden[m["id"]] = m
        async with get_session() as s:
            r = await s.execute(text("""
                UPDATE mail SET entfernt_am = now(), aktualisiert_am = now()
                 WHERE ordner_id = :o AND nachrichtentyp = :t AND entfernt_am IS NULL
                   AND NOT (id = ANY(:ids))
            """), {"o": oid, "t": TYP, "ids": list(gefunden)})
            z["entfernt"] += r.rowcount or 0
            n, _ = await index._schreibe_seite(s, oid, list(gefunden.values()))
            z["eingetragen"] += n
        z["gefunden"] += len(gefunden)
    return z
