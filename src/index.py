"""Der Metadaten-Index: Ordnerbaum spiegeln, Mails je Ordner per Delta nachziehen.

Zwei Schritte, die der Worker in jedem Zyklus laufen laesst:

1. `spiegle_ordner` — alle Ordner aus Graph, Pfad aus der Elternkette, Upsert
   in `ordner`; was Graph nicht mehr liefert, bekommt `verschwunden_am`.
   Arbeitsordner werden ueber die Well-Known-Namen erkannt (sprachunabhaengig)
   und an ihre Unterordner vererbt — ausser beim Posteingang, dessen
   Unterordner die Sammelordner des Nutzers sind und echte Ziele.
2. `sync_mails` — je Ordner eine Delta-Runde. Der erste Lauf holt alles
   (~50.000 Metadatensaetze), danach nur Aenderungen. Commit je Seite, damit
   ein Abbruch mitten in 'Gesendete Elemente' nicht alles verwirft; der
   Delta-Link wird erst mit der letzten Seite gespeichert, ein abgebrochener
   Lauf beginnt den Ordner also von vorn — korrekt, nur langsamer.

Verschobene Mails: Graph meldet sie im alten Ordner als `@removed` und im
neuen als neu. Die Reihenfolge, in der die Ordner synchronisiert werden, ist
beliebig, deshalb setzt die Entfernung `entfernt_am` NUR, wenn die Mail im
Index noch in genau diesem Ordner liegt — sonst hat der neue Ordner sie schon
uebernommen.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_session
from src.graph import Graph

log = logging.getLogger("schoepsmail.index")

# Well-Known-Ordner, die nie Ziel und nie Evidenz sind. `inbox` steht hier,
# vererbt die Eigenschaft aber NICHT (siehe Modul-Docstring).
ARBEITS_WELL_KNOWN = {
    "inbox", "drafts", "sentitems", "deleteditems", "junkemail", "outbox",
    "archive", "conversationhistory", "syncissues", "recoverableitemsdeletions",
}
VERERBT_NICHT = {"inbox"}

# Zusaetzliche Arbeitsordner ueber den Pfad (der Nutzer-Ordner `Move` und
# Outlook-Systemordner ohne Well-Known-Namen).
ARBEITS_PFADE = {
    os.getenv("MOVE_PFAD", "Posteingang/Move"),
    "RSS-Feeds", "Spambericht", "Unwanted", "Infizierte Objekte",
    "Erneut erinnern aktiviert", "Gefundene Objekte", "GroupWise Archive",
    "Synchronisierungsprobleme", "Verlauf der Unterhaltung",
}

# Ordner, deren Mails gar nicht erst geholt werden: kein Nutzen fuer die
# Entscheidung, aber viele Datensaetze. Gesendete und Junk werden geholt —
# Gesendete beantwortet "hat Helmut je geantwortet", Junk "lag je im Junk".
SYNC_AUS_WELL_KNOWN = {"deleteditems", "outbox", "drafts", "syncissues",
                       "recoverableitemsdeletions", "conversationhistory"}


def _zeit(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def _adresse(ea: dict[str, Any] | None) -> tuple[str | None, str | None]:
    ea = (ea or {}).get("emailAddress") or {}
    adr = (ea.get("address") or "").strip().lower() or None
    name = (ea.get("name") or "").strip().replace("\x00", "") or None
    return adr, name


def _mail_zeile(m: dict[str, Any]) -> dict[str, Any]:
    adr, name = _adresse(m.get("from"))
    an = [a for a, _ in (_adresse(r) for r in (m.get("toRecipients") or [])) if a]
    return {
        "id": m["id"],
        "ordner_id": m.get("parentFolderId"),
        "conversation_id": m.get("conversationId"),
        "imid": m.get("internetMessageId"),
        "von_adresse": adr,
        "von_name": name,
        "von_domain": adr.split("@", 1)[1] if adr and "@" in adr else None,
        "an": an,
        "betreff": (m.get("subject") or "").replace("\x00", "")[:500],
        "vorschau": (m.get("bodyPreview") or "").replace("\x00", "")[:255],
        "empfangen_am": _zeit(m.get("receivedDateTime")),
        "gesendet_am": _zeit(m.get("sentDateTime")),
        "kategorien": list(m.get("categories") or []),
        "ist_gelesen": m.get("isRead"),
        "hat_anhang": m.get("hasAttachments"),
    }


# ---------------------------------------------------------------- Ordnerbaum
async def spiegle_ordner(graph: Graph) -> dict[str, dict[str, Any]]:
    """Ordnerbaum nach `ordner` spiegeln. Liefert id -> {pfad, arbeitsordner, well_known}."""
    wk = await graph.well_known_ids()
    wk_name = {oid: name for name, oid in wk.items()}
    roh = await graph.alle_ordner()
    nach_id = {o["id"]: o for o in roh}
    wurzel = wk.get("msgfolderroot")

    def pfad_von(o: dict[str, Any]) -> str:
        teile = [o["displayName"]]
        eltern = o.get("parentFolderId")
        while eltern and eltern != wurzel and eltern in nach_id:
            e = nach_id[eltern]
            teile.append(e["displayName"])
            eltern = e.get("parentFolderId")
        return "/".join(reversed(teile))

    def ist_arbeit(o: dict[str, Any]) -> bool:
        name = wk_name.get(o["id"])
        if name in ARBEITS_WELL_KNOWN:
            return True
        # Vererbung von einem Arbeitsordner-Vorfahren (ausser inbox)
        eltern = o.get("parentFolderId")
        while eltern and eltern != wurzel and eltern in nach_id:
            en = wk_name.get(eltern)
            if en in ARBEITS_WELL_KNOWN and en not in VERERBT_NICHT:
                return True
            if pfad_von(nach_id[eltern]) in ARBEITS_PFADE:
                return True
            eltern = nach_id[eltern].get("parentFolderId")
        return False

    aus: dict[str, dict[str, Any]] = {}
    async with get_session() as s:
        for o in roh:
            pfad = pfad_von(o)
            arbeit = ist_arbeit(o) or pfad in ARBEITS_PFADE
            eltern = o.get("parentFolderId")
            if eltern == wurzel or eltern not in nach_id:
                eltern = None
            await s.execute(text("""
                INSERT INTO ordner (id, eltern_id, name, pfad, anzahl, ist_arbeitsordner, gesehen_am)
                VALUES (:id, :eltern, :name, :pfad, :anzahl, :arbeit, now())
                ON CONFLICT (id) DO UPDATE SET
                    eltern_id = EXCLUDED.eltern_id, name = EXCLUDED.name, pfad = EXCLUDED.pfad,
                    anzahl = EXCLUDED.anzahl, ist_arbeitsordner = EXCLUDED.ist_arbeitsordner,
                    verschwunden_am = NULL, gesehen_am = now()
            """), {"id": o["id"], "eltern": eltern, "name": o["displayName"], "pfad": pfad,
                   "anzahl": int(o.get("totalItemCount") or 0), "arbeit": arbeit})
            aus[o["id"]] = {"pfad": pfad, "arbeitsordner": arbeit, "well_known": wk_name.get(o["id"]),
                            "anzahl": int(o.get("totalItemCount") or 0)}
        r = await s.execute(text("""
            UPDATE ordner SET verschwunden_am = now()
             WHERE verschwunden_am IS NULL AND NOT (id = ANY(:ids))
            RETURNING pfad
        """), {"ids": list(aus)})
        weg = [row[0] for row in r.fetchall()]
    if weg:
        log.info("Ordner verschwunden: %s", weg)
    log.info("Ordnerbaum gespiegelt: %d Ordner, %d Arbeitsordner",
             len(aus), sum(1 for v in aus.values() if v["arbeitsordner"]))
    return aus


# --------------------------------------------------------------------- Mails
async def _schreibe_seite(s: AsyncSession, ordner_id: str, eintraege: list[dict[str, Any]]) -> tuple[int, int]:
    neu_oder_geaendert = 0
    entfernt = 0
    for m in eintraege:
        if "@removed" in m:
            r = await s.execute(text("""
                UPDATE mail SET entfernt_am = now(), aktualisiert_am = now()
                 WHERE id = :id AND ordner_id = :ordner AND entfernt_am IS NULL
            """), {"id": m["id"], "ordner": ordner_id})
            entfernt += r.rowcount or 0
            continue
        z = _mail_zeile(m)
        if not z["ordner_id"]:
            z["ordner_id"] = ordner_id
        await s.execute(text("""
            INSERT INTO mail (id, ordner_id, conversation_id, internet_message_id,
                              von_adresse, von_name, von_domain, an, betreff, vorschau,
                              empfangen_am, gesendet_am, kategorien, ist_gelesen, hat_anhang,
                              entfernt_am, gesehen_am, aktualisiert_am)
            VALUES (:id, :ordner_id, :conversation_id, :imid, :von_adresse, :von_name, :von_domain,
                    :an, :betreff, :vorschau, :empfangen_am, :gesendet_am, :kategorien,
                    :ist_gelesen, :hat_anhang, NULL, now(), now())
            ON CONFLICT (id) DO UPDATE SET
                ordner_id = EXCLUDED.ordner_id, conversation_id = EXCLUDED.conversation_id,
                internet_message_id = EXCLUDED.internet_message_id,
                von_adresse = EXCLUDED.von_adresse, von_name = EXCLUDED.von_name,
                von_domain = EXCLUDED.von_domain, an = EXCLUDED.an, betreff = EXCLUDED.betreff,
                vorschau = EXCLUDED.vorschau, empfangen_am = EXCLUDED.empfangen_am,
                gesendet_am = EXCLUDED.gesendet_am, kategorien = EXCLUDED.kategorien,
                ist_gelesen = EXCLUDED.ist_gelesen, hat_anhang = EXCLUDED.hat_anhang,
                entfernt_am = NULL, aktualisiert_am = now()
        """), z)
        neu_oder_geaendert += 1
    return neu_oder_geaendert, entfernt


async def sync_ordner(graph: Graph, ordner_id: str, pfad: str) -> tuple[int, int]:
    """Eine Delta-Runde fuer einen Ordner, Commit je Seite. Liefert (geschrieben, entfernt)."""
    async with get_session() as s:
        r = await s.execute(text("SELECT delta_link FROM ordner WHERE id = :id"), {"id": ordner_id})
        row = r.fetchone()
        delta_link = row[0] if row else None
    gesamt_neu = gesamt_weg = 0
    seiten = 0
    async for eintraege, fertig in graph.delta_seiten(ordner_id, delta_link):
        seiten += 1
        async with get_session() as s:
            n, w = await _schreibe_seite(s, ordner_id, eintraege)
            gesamt_neu += n
            gesamt_weg += w
            if fertig:
                await s.execute(text("""
                    UPDATE ordner SET delta_link = :dl, delta_am = now() WHERE id = :id
                """), {"dl": fertig, "id": ordner_id})
        if seiten % 20 == 0:
            log.info("  %s: %d Seiten, %d Mails bisher", pfad, seiten, gesamt_neu)
    if gesamt_neu or gesamt_weg:
        log.info("%s: %d geschrieben, %d entfernt (%d Seiten)", pfad, gesamt_neu, gesamt_weg, seiten)
    return gesamt_neu, gesamt_weg


async def sync_mails(graph: Graph, ordner: dict[str, dict[str, Any]] | None = None) -> tuple[int, int]:
    """Alle synchronisierbaren Ordner nachziehen. Liefert Summen (geschrieben, entfernt)."""
    if ordner is None:
        ordner = await spiegle_ordner(graph)
    neu = weg = 0
    fehler: list[str] = []
    # Kleine Ordner zuerst: der Erstlauf zeigt so frueh, ob alles stimmt, bevor
    # 'Gesendete Elemente' mit 34.000 Mails dran ist.
    for oid, info in sorted(ordner.items(), key=lambda kv: kv[1]["anzahl"]):
        if info["well_known"] in SYNC_AUS_WELL_KNOWN:
            continue
        # Unterordner ausgeschlossener Systemordner ebenfalls ueberspringen
        # (z.B. Geloeschte Elemente/…) — sie sind Arbeitsordner, aber nicht
        # jeder Arbeitsordner wird uebersprungen (Sent, Junk, Move nicht).
        if info["arbeitsordner"] and info["pfad"].split("/")[0] in {
            "Gelöschte Elemente", "Synchronisierungsprobleme", "Verlauf der Unterhaltung",
        }:
            continue
        try:
            n, w = await sync_ordner(graph, oid, info["pfad"])
        except Exception as exc:
            # Ein Ordner darf die anderen nicht kosten — aber der Zyklus gilt
            # als gescheitert (Lehre aus dem LifeOS-imap-worker: ein still
            # uebersprungener Ordner blieb dort 48 h lang unbemerkt).
            log.error("Sync von %s fehlgeschlagen: %s", info["pfad"], exc)
            fehler.append(info["pfad"])
            continue
        neu += n
        weg += w
    if fehler:
        raise RuntimeError(f"Sync fehlgeschlagen fuer: {', '.join(fehler)}")
    return neu, weg
