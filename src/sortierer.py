"""Phase 2: der Sortierer. Nimmt, was in `Posteingang/Move` liegt, und legt es ab.

Je Mail laeuft die Kaskade (Phase 2: Stufen 1–3, `mit_ki=False`; Phase 3
schaltet das Urteil zu). Ist das Ergebnis belastbar (`kaskade.bewegt`), bekommt
die Mail die Kategorie ihrer Stufe und wandert per Graph in den Zielordner; der
Index wird sofort nachgezogen, nicht erst beim naechsten Delta. Was unklar
bleibt, bleibt in Move — Entscheidung Helmut, kein Unbekannt-Ordner — und wird
beim naechsten Zyklus neu bewertet, weil inzwischen Historie entstanden sein kann.

DRY_RUN: dieselbe Entscheidung, nur protokolliert (`regel_entscheidung.dry_run`),
nichts bewegt. Damit im Trockenlauf nicht alle zwei Minuten dieselbe Zeile
entsteht, wird je Mail nur protokolliert, wenn sich seit dem letzten Eintrag
Stufe oder Ziel geaendert haben oder er aelter als `PROTOKOLL_PAUSE_MIN` ist.
"""
from __future__ import annotations

import logging
import os
from collections import Counter
from typing import Any

from sqlalchemy import text

from src import kaskade, urteil
from src.db import get_session
from src.graph import Graph

log = logging.getLogger("schoepsmail.sortierer")

MOVE_PFAD = os.getenv("MOVE_PFAD", "Posteingang/Move")
PROTOKOLL_PAUSE_MIN = int(os.getenv("PROTOKOLL_PAUSE_MIN", "360"))
KATEGORIEN = ["auto-regel", "auto-thread", "auto-ki", "auto-neu"]

FELDER = ("m.id, m.ordner_id, m.conversation_id, m.von_adresse, m.von_name, m.von_domain, "
          "m.an, m.betreff, m.vorschau, m.empfangen_am, m.kategorien")


async def move_ordner() -> tuple[str, str] | None:
    async with get_session() as s:
        r = await s.execute(text("SELECT id, pfad FROM ordner WHERE pfad = :p AND verschwunden_am IS NULL"),
                            {"p": MOVE_PFAD})
        row = r.fetchone()
    return (row[0], row[1]) if row else None


async def lade_move_mails(ordner_id: str) -> list[dict[str, Any]]:
    async with get_session() as s:
        r = await s.execute(text(f"""
            SELECT {FELDER} FROM mail m
             WHERE m.ordner_id = :o AND m.entfernt_am IS NULL
             ORDER BY m.empfangen_am DESC NULLS LAST
        """), {"o": ordner_id})
        return [dict(row._mapping) for row in r.fetchall()]


async def kategorien_sicherstellen(graph: Graph) -> list[str]:
    """Fehlende auto-Kategorien in der Master-Liste anlegen (Entscheidung Helmut)."""
    vorhanden = await graph.master_kategorien()
    belegt = set(vorhanden.values())
    frei = [f"preset{i}" for i in range(25) if f"preset{i}" not in belegt]
    neu = []
    for name in KATEGORIEN:
        if name in vorhanden:
            continue
        farbe = frei.pop(0) if frei else "none"
        await graph.lege_kategorie_an(name, farbe)
        neu.append(name)
    if neu:
        log.info("Kategorien angelegt: %s", neu)
    return neu


async def verschiebe(graph: Graph, m: dict[str, Any], e: dict[str, Any]) -> None:
    """Kategorie setzen, Move ausfuehren, Index sofort nachziehen, protokollieren."""
    kat = kaskade.kategorie(e)
    neue = [k for k in (m.get("kategorien") or []) if not k.startswith("auto-")] + [kat]
    await graph.setze_kategorien(m["id"], neue)
    await graph.verschiebe(m["id"], e["ordner_id"])
    async with get_session() as s:
        await s.execute(text("""
            UPDATE mail SET ordner_id = :o, kategorien = :k, aktualisiert_am = now() WHERE id = :id
        """), {"o": e["ordner_id"], "k": neue, "id": m["id"]})
        await kaskade.protokolliere(s, m["id"], e, dry_run=False, ausgefuehrt=True)


async def _schon_protokolliert(mail_id: str, e: dict[str, Any]) -> bool:
    async with get_session() as s:
        r = await s.execute(text("""
            SELECT stufe, ziel_ordner_id, am > now() - make_interval(mins => CAST(:pause AS integer))
              FROM regel_entscheidung WHERE mail_id = :m ORDER BY am DESC LIMIT 1
        """), {"m": mail_id, "pause": PROTOKOLL_PAUSE_MIN})
        row = r.fetchone()
    return bool(row and row[0] == e["stufe"] and row[1] == e.get("ordner_id") and row[2])


async def sortiere(graph: Graph, dry_run: bool = True, mit_ki: bool = False) -> Counter:
    """Ein Durchgang ueber Move. Liefert Zaehler je Stufe plus 'bewegt'/'move_fehler'."""
    z: Counter = Counter()
    mo = await move_ordner()
    if not mo:
        log.error("Move-Ordner %r nicht im Index — Ordnerbaum spiegeln", MOVE_PFAD)
        return z
    mails = await lade_move_mails(mo[0])
    z["in_move"] = len(mails)
    if not mails:
        return z
    ordnerliste = None
    if mit_ki:
        async with get_session() as s:
            ordnerliste = await urteil.lade_ordnerliste(s)
    for m in mails:
        async with get_session() as s:
            e = await kaskade.entscheide(s, m, graph if mit_ki else None, mit_ki=mit_ki,
                                         ordnerliste=ordnerliste)
        schl = e["stufe"] if e["stufe"] != "ki" else f"ki/{e.get('sicherheit')}"
        z[schl] += 1
        if kaskade.bewegt(e) and not dry_run:
            try:
                await verschiebe(graph, m, e)
                z["bewegt"] += 1
                log.info("→ %s  [%s]  %r", e["ziel_pfad"], e["stufe"], (m["betreff"] or "")[:60])
            except Exception as exc:  # noqa: BLE001 — eine Mail darf den Durchgang nicht kosten
                log.error("Move fehlgeschlagen fuer %r: %s", (m["betreff"] or "")[:60], exc)
                z["move_fehler"] += 1
            continue
        if not await _schon_protokolliert(m["id"], e):
            async with get_session() as s:
                await kaskade.protokolliere(s, m["id"], e, dry_run=True)
            if kaskade.bewegt(e):
                log.info("(dry) → %s  [%s]  %r", e["ziel_pfad"], e["stufe"], (m["betreff"] or "")[:60])
    return z
