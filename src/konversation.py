"""Stufe 2 und 3 der Kaskade: der Thread und der Absender-Ordner-Bezug.

Stufe 2 — Konversation. Graph gibt jeder Mail eine `conversationId`. Liegt
der Rest des Threads in einem Zielordner, gehoert die neue Mail dorthin. Das
ist genau der Fall „Kollege antwortet dem Hauptabsender": die Antwort haengt
am Thread der Mail des Hauptabsenders, und der Absender-Statistik (Stufe 1)
waere der Kollege entgangen. In LifeOS ist die entsprechende References-Kante
die verlaesslichste Regel der Akten-Kaskade.

Streut der Thread ueber mehrere Ordner (Anteil unter `MIN_ANTEIL_THREAD`),
wird er NICHT entschieden, sondern liefert Kandidaten fuer Stufe 4.

Stufe 3 — Absender-Ordner-Bezug. Kein Entscheider, nur Kandidaten mit
Vorrang fuer das LLM: die Ordner, in denen dieser Absender in den letzten
`BEZUG_TAGE` Tagen lag, auch wenn seine Gesamtkonzentration unter 80 % liegt.
Fuer Kollegen (eigene Domain) zusaetzlich die Ordner ihrer juengsten Threads.
"""
from __future__ import annotations

import os
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.regel import auswerten

# Eine einzige andere Mail im Thread reicht: ein Thread ist eine harte Kante,
# keine Statistik. Mindestgewicht 1 = auch eine automatisch abgelegte Mail.
MIN_EVIDENZ_THREAD = float(os.getenv("THREAD_MIN_EVIDENZ", "1"))
MIN_ANTEIL_THREAD = float(os.getenv("THREAD_MIN_ANTEIL", "0.8"))
BEZUG_TAGE = int(os.getenv("BEZUG_TAGE", "180"))
MAX_KANDIDATEN = 4


async def thread_verteilung(s: AsyncSession, conversation_id: str, ohne_mail_id: str | None
                            ) -> list[tuple[str, str, float]]:
    r = await s.execute(text("""
        SELECT ordner_id, pfad, sum(gewicht)::float AS gew
          FROM mail_evidenz
         WHERE conversation_id = :c
           AND (CAST(:ohne AS text) IS NULL OR mail_id <> CAST(:ohne AS text))
         GROUP BY ordner_id, pfad
         ORDER BY gew DESC
    """), {"c": conversation_id, "ohne": ohne_mail_id})
    return [(row[0], row[1], float(row[2])) for row in r.fetchall()]


async def nach_thread(s: AsyncSession, conversation_id: str | None, ohne_mail_id: str | None = None
                      ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Liefert (Entscheidung | None, Kandidaten). Kandidaten sind gefuellt, wenn der
    Thread existiert, aber streut."""
    if not conversation_id:
        return None, []
    zeilen = await thread_verteilung(s, conversation_id, ohne_mail_id)
    if not zeilen:
        return None, []
    t = auswerten(zeilen, MIN_ANTEIL_THREAD, MIN_EVIDENZ_THREAD)
    if t:
        return {**t, "stufe": "thread", "schluessel": conversation_id,
                "begruendung": f"{t['treffer']:g} von {t['gesamt']:g} Mails desselben Threads "
                               f"liegen in {t['ziel_pfad']}"}, []
    return None, [{"ordner_id": o, "pfad": p, "gewicht": round(g, 1), "grund": "thread streut"}
                  for o, p, g in zeilen[:MAX_KANDIDATEN]]


async def absender_bezug(s: AsyncSession, von_adresse: str | None, ohne_mail_id: str | None = None
                         ) -> list[dict[str, Any]]:
    """Stufe 3: juengste Ordner dieses Absenders als Kandidaten (kein Entscheider)."""
    if not von_adresse:
        return []
    r = await s.execute(text("""
        SELECT ordner_id, pfad, sum(gewicht)::float AS gew, max(empfangen_am) AS zuletzt
          FROM mail_evidenz
         WHERE von_adresse = :a
           AND empfangen_am >= now() - make_interval(days => CAST(:tage AS integer))
           AND (CAST(:ohne AS text) IS NULL OR mail_id <> CAST(:ohne AS text))
         GROUP BY ordner_id, pfad
         ORDER BY gew DESC, zuletzt DESC
         LIMIT :n
    """), {"a": von_adresse.lower(), "tage": BEZUG_TAGE, "ohne": ohne_mail_id, "n": MAX_KANDIDATEN})
    return [{"ordner_id": row[0], "pfad": row[1], "gewicht": round(float(row[2]), 1),
             "grund": f"Absender lag hier zuletzt {row[3]:%d.%m.%Y}"} for row in r.fetchall()]
