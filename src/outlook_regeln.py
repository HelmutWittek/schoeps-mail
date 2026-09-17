"""Spiegel der Outlook-Posteingangsregeln — und was sie ueber die Ablage sagen.

Warum das hier steht: neben diesem Worker laeuft im Postfach eine zweite
Automatik mit 175 Regeln. Sie greift bei der Zustellung, also vor jeder Stufe
der Kaskade, und sie war bis jetzt unsichtbar — weder im Index noch im
Protokoll tauchte auf, dass eine Mail per Regel im Ordner landete. Zwei Folgen
hat das:

1. **Die Statistik hat die Regeln laengst mitgelernt.** Eine per Regel
   abgelegte Mail kommt ohne `auto-*`-Kategorie an und zaehlt in `mail_evidenz`
   mit Gewicht 2, also wie eine Handablage. Das ist richtig so — eine Regel ist
   Helmuts Hand, nur vorab niedergeschrieben —, erklaert aber, warum manche
   Ordner (`❹ Marketing/Presse`: 40 Regeln) so scharfe Absenderprofile haben.
2. **Die Regeln wissen Dinge, die die Statistik nicht weiss.** Wer eine Regel
   fuer einen Absender angelegt hat, hat entschieden. Steht hinter der Regel
   keine Ablage-Historie, kommt die Kaskade trotzdem nicht darauf.

Dieses Modul spiegelt die Regeln nur (`spiegle`) und vergleicht sie mit der
Historie (`hygiene`). Geaendert wird im Postfach, nie hier — der Spiegel ist
Lesestoff fuer Bericht und spaetere Entscheidungen, keine Steuerung.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text

from src.db import get_session
from src.graph import Graph

log = logging.getLogger("schoepsmail.outlook_regeln")

# Ab wie vielen Tagen ohne eine einzige Mail des Regel-Absenders gilt eine
# aktive Regel als eingeschlafen. Zwei Jahre, damit Messe- und Jahresrhythmen
# (IBC, AES, Tonmeistertagung) nicht als tot gemeldet werden.
TOT_AB_TAGEN = 730


async def spiegle(graph: Graph) -> dict[str, int]:
    """Regeln holen und in `outlook_regel` abgleichen. Reiner Lesevorgang gegen Graph."""
    regeln = await graph.posteingangs_regeln()
    ids: list[str] = []
    neu = geaendert = 0
    async with get_session() as s:
        for r in regeln:
            ids.append(r["id"])
            aktionen = r.get("actions") or {}
            zeile = {
                "id": r["id"],
                "name": r.get("displayName") or "(ohne Namen)",
                "sequenz": r.get("sequence"),
                "aktiv": bool(r.get("isEnabled")),
                "hat_fehler": bool(r.get("hasError")),
                "bedingungen": json.dumps(r.get("conditions") or {}, ensure_ascii=False),
                "ausnahmen": json.dumps(r.get("exceptions") or {}, ensure_ascii=False),
                "aktionen": json.dumps(aktionen, ensure_ascii=False),
                "ziel": aktionen.get("moveToFolder") or aktionen.get("copyToFolder"),
            }
            # `geaendert_am` nur setzen, wenn sich inhaltlich etwas bewegt hat —
            # die Reihenfolge (`sequenz`) verschiebt sich schon, wenn irgendwo
            # eine Regel eingefuegt wird, und ist deshalb kein Merkmal.
            res = await s.execute(text("""
                INSERT INTO outlook_regel (id, name, sequenz, aktiv, hat_fehler,
                                           bedingungen, ausnahmen, aktionen, ziel_ordner_id)
                VALUES (:id, :name, :sequenz, :aktiv, :hat_fehler,
                        CAST(:bedingungen AS jsonb), CAST(:ausnahmen AS jsonb),
                        CAST(:aktionen AS jsonb), :ziel)
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name, sequenz = EXCLUDED.sequenz,
                    aktiv = EXCLUDED.aktiv, hat_fehler = EXCLUDED.hat_fehler,
                    bedingungen = EXCLUDED.bedingungen, ausnahmen = EXCLUDED.ausnahmen,
                    aktionen = EXCLUDED.aktionen, ziel_ordner_id = EXCLUDED.ziel_ordner_id,
                    gesehen_am = now(), verschwunden_am = NULL,
                    geaendert_am = CASE
                        WHEN outlook_regel.aktiv IS DISTINCT FROM EXCLUDED.aktiv
                          OR outlook_regel.bedingungen IS DISTINCT FROM EXCLUDED.bedingungen
                          OR outlook_regel.aktionen IS DISTINCT FROM EXCLUDED.aktionen
                        THEN now() ELSE outlook_regel.geaendert_am END
                RETURNING (xmax = 0) AS eingefuegt,
                          (geaendert_am IS NOT NULL
                           AND geaendert_am > now() - interval '5 seconds') AS frisch
            """), zeile)
            eingefuegt, frisch = res.fetchone()
            neu += 1 if eingefuegt else 0
            geaendert += 1 if (frisch and not eingefuegt) else 0
        weg = await s.execute(text("""
            UPDATE outlook_regel SET verschwunden_am = now()
             WHERE verschwunden_am IS NULL AND NOT (id = ANY(:ids))
        """), {"ids": ids})
    return {"gesehen": len(regeln), "neu": neu, "geaendert": geaendert,
            "verschwunden": weg.rowcount or 0}


async def hygiene() -> dict[str, list[dict[str, Any]]]:
    """Befunde ueber den Regelbestand — was widerspricht, was ist tot, was fehlt.

    Keine Bewertung im Code: jede Liste ist eine Beobachtung mit Zahlen daneben,
    entschieden wird von Hand im Postfach.
    """
    befunde: dict[str, list[dict[str, Any]]] = {}
    async with get_session() as s:
        # 1. Ziel weg: die Regel verschiebt in einen Ordner, den es nicht mehr gibt.
        r = await s.execute(text("""
            SELECT r.name, r.aktiv FROM outlook_regel r
             LEFT JOIN ordner o ON o.id = r.ziel_ordner_id AND o.verschwunden_am IS NULL
             WHERE r.verschwunden_am IS NULL AND r.ziel_ordner_id IS NOT NULL AND o.id IS NULL
             ORDER BY r.aktiv DESC, r.name
        """))
        befunde["ziel_weg"] = [{"regel": a, "aktiv": b} for a, b in r.fetchall()]

        # 2. Leer: ohne Bedingung faengt eine Regel alles, ohne Aktion nichts.
        r = await s.execute(text("""
            SELECT name, aktiv FROM outlook_regel
             WHERE verschwunden_am IS NULL
               AND (bedingungen = CAST('{}' AS jsonb) OR aktionen = CAST('{}' AS jsonb))
             ORDER BY aktiv DESC, name
        """))
        befunde["leer"] = [{"regel": a, "aktiv": b} for a, b in r.fetchall()]

        # 3. Outlook meldet die Regel selbst als fehlerhaft.
        r = await s.execute(text("""
            SELECT name, aktiv FROM outlook_regel
             WHERE verschwunden_am IS NULL AND hat_fehler ORDER BY aktiv DESC, name
        """))
        befunde["fehlerhaft"] = [{"regel": a, "aktiv": b} for a, b in r.fetchall()]

        # 4. Derselbe Absender in mehreren Regeln mit verschiedenen Zielen.
        #    Es gewinnt die mit der kleinsten Sequenz — die andere ist tote Absicht.
        r = await s.execute(text("""
            SELECT a.adresse, count(DISTINCT a.ziel_ordner_id) AS ziele,
                   string_agg(DISTINCT a.name || CASE WHEN a.aktiv THEN '' ELSE ' (aus)' END, ' | ')
              FROM outlook_regel_absender a
             WHERE a.adresse IS NOT NULL
             GROUP BY a.adresse HAVING count(DISTINCT a.ziel_ordner_id) > 1
             ORDER BY 2 DESC, 1
        """))
        befunde["doppelt"] = [{"adresse": a, "ziele": z, "regeln": n} for a, z, n in r.fetchall()]

        # 5. Widerspruch: die Regel zeigt woanders hin als die tatsaechliche Ablage
        #    des Absenders. Meist ein Ordner, der spaeter umbenannt oder geteilt
        #    wurde — die Regel fuellt dann weiter den alten.
        r = await s.execute(text("""
            WITH regel AS (
                SELECT a.adresse, a.name AS regel, o.pfad AS ziel
                  FROM outlook_regel_absender a
                  JOIN ordner o ON o.id = a.ziel_ordner_id AND o.verschwunden_am IS NULL
                 WHERE a.adresse IS NOT NULL AND a.aktiv
            ), ablage AS (
                SELECT e.von_adresse AS adresse, e.pfad,
                       sum(e.gewicht) AS gewicht,
                       row_number() OVER (PARTITION BY e.von_adresse
                                          ORDER BY sum(e.gewicht) DESC) AS rang,
                       sum(sum(e.gewicht)) OVER (PARTITION BY e.von_adresse) AS gesamt
                  FROM mail_evidenz e
                 WHERE e.von_adresse IN (SELECT adresse FROM regel)
                 GROUP BY e.von_adresse, e.pfad
            )
            SELECT r.adresse, r.regel, r.ziel, ab.pfad, ab.gewicht, ab.gesamt
              FROM regel r JOIN ablage ab ON ab.adresse = r.adresse AND ab.rang = 1
             WHERE ab.pfad <> r.ziel
               AND ab.gewicht::float / ab.gesamt >= 0.8
               AND ab.gesamt >= 4
             ORDER BY ab.gesamt DESC
        """))
        befunde["widerspruch"] = [
            {"adresse": a, "regel": rg, "regel_ziel": z, "tatsaechlich": p,
             "anteil": round(g / ges, 2), "gewicht": int(ges)}
            for a, rg, z, p, g, ges in r.fetchall()]

        # 6. Wissen, das nur in der Regel steht: aktiver Absender ohne belastbare
        #    Evidenz. Genau hier koennte die Kaskade von den Regeln lernen.
        r = await s.execute(text("""
            SELECT a.adresse, a.name, o.pfad,
                   COALESCE((SELECT sum(gewicht) FROM mail_evidenz e
                              WHERE e.von_adresse = a.adresse), 0) AS evidenz
              FROM outlook_regel_absender a
              JOIN ordner o ON o.id = a.ziel_ordner_id AND o.verschwunden_am IS NULL
             WHERE a.adresse IS NOT NULL AND a.aktiv
               AND COALESCE((SELECT sum(gewicht) FROM mail_evidenz e
                              WHERE e.von_adresse = a.adresse), 0) < 2
             ORDER BY a.adresse
        """))
        befunde["ohne_evidenz"] = [{"adresse": a, "regel": n, "ziel": p, "evidenz": int(g)}
                                   for a, n, p, g in r.fetchall()]

        # 7. Eingeschlafen: aktive Regel, deren Absender seit TOT_AB_TAGEN keine
        #    Mail mehr geschickt hat (in keinem Ordner).
        r = await s.execute(text("""
            SELECT DISTINCT a.adresse, a.name,
                   (SELECT max(m.empfangen_am) FROM mail m
                     WHERE m.von_adresse = a.adresse AND m.entfernt_am IS NULL) AS letzte
              FROM outlook_regel_absender a
             WHERE a.adresse IS NOT NULL AND a.aktiv
               AND COALESCE((SELECT max(m.empfangen_am) FROM mail m
                              WHERE m.von_adresse = a.adresse AND m.entfernt_am IS NULL),
                            CAST('1970-01-01' AS timestamptz))
                   < now() - make_interval(days => :tage)
             ORDER BY 3 NULLS FIRST
        """), {"tage": TOT_AB_TAGEN})
        befunde["eingeschlafen"] = [
            {"adresse": a, "regel": n, "letzte_mail": str(d)[:10] if d else "nie"}
            for a, n, d in r.fetchall()]
    return befunde


async def kennzahlen() -> dict[str, int]:
    async with get_session() as s:
        r = await s.execute(text("""
            SELECT count(*) FILTER (WHERE verschwunden_am IS NULL),
                   count(*) FILTER (WHERE verschwunden_am IS NULL AND aktiv),
                   count(*) FILTER (WHERE verschwunden_am IS NOT NULL)
              FROM outlook_regel
        """))
        gesamt, aktiv, weg = r.fetchone()
        r = await s.execute(text(
            "SELECT count(DISTINCT adresse) FROM outlook_regel_absender WHERE aktiv"))
        adressen = r.scalar_one()
    return {"regeln": gesamt, "aktiv": aktiv, "verschwunden": weg, "adressen_aktiv": adressen}
