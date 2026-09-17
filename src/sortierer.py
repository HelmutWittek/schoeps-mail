"""Phase 2: der Sortierer. Nimmt, was in `Posteingang/Move` liegt, und legt es ab.

Je Mail laeuft die Kaskade (Phase 2: Stufen 1–3, `mit_ki=False`; Phase 3
schaltet das Urteil zu). Ist das Ergebnis belastbar (`kaskade.bewegt`), bekommt
die Mail die Kategorie ihrer Stufe und wandert per Graph in den Zielordner; der
Index wird sofort nachgezogen, nicht erst beim naechsten Delta. Was unklar
bleibt, bleibt in Move und wird beim naechsten Zyklus neu bewertet, weil
inzwischen Historie entstanden sein kann.

Eine Ausnahme davon seit 2026-09-15 (Entscheidung Helmut): Post, die in keinen
Ordner GEHOERT — der Absender-Vorfilter hat angehalten, oder die KI sagt
`nirgends` — wandert mit der Marke `auto-unbestimmt` nach `Move/Unbestimmt`,
den Ordner fuer „kann ich selbst nicht sortieren". Das haelt den Arbeitsvorrat
`Move` frei von Akquise und Fremdthemen. `unsicher` bleibt ausdruecklich in
Move: da passt das Thema, nur der Ordner ist unklar, und das soll Helmut sehen.
Wirksam wird es mit Stufe 4 (`SORTIERER_KI=1`); ohne KI gibt es keine
`nirgends`-Urteile und der Vorfilter laeuft nicht.

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

from src import kaskade, uninteressant, urteil
from src.db import get_session
from src.graph import Graph

log = logging.getLogger("schoepsmail.sortierer")

MOVE_PFAD = os.getenv("MOVE_PFAD", "Posteingang/Move")
# Ablage fuer Post, die in keinen Ordner gehoert (Vorfilter-Treffer, KI sagt
# `nirgends`) — Entscheidung Helmut 2026-09-15. Fehlt der Ordner im Index,
# bleiben diese Mails einfach in `Move` liegen.
UNBESTIMMT_PFAD = os.getenv("UNBESTIMMT_PFAD", "Posteingang/Move/Unbestimmt")
PROTOKOLL_PAUSE_MIN = int(os.getenv("PROTOKOLL_PAUSE_MIN", "360"))
KATEGORIEN = ["auto-regel", "auto-thread", "auto-ki", "auto-neu",
              kaskade.KATEGORIE_UNBESTIMMT, kaskade.KATEGORIE_UNINTERESSANT]
# Quelle der groben Vorstufe: der Posteingang selbst, ohne Unterordner.
POSTEINGANG_PFAD = os.getenv("POSTEINGANG_PFAD", "Posteingang")

FELDER = ("m.id, m.ordner_id, m.conversation_id, m.von_adresse, m.von_name, m.von_domain, "
          "m.an, m.betreff, m.vorschau, m.empfangen_am, m.kategorien")


async def ordner_nach_pfad(pfad: str) -> tuple[str, str] | None:
    async with get_session() as s:
        r = await s.execute(text("SELECT id, pfad FROM ordner WHERE pfad = :p AND verschwunden_am IS NULL"),
                            {"p": pfad})
        row = r.fetchone()
    return (row[0], row[1]) if row else None


async def move_ordner() -> tuple[str, str] | None:
    return await ordner_nach_pfad(MOVE_PFAD)


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
        # Eigene Bewegung ins Log, damit der Index sie spaeter nicht fuer Hand haelt
        # (er sieht die Mail dann schon im Zielordner, kein Wechsel mehr).
        await s.execute(text("""
            INSERT INTO mail_bewegung (mail_id, von_ordner_id, nach_ordner_id, quelle, verarbeitet_am)
            VALUES (:m, :von, :nach, 'worker', now())
        """), {"m": m["id"], "von": m.get("ordner_id"), "nach": e["ordner_id"]})
        await kaskade.protokolliere(s, m["id"], e, dry_run=False, ausgefuehrt=True)


async def _schon_protokolliert(mail_id: str, e: dict[str, Any]) -> bool:
    async with get_session() as s:
        r = await s.execute(text("""
            SELECT stufe, ziel_ordner_id, am > now() - make_interval(mins => CAST(:pause AS integer))
              FROM regel_entscheidung WHERE mail_id = :m ORDER BY am DESC LIMIT 1
        """), {"m": mail_id, "pause": PROTOKOLL_PAUSE_MIN})
        row = r.fetchone()
    return bool(row and row[0] == e["stufe"] and row[1] == e.get("ordner_id") and row[2])


async def raeume_posteingang(graph: Graph, dry_run: bool = True) -> Counter:
    """Grobe Vorstufe: bekannt uninteressante Post aus dem Posteingang wegraeumen.

    Bewusst NICHT die Kaskade — im Posteingang laeuft nur die eine Regel aus
    `uninteressant.pruefe` (Absender, den Helmut selbst schon als uninteressant
    abgelegt hat, mit vier Sicherheitsnetzen). Alles andere bleibt liegen, damit
    Helmut es sieht und selbst nach `Move` zieht. Nur Mails direkt im
    Posteingang, keine Unterordner.
    """
    z: Counter = Counter()
    po = await ordner_nach_pfad(POSTEINGANG_PFAD)
    ziel = await ordner_nach_pfad(uninteressant.SPAM_PFAD)
    if not po or not ziel:
        log.warning("Posteingang (%r) oder %r nicht im Index — Vorstufe uebersprungen",
                    POSTEINGANG_PFAD, uninteressant.SPAM_PFAD)
        return z
    mails = await lade_move_mails(po[0])
    z["im_posteingang"] = len(mails)
    for m in mails:
        async with get_session() as s:
            grund = await uninteressant.pruefe(s, m)
        if not grund:
            continue
        e = {"stufe": "uninteressant", "ordner_id": ziel[0], "ziel_pfad": ziel[1],
             "sicherheit": "sicher", "anteil": None, "begruendung": grund, "kandidaten": []}
        if dry_run:
            if not await _schon_protokolliert(m["id"], e):
                async with get_session() as s:
                    await kaskade.protokolliere(s, m["id"], e, dry_run=True)
                log.info("(dry) ⇢ %s  %r  [%s]", ziel[1], (m["betreff"] or "")[:50], grund)
            z["wuerde_raeumen"] += 1
            continue
        try:
            await verschiebe(graph, m, e)
            z["geraeumt"] += 1
            log.info("⇢ %s  %r  [%s]", ziel[1], (m["betreff"] or "")[:50], grund)
        except Exception as exc:  # noqa: BLE001 — eine Mail darf den Lauf nicht kosten
            log.error("Wegraeumen fehlgeschlagen fuer %r: %s", (m["betreff"] or "")[:50], exc)
            z["move_fehler"] += 1
    return z


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
    unbestimmt = await ordner_nach_pfad(UNBESTIMMT_PFAD)
    if unbestimmt is None:
        log.warning("Ordner %r nicht im Index — `nirgends` bleibt in Move liegen", UNBESTIMMT_PFAD)
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
        # Gehoert in keinen Ordner (Vorfilter oder KI-`nirgends`): nach
        # `Move/Unbestimmt` wegraeumen, statt den Arbeitsvorrat zu fuellen.
        if kaskade.nach_unbestimmt(e) and unbestimmt is not None:
            ziel = {**e, "ordner_id": unbestimmt[0], "ziel_pfad": unbestimmt[1]}
            if dry_run:
                if not await _schon_protokolliert(m["id"], ziel):
                    async with get_session() as s:
                        await kaskade.protokolliere(s, m["id"], ziel, dry_run=True)
                    log.info("(dry) ⇢ %s  %r", unbestimmt[1], (m["betreff"] or "")[:60])
                continue
            try:
                await verschiebe(graph, m, ziel)
                z["unbestimmt"] += 1
                log.info("⇢ %s  %r", unbestimmt[1], (m["betreff"] or "")[:60])
            except Exception as exc:  # noqa: BLE001
                log.error("Wegraeumen fehlgeschlagen fuer %r: %s", (m["betreff"] or "")[:60], exc)
                z["move_fehler"] += 1
            continue
        if not await _schon_protokolliert(m["id"], e):
            async with get_session() as s:
                await kaskade.protokolliere(s, m["id"], e, dry_run=True)
            if kaskade.bewegt(e):
                log.info("(dry) → %s  [%s]  %r", e["ziel_pfad"], e["stufe"], (m["betreff"] or "")[:60])
    return z
