"""Nachrichtentyp (Migration 006) fuer bereits indizierte Mails nachtragen.

    docker compose run --rm -T --no-deps worker python scripts/nachrichtentyp_nachtragen.py [--ordner PFAD ...]

Der laufende Index traegt den Typ nur fuer Mails ein, die das Delta neu oder
geaendert liefert. Fuer den Bestand holt dieses Skript ein frisches Delta des
Ordners (ohne gespeicherten Delta-Link, der bleibt unberuehrt) und schreibt
NUR die Spalte `nachrichtentyp`. Default: `Posteingang/Move` und der
Posteingang — mehr braucht die harte Ablage nicht, sie wirkt nur auf Move.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter

from sqlalchemy import text

from src.db import get_session
from src.graph import Graph
from src.index import nachrichtentyp


async def main(pfade: list[str]) -> None:
    graph = Graph()
    try:
        for pfad in pfade:
            async with get_session() as s:
                r = await s.execute(text("SELECT id FROM ordner WHERE pfad = :p AND verschwunden_am IS NULL"),
                                    {"p": pfad})
                oid = r.scalar()
            if not oid:
                print(f"{pfad}: nicht im Index")
                continue
            z: Counter = Counter()
            async for seite, _ in graph.delta_seiten(oid, None):
                async with get_session() as s:
                    for m in seite:
                        if "@removed" in m:
                            continue
                        typ = nachrichtentyp(m)
                        r = await s.execute(text("""
                            UPDATE mail SET nachrichtentyp = :t
                             WHERE id = :id AND nachrichtentyp IS DISTINCT FROM :t
                        """), {"t": typ, "id": m["id"]})
                        z[typ or "message"] += 1
                        z["geaendert"] += r.rowcount or 0
            print(f"{pfad}: {dict(z)}")
    finally:
        await graph.aclose()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--ordner", nargs="*", default=["Posteingang/Move", "Posteingang"])
    asyncio.run(main(p.parse_args().ordner))
