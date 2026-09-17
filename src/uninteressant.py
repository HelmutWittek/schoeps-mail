"""Grobe Vorstufe: bekannt uninteressante Post aus dem Posteingang wegraeumen.

Helmut hat am 2026-09-16 `Posteingang/Move/Spam, uninteressant` angelegt und 26
Mails von Hand hineingezogen (Messe-Werbung, Kaltakquise, Branchen-Newsletter).
Der Wunsch: klare Faelle sollen gar nicht erst in seinem Arbeitsvorrat landen.

Dieses Modul lernt genau daraus — und nur daraus. Es gibt **keine Heuristik auf
Betreff oder Inhalt**: entschieden wird ausschliesslich, wenn Helmut einen
Absender schon einmal selbst als uninteressant markiert hat. Der erste Kontakt
einer Domain bleibt immer liegen; das muss so sein, weil „noch nie abgelegt"
auch auf jede Kundenanfrage zutrifft.

**Befund, der den Umfang bestimmt (2026-09-17, Zufluss 90 Tage, 1.551 Mails):**
213 Mails kamen von 75 Domains ohne jede Ablage-Historie — davon liegen aber
**185 schon im Junk**, von Exchange gefiltert. Nur 24 landeten im Spam-Ordner,
3 im Posteingang. Die 26 markierten Mails stammen aus 70 Tagen, das sind
**~2,6 pro Woche** (Spitze 13 in der Woche vom 07.09.). Der Automat ersetzt also
wenige Handbewegungen — sein Wert liegt bei den Wiederholungstaetern, die
Exchange NICHT filtert.

**Vier Sicherheitsnetze**, weil der Posteingang Helmuts Arbeitsplatz ist und
eine falsch weggeraeumte Mail dort echten Schaden macht:

1. Absender der eigenen Domain werden nie angefasst (Kollegen).
2. Wer Evidenz in einem Zielordner hat, wird nie weggeraeumt — auf der Ebene,
   die entscheidet. `dhd.news@dhd-audio.de` (Newsletter, als Spam markiert) hat
   0 Evidenz, die Domain `dhd-audio.de` aber 6: die Adress-Regel greift, die
   Domain-Regel nicht.
3. Hat Helmut je an diese Adresse oder Domain geschrieben, gilt sie als
   Geschaeftskontakt. Gemessen: bei allen 16 Top-Kandidaten 0 gesendete Mails —
   das Netz kostet also nichts und faengt den Fall, wenn er kommt.
4. Laeuft ein Dialog (die `conversationId` taucht in den Gesendeten auf), bleibt
   die Mail liegen, auch wenn der Absender markiert war.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.regel import ist_anbieter, ist_eigene

log = logging.getLogger("schoepsmail.uninteressant")

SPAM_PFAD = os.getenv("SPAM_PFAD", "Posteingang/Move/Spam, uninteressant")
# Eine Handbewegung reicht bei der Adresse; fuer eine ganze Domain verlangen wir
# zwei, sonst sperrt ein einzelner Newsletter die Post einer ganzen Firma.
SPAM_MIN_DOMAIN = int(os.getenv("SPAM_MIN_DOMAIN", "2"))


async def _hat_gesendet(s: AsyncSession, adresse: str, domain: str) -> bool:
    """Hat Helmut je an diese Adresse oder Domain geschrieben? (Netz 3)"""
    r = await s.execute(text("""
        SELECT EXISTS (
            SELECT 1 FROM mail g JOIN ordner o ON o.id = g.ordner_id
             WHERE o.pfad = 'Gesendete Elemente' AND g.an IS NOT NULL
               AND EXISTS (SELECT 1 FROM unnest(g.an) a
                            WHERE lower(a) = :adr OR lower(a) LIKE :dom))
    """), {"adr": adresse, "dom": "%@" + domain if domain else "%@\x00"})
    return bool(r.scalar())


async def pruefe(s: AsyncSession, mail: dict[str, Any]) -> str | None:
    """Ist dieser Absender als uninteressant belegt? Liefert den Grund oder None."""
    adresse = (mail.get("von_adresse") or "").strip().lower()
    domain = (mail.get("von_domain") or "").strip().lower()
    if not adresse or not domain:
        return None
    if ist_eigene(domain):  # Netz 1
        return None

    r = await s.execute(text("""
        SELECT
          -- Handbewegungen in den Spam-Ordner, je Adresse und je Domain
          count(*) FILTER (WHERE m.von_adresse = :adr),
          count(*) FILTER (WHERE m.von_domain = :dom),
          -- Evidenz in echten Zielordnern (Netz 2)
          (SELECT count(*) FROM mail_evidenz e WHERE e.von_adresse = :adr),
          (SELECT count(*) FROM mail_evidenz e WHERE e.von_domain = :dom)
          FROM mail_bewegung b
          JOIN ordner o ON o.id = b.nach_ordner_id
          JOIN mail m ON m.id = b.mail_id
         WHERE o.pfad = :spam AND b.quelle = 'hand'
           AND (m.von_adresse = :adr OR m.von_domain = :dom)
    """), {"adr": adresse, "dom": domain, "spam": SPAM_PFAD})
    hand_adr, hand_dom, ev_adr, ev_dom = r.fetchone()

    treffer = None
    if hand_adr and not ev_adr:
        treffer = f"{adresse} wurde {hand_adr}x von Hand als uninteressant abgelegt"
    elif hand_dom >= SPAM_MIN_DOMAIN and not ev_dom and not ist_anbieter(domain):
        treffer = f"Domain {domain}: {hand_dom} Mails von Hand als uninteressant abgelegt"
    if not treffer:
        return None

    if await _hat_gesendet(s, adresse, domain):  # Netz 3
        log.info("Uninteressant-Regel gestoppt: an %s wurde schon geschrieben", adresse)
        return None

    # Netz 4: laeuft ein Dialog in diesem Thread?
    if mail.get("conversation_id"):
        r = await s.execute(text("""
            SELECT EXISTS (
                SELECT 1 FROM mail g JOIN ordner o ON o.id = g.ordner_id
                 WHERE o.pfad = 'Gesendete Elemente' AND g.conversation_id = :c)
        """), {"c": mail["conversation_id"]})
        if r.scalar():
            log.info("Uninteressant-Regel gestoppt: Thread hat eine Antwort von Helmut")
            return None
    return treffer
