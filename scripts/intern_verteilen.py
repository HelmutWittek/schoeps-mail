"""Sammelordner aufloesen: Mails aus `Posteingang/SCHOEPS intern` (oder einem
anderen Sammelordner) nach 1. Thread und 2. Thema in die Themenordner verteilen.

Entscheidung Helmut 2026-09-14: „SCHOEPS intern soll kein bevorzugter Zielordner
sein. Die darin befindlichen Mails sollten auf die Themenordner verteilt werden,
nach 1. Thread und 2. Thema."

Ablauf je Mail: die normale Kaskade (Adresse/Domain fuer externe Absender,
Thread, Kandidaten, Haiku-Urteil). Der Sammelordner selbst ist Arbeitsordner
und damit weder Ziel noch Evidenz. Was `sicher` ist, wird verschoben und
bekommt die Kategorie der Stufe (`auto-thread` / `auto-ki` / `auto-regel`);
was unklar bleibt, bleibt liegen und wird gezaehlt.

Verarbeitet wird je Konversation und **juengste zuerst**: sobald eine Mail
eines Threads bewegt ist, zieht sie den Rest per Stufe 2 nach — der Index wird
sofort nach jedem Move aktualisiert, nicht erst beim naechsten Delta-Lauf.

    # Trockenlauf ohne LLM (kostenlos): wie viel loest der Thread allein?
    docker compose exec worker python scripts/intern_verteilen.py --ohne-ki
    # Trockenlauf mit LLM auf einer Stichprobe
    docker compose exec worker python scripts/intern_verteilen.py --limit 100
    # Scharf, in Stapeln
    docker compose exec worker python scripts/intern_verteilen.py --ausfuehren --limit 500

Nichts wird geloescht; jede Bewegung steht in `regel_entscheidung`
(`ausgefuehrt = true`), und die Kategorie macht sie im Client erkennbar.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from collections import Counter

from sqlalchemy import text

from src import kaskade, urteil
from src.db import get_session
from src.graph import Graph

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("schoepsmail.verteilen")

FELDER = "m.id, m.ordner_id, m.conversation_id, m.von_adresse, m.von_name, m.von_domain, m.an, m.betreff, m.vorschau, m.empfangen_am, m.kategorien"


async def lade_mails(pfad: str, limit: int | None, monate: int | None) -> list[dict]:
    async with get_session() as s:
        r = await s.execute(text(f"""
            SELECT {FELDER}
              FROM mail m JOIN ordner o ON o.id = m.ordner_id
             WHERE o.pfad = :pfad AND m.entfernt_am IS NULL
               AND (CAST(:monate AS integer) IS NULL
                    OR m.empfangen_am >= now() - make_interval(months => CAST(:monate AS integer)))
             ORDER BY m.conversation_id, m.empfangen_am DESC
             {"LIMIT :limit" if limit else ""}
        """), {"pfad": pfad, "monate": monate, **({"limit": limit} if limit else {})})
        return [dict(row._mapping) for row in r.fetchall()]


async def verschiebe(graph: Graph, m: dict, e: dict) -> None:
    """Move + Kategorie in Graph, dann den Index sofort nachziehen."""
    kat = kaskade.kategorie(e)
    neue = [k for k in (m.get("kategorien") or []) if not k.startswith("auto-")] + [kat]
    await graph.setze_kategorien(m["id"], neue)
    await graph.verschiebe(m["id"], e["ordner_id"])
    async with get_session() as s:
        await s.execute(text("""
            UPDATE mail SET ordner_id = :o, kategorien = :k, aktualisiert_am = now() WHERE id = :id
        """), {"o": e["ordner_id"], "k": neue, "id": m["id"]})
        await kaskade.protokolliere(s, m["id"], e, dry_run=False, ausgefuehrt=True)


async def main(pfad: str, limit: int | None, monate: int | None, mit_ki: bool,
               ausfuehren: bool, zeige: int) -> None:
    mails = await lade_mails(pfad, limit, monate)
    print(f"{len(mails)} Mails in {pfad!r}"
          f"{f' (letzte {monate} Monate)' if monate else ''}, KI={'an' if mit_ki else 'aus'}, "
          f"{'SCHARF' if ausfuehren else 'Trockenlauf'}")
    graph = Graph()
    zaehler: Counter = Counter()
    ziele: Counter = Counter()
    tokens_in = tokens_out = 0
    beispiele: list[tuple[str, str, str]] = []
    try:
        async with get_session() as s:
            ordnerliste = await urteil.lade_ordnerliste(s) if mit_ki else None
        for i, m in enumerate(mails, 1):
            async with get_session() as s:
                e = await kaskade.entscheide(s, m, graph if mit_ki else None, ohne_mail_id=None,
                                             mit_ki=mit_ki, ordnerliste=ordnerliste)
            if e.get("tokens"):
                tokens_in += e["tokens"]["in"]
                tokens_out += e["tokens"]["out"]
            schl = e["stufe"] if e["stufe"] != "ki" else f"ki/{e.get('sicherheit')}"
            zaehler[schl] += 1
            if kaskade.bewegt(e):
                ziele[e["ziel_pfad"]] += 1
                if len(beispiele) < zeige:
                    beispiele.append((schl, e["ziel_pfad"], (m["betreff"] or "")[:70]))
                if ausfuehren:
                    try:
                        await verschiebe(graph, m, e)
                        zaehler["bewegt"] += 1
                    except Exception as exc:  # noqa: BLE001 — eine Mail darf den Stapel nicht kosten
                        log.error("Move fehlgeschlagen fuer %s: %s", m["id"][:20], exc)
                        zaehler["move_fehler"] += 1
                else:
                    async with get_session() as s:
                        await kaskade.protokolliere(s, m["id"], e, dry_run=True)
            if i % 100 == 0:
                print(f"  … {i}/{len(mails)}  {dict(zaehler)}", flush=True)
    finally:
        await graph.aclose()

    print("\nErgebnis je Stufe:")
    for k, v in sorted(zaehler.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<14}{v:>6}")
    waere = sum(v for k, v in zaehler.items() if k in ("adresse", "domain", "thread", "ki/sicher"))
    print(f"\n{'Bewegt' if ausfuehren else 'Wuerde bewegen'}: {waere} von {len(mails)} "
          f"({100 * waere / max(len(mails), 1):.0f} %)")
    if mit_ki:
        print(f"Haiku-Tokens: in={tokens_in} out={tokens_out} ≈ {tokens_in / 1e6 + tokens_out / 1e6 * 5:.2f} USD")
    print("\nHaeufigste Ziele:")
    for pfad_, n in ziele.most_common(15):
        print(f"  {n:>5}  {pfad_}")
    if beispiele:
        print(f"\nBeispiele (max. {zeige}):")
        for schl, ziel, betreff in beispiele:
            print(f"  [{schl}] → {ziel}\n      {betreff!r}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--pfad", default="Posteingang/SCHOEPS intern")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--monate", type=int, default=None, help="nur Mails der letzten N Monate")
    p.add_argument("--ohne-ki", action="store_true")
    p.add_argument("--ausfuehren", action="store_true", help="wirklich verschieben (sonst Trockenlauf)")
    p.add_argument("--zeige", type=int, default=25)
    a = p.parse_args()
    asyncio.run(main(a.pfad, a.limit, a.monate, not a.ohne_ki, a.ausfuehren, a.zeige))
