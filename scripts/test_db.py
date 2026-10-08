"""DB-Tests gegen die echte Datenbank — in EINER Transaktion, die am Ende
zurueckgerollt wird.

    docker compose run --rm -T --no-deps worker python scripts/test_db.py

Warum so: der Worker laeuft parallel auf derselben DB. Alle Funktionen holen
sich ihre Session ueber `db.get_session()`; die wird hier auf eine einzige
Verbindung mit offener Transaktion umgebogen (`join_transaction_mode=
"create_savepoint"`), ein `commit()` der Funktionen schliesst also nur einen
Savepoint. Der Worker sieht nie eine Testzeile, und am Ende ist nichts davon
uebrig — das prueft der letzte Test ueber eine zweite Verbindung.

Graph ist eine Attrappe, die Aufrufe nur mitschreibt; das Postfach wird nie
angefasst. Testdaten tragen das Praefix `ZZTEST` (Regel aus CLAUDE.md).
Geprueft wird, was bisher nur live und in Messlaeufen getestet war:
harte Ablage und Auffang gegen die echten Zielordner, Thread-Stufe,
Bewegungslog, Nachzieher, OOF-Nachtrag, Pfad-Waechter.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timezone
from typing import Any

os.environ.setdefault("EIGENE_DOMAINS", "schoeps.de")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # noqa: E402

from src import db  # noqa: E402

FAELLE = 0


def pruefe(bedingung: bool, name: str) -> None:
    global FAELLE
    FAELLE += 1
    if not bedingung:
        print(f"FEHLT: {name}")
        raise AssertionError(name)
    print(f"ok    {name}")


class GraphAttrappe:
    """Schreibt nur mit. `oof_treffer` steuert, was die Suche „findet"."""

    def __init__(self) -> None:
        self.aufrufe: list[tuple[str, str, Any]] = []
        self.oof_treffer: list[dict[str, Any]] = []

    async def verschiebe(self, mail_id: str, ziel: str) -> dict[str, Any]:
        self.aufrufe.append(("verschiebe", mail_id, ziel))
        return {}

    async def setze_kategorien(self, mail_id: str, kategorien: list[str]) -> None:
        self.aufrufe.append(("kategorien", mail_id, list(kategorien)))

    async def alle_seiten(self, pfad: str, **params: Any):
        for m in self.oof_treffer:
            yield {"id": "REST-" + m["id"]}

    async def get(self, pfad: str, **params: Any) -> dict[str, Any]:
        if pfad.startswith("/messages/REST-"):
            mid = pfad.removeprefix("/messages/REST-")
            return {"internetMessageId": f"<{mid}@zztest.invalid>"}
        if "$filter" in params:
            imid = params["$filter"].split("'")[1]
            return {"value": [m for m in self.oof_treffer if f"<{m['id']}@zztest.invalid>" == imid]}
        raise AssertionError(f"unerwarteter Graph-Aufruf {pfad}")


def jetzt() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def graph_mail(mid: str, ordner: str, conv: str, adresse: str, betreff: str,
               typ: str | None = None, name: str = "ZZTEST") -> dict[str, Any]:
    m = {"id": mid, "parentFolderId": ordner, "conversationId": conv,
         "internetMessageId": f"<{mid}@zztest.invalid>",
         "from": {"emailAddress": {"address": adresse, "name": name}},
         "toRecipients": [], "subject": betreff, "bodyPreview": "",
         "receivedDateTime": jetzt(), "sentDateTime": jetzt(), "categories": []}
    if typ:
        m["@odata.type"] = f"#microsoft.graph.{typ}"
    return m


async def ordner_id(pfad: str) -> str:
    async with db.get_session() as s:
        oid = (await s.execute(text("SELECT id FROM ordner WHERE pfad = :p AND verschwunden_am IS NULL"),
                               {"p": pfad})).scalar()
    assert oid, f"Ordner {pfad!r} fehlt im Index"
    return oid


