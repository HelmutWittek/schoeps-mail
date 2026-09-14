"""Worker-Schleife. Phase 1: nur Index (Ordnerbaum + Delta), nichts wird bewegt.

Jeder Zyklus quittiert per Heartbeat. DRY_RUN=1 ist Default und bleibt es,
bis Phase 2 die Kaskade scharf schaltet.
"""
from __future__ import annotations

import asyncio
import logging
import os

from src import index, llm, profil
from src.graph import Graph
from src.heartbeat import record_failure, record_success

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)  # eine Zeile je Graph-Seite ist Rauschen
log = logging.getLogger("schoepsmail.worker")

POLL_SECONDS = int(os.getenv("POLL_SECONDS", "120"))
DRY_RUN = os.getenv("DRY_RUN", "1") != "0"


async def zyklus(graph: Graph) -> int:
    ordner = await index.spiegle_ordner(graph)
    neu, weg = await index.sync_mails(graph, ordner)
    # Profile auffrischen, wenn faellig (profil.py prueft PROFIL_TAGE selbst;
    # im Normalfall ist hier nichts zu tun und es kostet eine Abfrage).
    if llm.aktiv():
        try:
            await profil.profil_lauf()
        except Exception:  # noqa: BLE001 — Profile sind Komfort, kein Muss
            log.exception("Profil-Lauf fehlgeschlagen")
    return neu + weg


async def main() -> None:
    log.info("Schoeps-Mail-Worker startet: poll=%ss dry_run=%s", POLL_SECONDS, DRY_RUN)
    graph = Graph()
    try:
        while True:
            try:
                n = await zyklus(graph)
                await record_success("index", n)
                log.info("Zyklus fertig: %d Aenderungen", n)
            except Exception as exc:  # noqa: BLE001
                log.exception("Zyklus fehlgeschlagen")
                await record_failure("index", exc)
            await asyncio.sleep(POLL_SECONDS)
    finally:
        await graph.aclose()


if __name__ == "__main__":
    asyncio.run(main())
