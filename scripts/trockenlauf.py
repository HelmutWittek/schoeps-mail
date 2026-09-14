"""Trockenlauf-Messung gegen die Historie (Phase 1).

Zieht N bereits abgelegte Mails aus Zielordnern (letzte 12 Monate), blendet
jede einzeln aus der Evidenz aus und laesst die Kaskade raten. Verglichen wird
mit dem Ordner, in dem die Mail tatsaechlich liegt. Nichts wird bewegt, nichts
protokolliert.

    docker compose exec worker python scripts/trockenlauf.py --n 200            # alle Stufen
    docker compose exec worker python scripts/trockenlauf.py --n 500 --ohne-ki  # nur Statistik/Thread, kostenlos

Was die Zahlen bedeuten: „Quote" ist der Anteil richtiger Ordner unter den
Entscheidungen einer Stufe. Bei Stufe 4 zaehlt nur `sicher` als Entscheidung —
das ist die Zahl, an der das Scharfschalten haengt (Ziel >= ~90 %).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import random
from collections import Counter, defaultdict

from sqlalchemy import text

from src import kaskade, urteil
from src.db import get_session
from src.graph import Graph

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

FELDER = "id, ordner_id, conversation_id, von_adresse, von_name, von_domain, an, betreff, vorschau, empfangen_am"


async def stichprobe(n: int, monate: int, seed: float) -> list[dict]:
    async with get_session() as s:
        await s.execute(text("SELECT setseed(:seed)"), {"seed": seed})
        r = await s.execute(text(f"""
            SELECT {FELDER}, (SELECT pfad FROM ordner o WHERE o.id = m.ordner_id) AS pfad
              FROM mail m
             WHERE entfernt_am IS NULL AND von_adresse IS NOT NULL
               AND empfangen_am >= now() - make_interval(months => CAST(:monate AS integer))
               AND ordner_id IN (SELECT id FROM ordner WHERE NOT ist_arbeitsordner AND verschwunden_am IS NULL)
             ORDER BY random() LIMIT :n
        """), {"n": n, "monate": monate})
        return [dict(row._mapping) for row in r.fetchall()]


async def main(n: int, monate: int, mit_ki: bool, seed: float, zeige: int) -> None:
    mails = await stichprobe(n, monate, seed)
    print(f"Stichprobe: {len(mails)} Mails aus den letzten {monate} Monaten, KI={'an' if mit_ki else 'aus'}")
    graph = Graph() if mit_ki else None
    je_stufe: dict[str, Counter] = defaultdict(Counter)
    fehler: list[tuple[str, str, str, str]] = []
    tokens_in = tokens_out = 0
    try:
        async with get_session() as s:
            ordnerliste = await urteil.lade_ordnerliste(s) if mit_ki else None
        for i, m in enumerate(mails, 1):
            async with get_session() as s:
                e = await kaskade.entscheide(s, m, graph, ohne_mail_id=m["id"], mit_ki=mit_ki,
                                             ordnerliste=ordnerliste)
            stufe = e["stufe"]
            schl = stufe if stufe != "ki" else f"ki/{e.get('sicherheit')}"
            if e.get("tokens"):
                tokens_in += e["tokens"]["in"]
                tokens_out += e["tokens"]["out"]
            if not e.get("ordner_id"):
                je_stufe[schl]["offen"] += 1
            elif e["ordner_id"] == m["ordner_id"]:
                je_stufe[schl]["richtig"] += 1
            else:
                je_stufe[schl]["falsch"] += 1
                fehler.append((schl, m["pfad"], e.get("ziel_pfad") or "", (m["betreff"] or "")[:60]))
            if i % 25 == 0:
                print(f"  … {i}/{len(mails)}", flush=True)
    finally:
        if graph:
            await graph.aclose()

    print(f"\n{'Stufe':<14}{'entsch.':>8}{'richtig':>8}{'falsch':>7}{'offen':>7}{'Quote':>8}")
    gesamt_richtig = gesamt_entsch = 0
    for schl in ["adresse", "domain", "thread", "ki/sicher", "ki/unsicher", "ki/nirgends", "unklar"]:
        c = je_stufe.get(schl)
        if not c:
            continue
        entsch = c["richtig"] + c["falsch"]
        quote = f"{100 * c['richtig'] / entsch:5.1f} %" if entsch else "   —"
        print(f"{schl:<14}{entsch:>8}{c['richtig']:>8}{c['falsch']:>7}{c['offen']:>7}{quote:>8}")
        if schl in ("adresse", "domain", "thread", "ki/sicher"):
            gesamt_richtig += c["richtig"]
            gesamt_entsch += entsch
    bewegt = gesamt_entsch
    print(f"\nWuerde bewegen: {bewegt} von {len(mails)} ({100 * bewegt / max(len(mails), 1):.0f} %), "
          f"davon richtig {gesamt_richtig} ({100 * gesamt_richtig / max(bewegt, 1):.1f} %)")
    if mit_ki:
        kosten = tokens_in / 1e6 * 1.0 + tokens_out / 1e6 * 5.0
        print(f"Haiku-Tokens: in={tokens_in} out={tokens_out} ≈ {kosten:.3f} USD (ohne Cache-Rabatt)")
    if fehler:
        print(f"\nFehlgriffe (max. {zeige}):")
        for schl, ist, soll, betreff in fehler[:zeige]:
            print(f"  [{schl}] liegt in: {ist}\n      geraten: {soll}\n      {betreff!r}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--n", type=int, default=200)
    p.add_argument("--monate", type=int, default=12)
    p.add_argument("--ohne-ki", action="store_true")
    p.add_argument("--seed", type=float, default=0.42)
    p.add_argument("--zeige", type=int, default=30)
    a = p.parse_args()
    asyncio.run(main(a.n, a.monate, not a.ohne_ki, a.seed, a.zeige))
