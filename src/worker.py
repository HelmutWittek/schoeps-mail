"""Worker-Schleife (Phase 2).

Jeder Zyklus (POLL_SECONDS, Default 120 s):
  1. Delta nur fuer `Posteingang/Move` — billig, eine Graph-Seite.
  2. Sortieren: Kaskade Stufe 1–3 ueber alles in Move, bewegen oder liegen lassen.
     DRY_RUN=1 (Default) protokolliert nur. KI-Stufe erst ab Phase 3 (`SORTIERER_KI=1`).
Jeder VOLL_SYNC_ALLE-te Zyklus (Default 8 → alle 16 min) zusaetzlich:
  3. Ordnerbaum spiegeln + Delta ueber alle Ordner (haelt die Evidenz frisch;
     Helmuts Handarbeit im Client wird so zur Regel).
  4. Ordnerprofile auffrischen, wenn faellig.
  5. Outlook-Posteingangsregeln spiegeln (`outlook_regeln`, nur lesend).

Heartbeats `sortierer` (jeder Zyklus) und `index` (Voll-Sync). Ab drei
Fehlern in Folge geht ein Slack-Alarm, gedrosselt auf einen je Stunde. Beim
Start werden fehlende auto-Kategorien angelegt und der Ablauf des Client-
Secrets geprueft (`GRAPH_SECRET_ABLAUF`, Warnung ab 30 Tage davor).
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import date

from src import index, llm, nachzieher, outlook_regeln, profil, slack, sortierer
from src.graph import Graph
from src.heartbeat import record_failure, record_success

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # eine Zeile je Graph-Seite ist Rauschen
log = logging.getLogger("schoepsmail.worker")

POLL_SECONDS = int(os.getenv("POLL_SECONDS", "120"))
VOLL_SYNC_ALLE = int(os.getenv("VOLL_SYNC_ALLE", "8"))
DRY_RUN = os.getenv("DRY_RUN", "1") != "0"
SORTIERER_KI = os.getenv("SORTIERER_KI", "0") == "1"
# Grobe Vorstufe im Posteingang (uninteressant.py): laeuft wie der Nachzieher
# immer mit und protokolliert, bewegt aber erst mit POSTEINGANG_DRY_RUN='0'.
POSTEINGANG_DRY_RUN = os.getenv("POSTEINGANG_DRY_RUN", "1") != "0"
ALARM_AB_FEHLERN = 3
SECRET_WARNUNG_TAGE = 30


def _secret_pruefen() -> str | None:
    ablauf = os.getenv("GRAPH_SECRET_ABLAUF", "").strip()
    if not ablauf:
        return None
    try:
        rest = (date.fromisoformat(ablauf) - date.today()).days
    except ValueError:
        return f"GRAPH_SECRET_ABLAUF={ablauf!r} ist kein ISO-Datum"
    if rest <= SECRET_WARNUNG_TAGE:
        return f"Graph-Client-Secret laeuft in {rest} Tagen ab ({ablauf}) — im Azure-Portal erneuern"
    return None


async def voll_sync(graph: Graph) -> int:
    ordner = await index.spiegle_ordner(graph)
    neu, weg = await index.sync_mails(graph, ordner)
    if llm.aktiv():
        try:
            await profil.profil_lauf()
        except Exception:  # noqa: BLE001 — Profile sind Komfort, kein Muss
            log.exception("Profil-Lauf fehlgeschlagen")
    # Nachzieher: Helmuts Handbewegungen seit dem letzten Lauf auf die Quell-
    # Ordner anwenden (eigener Schalter NACHZIEHER_DRY_RUN, Default 1).
    try:
        z = await nachzieher.lauf(graph)
        if z.get("handbewegungen"):
            log.info("Nachzieher: %s", dict(z))
    except Exception:  # noqa: BLE001 — darf den Sync nicht kosten
        log.exception("Nachzieher fehlgeschlagen")
    # Outlook-Posteingangsregeln spiegeln. Sie greifen bei der Zustellung, also
    # vor allem, was hier passiert — ohne den Spiegel bliebe diese zweite
    # Automatik unsichtbar. Nur lesen, geaendert wird im Postfach.
    try:
        z = await outlook_regeln.spiegle(graph)
        if z.get("neu") or z.get("geaendert") or z.get("verschwunden"):
            log.info("Outlook-Regeln: %s", dict(z))
    except Exception:  # noqa: BLE001 — darf den Sync nicht kosten
        log.exception("Regel-Spiegel fehlgeschlagen")
    # Grobe Vorstufe: bekannt uninteressante Post aus dem Posteingang wegraeumen.
    # Erst hier, nicht im 2-Minuten-Zyklus — der Posteingang ist gerade frisch
    # synchronisiert, und der Eingriff in Helmuts Arbeitsplatz soll selten sein.
    try:
        z = await sortierer.raeume_posteingang(graph, dry_run=POSTEINGANG_DRY_RUN)
        if z.get("geraeumt") or z.get("wuerde_raeumen") or z.get("move_fehler"):
            log.info("Posteingang-Vorstufe: %s", dict(z))
    except Exception:  # noqa: BLE001 — darf den Sync nicht kosten
        log.exception("Posteingang-Vorstufe fehlgeschlagen")
    return neu + weg


async def move_zyklus(graph: Graph) -> dict[str, int]:
    mo = await sortierer.move_ordner()
    if mo is None:
        await index.spiegle_ordner(graph)
        mo = await sortierer.move_ordner()
        if mo is None:
            raise RuntimeError(f"Move-Ordner {sortierer.MOVE_PFAD!r} existiert nicht im Postfach")
    await index.sync_ordner(graph, mo[0], mo[1])
    z = await sortierer.sortiere(graph, dry_run=DRY_RUN, mit_ki=SORTIERER_KI)
    return dict(z)


async def main() -> None:
    log.info("Schoeps-Mail-Worker startet: poll=%ss voll_sync_alle=%d dry_run=%s ki=%s",
             POLL_SECONDS, VOLL_SYNC_ALLE, DRY_RUN, SORTIERER_KI)
    graph = Graph()
    fehler_in_folge = 0
    zyklus = 0
    try:
        try:
            await sortierer.kategorien_sicherstellen(graph)
        except Exception:  # noqa: BLE001
            log.exception("Kategorien nicht geprueft")
        warnung = _secret_pruefen()
        if warnung:
            log.warning(warnung)
            await slack.alarm("secret", warnung)
        while True:
            zyklus += 1
            try:
                if zyklus % VOLL_SYNC_ALLE == 1:
                    n = await voll_sync(graph)
                    await record_success("index", n)
                    log.info("Voll-Sync: %d Aenderungen", n)
                z = await move_zyklus(graph)
                await record_success("sortierer", z.get("bewegt", 0))
                if z.get("in_move"):
                    log.info("Move: %s", z)
                fehler_in_folge = 0
            except Exception as exc:  # noqa: BLE001
                fehler_in_folge += 1
                log.exception("Zyklus fehlgeschlagen (%d in Folge)", fehler_in_folge)
                await record_failure("sortierer", exc)
                if fehler_in_folge >= ALARM_AB_FEHLERN:
                    await slack.alarm("zyklus", f"Mail-Sortierer: {fehler_in_folge} Zyklen in Folge "
                                                f"fehlgeschlagen — zuletzt: {type(exc).__name__}: {str(exc)[:200]}")
            await asyncio.sleep(POLL_SECONDS)
    finally:
        await graph.aclose()


if __name__ == "__main__":
    asyncio.run(main())
