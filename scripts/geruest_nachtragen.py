"""Leere Indexzeilen (Delta-Geruest, siehe index.ist_geruest) per Einzelabruf fuellen.

    docker compose run --rm -T --no-deps worker python scripts/geruest_nachtragen.py [--limit N] [--ausfuehren]

Default ist ein Trockenlauf: holt die Mails, zeigt, was eingetragen wuerde,
schreibt nichts. `--ausfuehren` traegt die Metadaten ein. Der Ordner wird NICHT
angefasst — weicht er in Graph ab, steht das im Bericht (das Delta zieht ihn
nach, mit Bewegungslog). Nicht mehr vorhandene Mails (404) bleiben, wie sie
sind; ihr `@removed` kommt ueber das Delta.
"""
from __future__ import annotations

import argparse
import asyncio
import collections

from sqlalchemy import text

from src.db import get_session
from src.graph import Graph, GraphFehler
from src.index import _mail_zeile, ist_geruest

PARALLEL = 4


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ausfuehren", action="store_true")
    args = ap.parse_args()

    async with get_session() as s:
        r = await s.execute(text(f"""
            SELECT m.id, m.ordner_id, o.pfad FROM mail m LEFT JOIN ordner o ON o.id = m.ordner_id
             WHERE m.entfernt_am IS NULL AND m.empfangen_am IS NULL
               AND m.von_adresse IS NULL AND m.conversation_id IS NULL
             ORDER BY m.aktualisiert_am DESC {"LIMIT " + str(args.limit) if args.limit else ""}
        """))
        zeilen = r.fetchall()
    print(f"{len(zeilen)} leere Zeilen, {'AUSFUEHREN' if args.ausfuehren else 'Trockenlauf'}")

    g = Graph()
    sem = asyncio.Semaphore(PARALLEL)
    zahl = collections.Counter()
    anders: list[str] = []
    beispiele: list[str] = []

    async def eine(mid: str, oid: str, pfad: str) -> None:
        async with sem:
            try:
                m = await g.mail_metadaten(mid)
            except GraphFehler as exc:
                zahl["404" if exc.status == 404 else "fehler"] += 1
                if exc.status != 404:
                    print(f"  Fehler {mid[-16:]}: {exc}")
                return
        if ist_geruest(m):
            zahl["weiter_leer"] += 1
            return
        z = _mail_zeile(m)
        if z["ordner_id"] != oid:
            anders.append(f"  {pfad} → anderer Ordner in Graph: {z['betreff'][:60]}")
        if len(beispiele) < 10:
            beispiele.append(f"  {pfad} | {z['von_adresse']} | {z['betreff'][:60]}")
        if args.ausfuehren:
            async with get_session() as s:
                await s.execute(text("""
                    UPDATE mail SET conversation_id = :conversation_id, internet_message_id = :imid,
                           von_adresse = :von_adresse, von_name = :von_name, von_domain = :von_domain,
                           an = :an, betreff = :betreff, vorschau = :vorschau,
                           empfangen_am = :empfangen_am, gesendet_am = :gesendet_am,
                           kategorien = :kategorien, ist_gelesen = :ist_gelesen,
                           hat_anhang = :hat_anhang, nachrichtentyp = :nachrichtentyp,
                           aktualisiert_am = now()
                     WHERE id = :id AND empfangen_am IS NULL
                """), z)
        zahl["gefuellt"] += 1
        zahl[f"typ:{z['nachrichtentyp'] or 'message'}"] += 1

    await asyncio.gather(*(eine(*z) for z in zeilen))
    await g.aclose()
    print("Beispiele:", *beispiele, sep="\n")
    if anders:
        print(f"{len(anders)} mit anderem Ordner in Graph:", *anders[:20], sep="\n")
    print(dict(zahl))


if __name__ == "__main__":
    asyncio.run(main())
