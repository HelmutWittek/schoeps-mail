"""Einmaliger Index-Lauf: Ordnerbaum spiegeln, alle Ordner per Delta nachziehen,
danach Kennzahlen. Im Container:

    docker compose exec worker python scripts/index_lauf.py [--nur-ordner]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import time

from sqlalchemy import text

from src import index
from src.db import get_session
from src.graph import Graph

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)


async def kennzahlen() -> None:
    async with get_session() as s:
        r = await s.execute(text("""
            SELECT (SELECT count(*) FROM ordner WHERE verschwunden_am IS NULL),
                   (SELECT count(*) FROM ordner WHERE verschwunden_am IS NULL AND ist_arbeitsordner),
                   (SELECT count(*) FROM mail WHERE entfernt_am IS NULL),
                   (SELECT count(*) FROM mail_evidenz),
                   (SELECT min(empfangen_am) FROM mail),
                   (SELECT count(DISTINCT von_adresse) FROM mail_evidenz),
                   (SELECT count(DISTINCT conversation_id) FROM mail WHERE entfernt_am IS NULL)
        """))
        o, oa, m, ev, aelteste, adressen, konv = r.fetchone()
        print(f"\nOrdner: {o} (davon {oa} Arbeitsordner) | Mails: {m} | Evidenz-Mails: {ev} | "
              f"Absender mit Evidenz: {adressen} | Konversationen: {konv} | aelteste: {aelteste}")
        r = await s.execute(text("""
            SELECT o.pfad, count(m.id) FROM ordner o LEFT JOIN mail m ON m.ordner_id = o.id AND m.entfernt_am IS NULL
             WHERE o.verschwunden_am IS NULL AND NOT o.ist_arbeitsordner
             GROUP BY o.pfad ORDER BY 2 DESC LIMIT 15
        """))
        print("\nGroesste Zielordner:")
        for pfad, n in r.fetchall():
            print(f"  {n:>6}  {pfad}")


async def main(nur_ordner: bool) -> None:
    graph = Graph()
    t0 = time.time()
    try:
        ordner = await index.spiegle_ordner(graph)
        for oid, info in sorted(ordner.items(), key=lambda kv: kv[1]["pfad"]):
            marke = "A" if info["arbeitsordner"] else " "
            print(f"  [{marke}] {info['anzahl']:>6}  {info['pfad']}")
        if not nur_ordner:
            neu, weg = await index.sync_mails(graph, ordner)
            print(f"\nSync: {neu} geschrieben, {weg} entfernt, {time.time() - t0:.0f}s")
        await kennzahlen()
    finally:
        await graph.aclose()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--nur-ordner", action="store_true", help="nur den Ordnerbaum spiegeln")
    a = p.parse_args()
    asyncio.run(main(a.nur_ordner))
