"""Outlook-Posteingangsregeln spiegeln und den Hygienebericht ausgeben.

    docker compose run --rm -T --no-deps worker python scripts/regel_bericht.py [--nur-bericht]

`--nur-bericht` liest nur die Datenbank (kein Graph-Aufruf). Ohne den Schalter
werden die Regeln vorher frisch aus dem Postfach gespiegelt.
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import text

from src import outlook_regeln
from src.db import get_session
from src.graph import Graph

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

# Wie viele Zeilen je Befund gedruckt werden — der Rest wird nur gezaehlt.
ZEILEN = 12


def block(titel: str, zeilen: list, formatierer) -> None:
    print(f"\n{titel} ({len(zeilen)})")
    if not zeilen:
        print("  —")
        return
    for z in zeilen[:ZEILEN]:
        print("  " + formatierer(z))
    if len(zeilen) > ZEILEN:
        print(f"  … {len(zeilen) - ZEILEN} weitere")


async def ziele() -> None:
    """Welche Ordner fuellen die aktiven Regeln?"""
    async with get_session() as s:
        r = await s.execute(text("""
            SELECT o.pfad, count(*) FROM outlook_regel r
              JOIN ordner o ON o.id = r.ziel_ordner_id
             WHERE r.verschwunden_am IS NULL AND r.aktiv
             GROUP BY o.pfad ORDER BY 2 DESC LIMIT 10
        """))
        print("\nZiele aktiver Regeln (Top 10)")
        for pfad, n in r.fetchall():
            print(f"  {n:3}  {pfad}")


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--nur-bericht", action="store_true", help="nicht spiegeln, nur die DB lesen")
    args = p.parse_args()

    if not args.nur_bericht:
        graph = Graph()
        try:
            print("Spiegel:", await outlook_regeln.spiegle(graph))
        finally:
            await graph.aclose()
    print("Bestand:", await outlook_regeln.kennzahlen())

    b = await outlook_regeln.hygiene()
    block("Zielordner existiert nicht mehr", b["ziel_weg"],
          lambda z: f"{z['regel']!r}{'' if z['aktiv'] else '  (aus)'}")
    block("Ohne Bedingung oder ohne Aktion", b["leer"],
          lambda z: f"{z['regel']!r}{'' if z['aktiv'] else '  (aus)'}")
    block("Von Outlook als fehlerhaft gemeldet", b["fehlerhaft"],
          lambda z: f"{z['regel']!r}{'' if z['aktiv'] else '  (aus)'}")
    block("Absender in mehreren Regeln mit verschiedenen Zielen", b["doppelt"],
          lambda z: f"{z['adresse']}  →  {z['regeln']}")
    block("Regel widerspricht der tatsaechlichen Ablage", b["widerspruch"],
          lambda z: (f"{z['adresse']}\n      Regel {z['regel']!r} → {z['regel_ziel']}"
                     f"\n      tatsaechlich {z['tatsaechlich']} "
                     f"({int(z['anteil'] * 100)} % von {z['gewicht']})"))
    block("Wissen nur in der Regel (Absender ohne Evidenz)", b["ohne_evidenz"],
          lambda z: f"{z['adresse']:45} → {z['ziel']}")
    block(f"Eingeschlafen (seit {outlook_regeln.TOT_AB_TAGEN} Tagen keine Mail)", b["eingeschlafen"],
          lambda z: f"{z['adresse']:45} letzte Mail {z['letzte_mail']}")
    await ziele()


if __name__ == "__main__":
    asyncio.run(main())
