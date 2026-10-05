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


async def _immutable(graph: Graph, rest_ids: list[str]) -> list[str]:
    """REST-IDs in ImmutableIds umrechnen (`translateExchangeIds`, je 1000)."""
    aus: list[str] = []
    for i in range(0, len(rest_ids), 1000):
        d = await graph._anfrage("POST", "/translateExchangeIds", json={
            "inputIds": rest_ids[i:i + 1000],
            "sourceIdType": "restId", "targetIdType": "restImmutableEntryId"})
        aus += [x["targetId"] for x in d.get("value", []) if x.get("targetId")]
    return aus


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
        # aendert — `Prefer: IdType` wirkt dort nicht, und der Einzelabruf gibt
        # die ID zurueck, mit der man fragt. Also erst umrechnen. (Erster Versuch
        # am 2026-10-05 schrieb die REST-ID in den Index.)
        for mid in await _immutable(graph, sorted(such_ids)):
            m = await graph.get(f"/messages/{mid}", **{
                "$select": MAIL_FELDER,
                "$expand": "singleValueExtendedProperties($filter=id eq 'String 0x001A')"})
            klasse = [p.get("value") for p in m.get("singleValueExtendedProperties", [])]
            # Der Suchindex von Exchange hinkt nach: nur was WIRKLICH noch im
            # Ordner liegt und die OOF-Klasse traegt.
            if KLASSE not in klasse or m.get("parentFolderId") != oid:
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
