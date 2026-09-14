"""Ordnerprofile erzeugen bzw. auffrischen (siehe src/profil.py).

    docker compose exec worker python scripts/profil_lauf.py [--nur-fehlende] [--zeige 20]
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import text

from src.db import get_session
from src.profil import profil_lauf

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)


async def main(nur_fehlende: bool, zeige: int) -> None:
    n = await profil_lauf(nur_fehlende=nur_fehlende)
    print(f"\n{n} Profile geschrieben.\n")
    async with get_session() as s:
        r = await s.execute(text("""
            SELECT o.pfad, o.profil,
                   (SELECT count(*) FROM mail m WHERE m.ordner_id = o.id AND m.entfernt_am IS NULL) AS n
              FROM ordner o WHERE o.profil IS NOT NULL AND NOT o.ist_arbeitsordner
             ORDER BY n DESC LIMIT :z
        """), {"z": zeige})
        for pfad, profil, n in r.fetchall():
            print(f"{pfad}  [{n}]\n    {profil}\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--nur-fehlende", action="store_true")
    p.add_argument("--zeige", type=int, default=20)
    a = p.parse_args()
    asyncio.run(main(a.nur_fehlende, a.zeige))