async def tests() -> None:
    from src import index, kaskade, konversation, nachzieher, oof_nachtrag, regel, sortierer

    move = await ordner_id(sortierer.MOVE_PFAD)
    einladungen = await ordner_id("Posteingang/Einladungen")
    redmine = await ordner_id("Posteingang/Redmine, Planio, Slite, Scanner")
    zendesk = await ordner_id("Posteingang/Zendesk")
    google = await ordner_id("❻ Verwaltung/Hardware, Software, Netzwerk/Google")

    # Testordner: ein Zielordner und eine Quelle fuer den OOF-Nachtrag
    async with db.get_session() as s:
        await s.execute(text("""
            INSERT INTO ordner (id, name, pfad, ist_arbeitsordner) VALUES
              ('ZZTEST-ZIEL', 'Ziel', 'ZZTEST/Ziel', false),
              ('ZZTEST-ZIEL2', 'Ziel2', 'ZZTEST/Ziel2', false),
              ('ZZTEST-QUELLE', 'Quelle', 'ZZTEST/Quelle', true)
        """))

    # --- Pfad-Waechter: die laufende Konfiguration passt zum Index
    async with db.get_session() as s:
        pruefe(await regel.fehlende_ziele(s) == [], "alle Ziele fester Regeln existieren im Index")
        pruefe(await index.fehlende_sonderpfade(s) == [], "alle Sammel-/Altablage-Pfade existieren im Index")

    # --- Bewegungslog im Index: Ordnerwechsel per Delta = Handbewegung
    async with db.get_session() as s:
        await index._schreibe_seite(s, "ZZTEST-ZIEL", [graph_mail(
            "ZZTEST-M1", "ZZTEST-ZIEL", "ZZTEST-CONV-1", "zztest1@zztest.invalid", "ZZTEST Thema")])
        await index._schreibe_seite(s, "ZZTEST-ZIEL2", [graph_mail(
            "ZZTEST-M1", "ZZTEST-ZIEL2", "ZZTEST-CONV-1", "zztest1@zztest.invalid", "ZZTEST Thema")])
        r = (await s.execute(text("""
            SELECT von_ordner_id, nach_ordner_id, quelle FROM mail_bewegung WHERE mail_id = 'ZZTEST-M1'
        """))).fetchall()
    pruefe([tuple(x) for x in r] == [("ZZTEST-ZIEL", "ZZTEST-ZIEL2", "hand")],
           "Ordnerwechsel im Delta erzeugt genau eine Handbewegung")
    async with db.get_session() as s:
        await index._schreibe_seite(s, "ZZTEST-ZIEL2", [{"id": "ZZTEST-M1", "@removed": {"reason": "changed"}}])
        weg = (await s.execute(text("SELECT entfernt_am IS NOT NULL FROM mail WHERE id='ZZTEST-M1'"))).scalar()
    pruefe(bool(weg), "@removed im Delta setzt entfernt_am")

    # Thread-Geschwister im Zielordner fuer die Kaskaden-Tests
    async with db.get_session() as s:
        await index._schreibe_seite(s, "ZZTEST-ZIEL", [
            graph_mail("ZZTEST-M2", "ZZTEST-ZIEL", "ZZTEST-CONV-2", "kollege@schoeps.de", "ZZTEST Projekt"),
        ])

    async def kaskade_fuer(mail: dict[str, Any]) -> dict[str, Any]:
        async with db.get_session() as s:
            return await kaskade.entscheide(s, index._mail_zeile(mail), None, mit_ki=False)

    # --- Stufe 0 gegen die echten Zielordner
    e = await kaskade_fuer(graph_mail("ZZTEST-E1", move, "ZZTEST-CONV-E1", "kollege@schoeps.de",
                                      "Jour fixe", typ="eventMessageRequest"))
    pruefe(e["stufe"] == "hart" and e["ordner_id"] == einladungen, "Einladung (Nachrichtentyp) → hart Einladungen")
    e = await kaskade_fuer(graph_mail("ZZTEST-E2", move, "ZZTEST-CONV-E2", "1og-entwicklung@schoeps.de",
                                      "Attached Image"))
    pruefe(e["stufe"] == "hart" and e["ordner_id"] == redmine, "Scanner → hart Redmine, Planio, Slite, Scanner")
    e = await kaskade_fuer(graph_mail("ZZTEST-E3", move, "ZZTEST-CONV-E3", "zztest9@zztest.invalid",
                                      "Neue Buchung: ZZTEST"))
    pruefe(e["stufe"] == "hart" and e["ordner_id"] == einladungen, "Bookings-Betreff → hart Einladungen")
    # Anzeigename muss durch die ganze Kaskade bis zur harten Ablage kommen (2026-10-08)
    e = await kaskade_fuer(graph_mail("ZZTEST-E8", move, "ZZTEST-CONV-E8", "sales@schoeps.de",
                                      "ZZTEST Bestellung", name="Schoeps Mikrofone Sales"))
    pruefe(e["stufe"] == "hart" and e["ordner_id"] == zendesk, "sales@ ohne Agent → hart Zendesk")
    e = await kaskade_fuer(graph_mail("ZZTEST-E9", move, "ZZTEST-CONV-E9", "sales@schoeps.de",
                                      "ZZTEST Bestellung", name="ZZTEST Agent (Schoeps Mikrofone Sales)"))
    pruefe(e["stufe"] != "hart", "sales@ mit Agent → nicht hart")
    e = await kaskade_fuer(graph_mail("ZZTEST-E10", move, "ZZTEST-CONV-E10", "no-reply@accounts.google.com",
                                      "Sicherheitswarnung"))
    pruefe(e["stufe"] == "hart" and e["ordner_id"] == google, "Google-Kontowarnung → hart Google")

    # --- Stufe 2 vor Auffang; Auffang nur ohne Thread
    e = await kaskade_fuer(graph_mail("ZZTEST-E4", move, "ZZTEST-CONV-2", "kollege@schoeps.de",
                                      "AW: ZZTEST Projekt"))
    pruefe(e["stufe"] == "thread" and e["ordner_id"] == "ZZTEST-ZIEL", "Kollegen-Antwort folgt dem Thread")
    e = await kaskade_fuer(graph_mail("ZZTEST-E5", move, "ZZTEST-CONV-2", "kollege@schoeps.de",
                                      "Automatische Antwort: ZZTEST Projekt"))
    pruefe(e["stufe"] == "thread" and e["ordner_id"] == "ZZTEST-ZIEL",
           "Abwesenheitsnotiz im Thread: Thread gewinnt vor Auffang")
    e = await kaskade_fuer(graph_mail("ZZTEST-E6", move, "ZZTEST-CONV-E6", "kollege@schoeps.de",
                                      "Automatische Antwort: etwas anderes"))
    pruefe(e["stufe"] == "auffang" and e["ordner_id"] == einladungen,
           "Abwesenheitsnotiz ohne Thread → Auffang Einladungen")
    e = await kaskade_fuer(graph_mail("ZZTEST-E7", move, "ZZTEST-CONV-E7", "kollege@schoeps.de", "Frage"))
    pruefe(e["stufe"] == "unklar", "Kollegen-Mail ohne Thread und Regel bleibt unklar")
    async with db.get_session() as s:
        t, _ = await konversation.nach_thread(s, "ZZTEST-CONV-2", "ZZTEST-M2")
    pruefe(t is None, "die Mail selbst zaehlt nicht als ihr eigener Thread (ohne_mail_id)")

    # --- Nachzieher: Handablage zieht das Thread-Geschwister aus Move nach
    async with db.get_session() as s:
        await index._schreibe_seite(s, move, [
            graph_mail("ZZTEST-N1", move, "ZZTEST-CONV-N", "partner@zztest.invalid", "ZZTEST Nachzug"),
            graph_mail("ZZTEST-N2", move, "ZZTEST-CONV-N", "partner@zztest.invalid", "AW: ZZTEST Nachzug"),
        ])
        await index._schreibe_seite(s, "ZZTEST-ZIEL", [
            graph_mail("ZZTEST-N1", "ZZTEST-ZIEL", "ZZTEST-CONV-N", "partner@zztest.invalid", "ZZTEST Nachzug"),
        ])
    echte = nachzieher.offene_handbewegungen

    async def nur_testbewegungen() -> list[dict[str, Any]]:
        return [b for b in await echte() if b["mail_id"].startswith("ZZTEST")]

    nachzieher.offene_handbewegungen = nur_testbewegungen
    g = GraphAttrappe()
    try:
        z = await nachzieher.lauf(g, dry_run=False)
    finally:
        nachzieher.offene_handbewegungen = echte
    pruefe(("verschiebe", "ZZTEST-N2", "ZZTEST-ZIEL") in g.aufrufe, "Nachzieher verschiebt das Thread-Geschwister")
    pruefe(("kategorien", "ZZTEST-N2", ["auto-thread"]) in g.aufrufe, "… mit Kategorie auto-thread")
    async with db.get_session() as s:
        ordner = (await s.execute(text("SELECT ordner_id FROM mail WHERE id='ZZTEST-N2'"))).scalar()
        worker = (await s.execute(text(
            "SELECT count(*) FROM mail_bewegung WHERE mail_id='ZZTEST-N2' AND quelle='worker'"))).scalar()
        offen = (await s.execute(text(
            "SELECT count(*) FROM mail_bewegung WHERE mail_id='ZZTEST-N1' AND verarbeitet_am IS NULL"))).scalar()
    pruefe(ordner == "ZZTEST-ZIEL", "Index zieht die bewegte Mail sofort nach")
    pruefe(worker == 1, "eigene Bewegung steht als 'worker' im Bewegungslog")
    pruefe(offen == 0 and z["bewegt"] == 1, "Handbewegung gilt danach als verarbeitet")

    # --- OOF-Nachtrag: Treffer eintragen, verschwundenen als entfernt markieren
    g = GraphAttrappe()
    oof = graph_mail("ZZTEST-OOF1", "ZZTEST-QUELLE", "ZZTEST-CONV-OOF", "kollege@schoeps.de",
                     "Automatische Antwort: ZZTEST")
    oof["singleValueExtendedProperties"] = [{"id": "String 0x1a", "value": oof_nachtrag.KLASSE}]
    normal = graph_mail("ZZTEST-NORM1", "ZZTEST-QUELLE", "ZZTEST-CONV-OOF", "extern@zztest.invalid",
                        "Automatische Antwort: extern")
    normal["singleValueExtendedProperties"] = [{"id": "String 0x1a", "value": "IPM.Note"}]
    g.oof_treffer = [oof, normal]
    z = await oof_nachtrag.nachtragen(g, ["ZZTEST/Quelle"])
    async with db.get_session() as s:
        typ = (await s.execute(text("SELECT nachrichtentyp FROM mail WHERE id='ZZTEST-OOF1'"))).scalar()
        norm = (await s.execute(text("SELECT count(*) FROM mail WHERE id='ZZTEST-NORM1'"))).scalar()
    pruefe(z["gefunden"] == 1 and typ == oof_nachtrag.TYP, "OOF-Notiz wird mit Typ oofTemplate eingetragen")
    pruefe(norm == 0, "gewoehnliche Mail aus der Suche wird NICHT nachgetragen (kommt ueber das Delta)")
    g.oof_treffer = []
    z = await oof_nachtrag.nachtragen(g, ["ZZTEST/Quelle"])
    async with db.get_session() as s:
        weg = (await s.execute(text("SELECT entfernt_am IS NOT NULL FROM mail WHERE id='ZZTEST-OOF1'"))).scalar()
    pruefe(z["entfernt"] == 1 and bool(weg), "nicht mehr gefundene OOF-Notiz gilt als entfernt")


async def main() -> int:
    verbindung = await db._engine.connect()
    transaktion = await verbindung.begin()
    db._Session = async_sessionmaker(bind=verbindung, expire_on_commit=False, class_=AsyncSession,
                                     join_transaction_mode="create_savepoint")
    try:
        await tests()
    except AssertionError:
        return 1
    finally:
        await transaktion.rollback()
        await verbindung.close()
    # Gegenprobe ueber eine frische Verbindung: nichts ist uebrig geblieben
    async with db._engine.connect() as c:
        rest = (await c.execute(text("""
            SELECT (SELECT count(*) FROM mail WHERE id LIKE 'ZZTEST%')
                 + (SELECT count(*) FROM ordner WHERE id LIKE 'ZZTEST%')
                 + (SELECT count(*) FROM mail_bewegung WHERE mail_id LIKE 'ZZTEST%')
                 + (SELECT count(*) FROM regel_entscheidung WHERE mail_id LIKE 'ZZTEST%')
        """))).scalar()
    pruefe(rest == 0, "nach dem Rollback ist keine Testzeile uebrig")
    print(f"\n{FAELLE} Pruefungen gruen.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
