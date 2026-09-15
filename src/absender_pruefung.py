"""Vorfilter vor Stufe 4: Absender, denen die KI nicht glauben soll.

Stufe 1–3 entscheiden nur aus der Ablage-Historie. Ein Spam-Absender hat keine,
faellt durch alle drei und bleibt in `Move` liegen — die Kaskade ist gegen Spam
also von Haus aus dicht. Erst Stufe 4 (`SORTIERER_KI=1`, Phase 3) entscheidet
OHNE Historie: sie liest die Mail. Damit wird jede Spam-Mail zum KI-Fall, und
ein Fehlurteil legt sie mit `auto-ki` in einen Themenordner.

Dieses Modul haelt sie davor an. Zwei Merkmale, beide gegen den Bestand
(114.352 Mails, Stand 2026-09-15) gemessen:

- **Unsichtbarer Absendername.** Uebernommen aus der privaten Email-Automation
  (`C:\\PROJEKTE\\Email-Verarbeitung`), wo eine Mail mit dem Anzeigenamen
  U+2800 (Braille-Blank) und gefaelschter Absenderadresse eine Akte erzeugt
  hat: nicht leer, also nicht auffaellig, aber unsichtbar. Hier: **0 Treffer**
  unter 463 Absendernamen mit Nicht-ASCII-Zeichen. Die Regel kostet nichts und
  faengt den Fall, wenn er kommt. Die beiden einzigen Namen mit „unsichtbaren"
  Zeichen im Bestand sind japanische Absender (`ysonoda@ktmail.tokai-u.jp`,
  `ken-usami@capcom.com`), die U+3000 als Trenner zwischen Nach- und Vornamen
  setzen — sie haben echten Text daneben und schlagen darum nicht an.

- **Junk-Historie ohne Gegengewicht.** 840 Mails von 255 Absendern lagen je im
  Junk-Ordner; nur 27 dieser Absender haben auch Evidenz in einem Zielordner.
  Diese 27 sind der Grund fuer das UND: Exchange schiebt auch Legitimes ins
  Junk (`slite.com` 22x im Junk, aber 341 Evidenz-Mails; `mail.anthropic.com`
  15 zu 45). Gesperrt wird darum nur, wer im Junk lag UND nie in einem
  Zielordner UND dort die Mehrheit seiner Post hat — dann ist er ohnehin ein
  reiner KI-Fall. Die dritte Bedingung trennt den Exchange-Fehlgriff vom
  Spammer: 214 der 228 betroffenen Absender liegen ausschliesslich im Junk,
  waehrend `no-reply@news.lawo.com` (echter Branchen-Hersteller) bei 1 von 3
  liegt und darum durchkommt.

Punycode (`xn--`) wird mitgeprueft, obwohl der Bestand **0** solche Domains
hat: der Test kostet einen Vergleich. Eine Homoglyph-Regel wie drueben gibt es
hier NICHT — Domains kommen aus Graph immer punycode-kodiert, gemischte
Schriftsysteme koennen im Domainfeld also gar nicht auftauchen.

Die Sperre haelt nur die KI an. Stufen 1–3 bleiben unberuehrt: wer Historie
hat, ist durch Helmuts Handablage legitimiert, und die soll ein Junk-Fehlgriff
von Exchange nicht aushebeln.
"""
from __future__ import annotations

import os
import unicodedata as ud
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.regel import ist_anbieter

# Zeichen ohne sichtbare Darstellung, die trotzdem einen "nicht leeren" Namen
# ergeben: Steuerzeichen (Cc), Formatzeichen (Cf: Zero-Width-Space, Word
# Joiner, BOM, RLO), Private Use (Co) und Surrogate (Cs).
UNSICHTBARE_KATEGORIEN = {"Cc", "Cf", "Co", "Cs"}

# Zeichen, die Unicode als sichtbar fuehrt, die aber leer rendern. Die
# Whitespace-Varianten (U+3000, U+00A0, …) fallen schon durch `str.strip()`.
FUELLZEICHEN = {
    "\u2800",  # Braille Pattern Blank — der Fall aus der privaten Automation
    "\u3164",  # Hangul Filler
    "\u115f", "\u1160",  # Hangul Choseong/Jungseong Filler
    "\u17b4", "\u17b5",  # Khmer inhaerente Vokale
    "\u2591", "\u2592",  # Schattenbloecke, in Spam als Fuellung gesehen
}

# Ab wie vielen Junk-Mails eine ganze Domain gesperrt wird (bei der Adresse
# reicht eine, bei der Domain waere das zu grob).
JUNK_MIN_DOMAIN = int(os.getenv("JUNK_MIN_DOMAIN", "3"))

# Anteil der Junk-Mails an allen Mails dieses Absenders. Gemessen 2026-09-15:
# von 228 Absendern mit Junk-Historie und ohne Evidenz liegen 214 AUSSCHLIESSLICH
# im Junk, 13 zu 50–99 %, genau einer darunter. Die Schwelle kostet also fast
# nichts und rettet den Fall, der sonst danebengeht: `no-reply@news.lawo.com`
# (Branchen-Hersteller, Newsletter) lag 1 von 3 Mails im Junk — ein Fehlgriff
# des Exchange-Filters, der den Absender sonst dauerhaft von Stufe 4 aussperrt.
JUNK_MIN_ANTEIL = float(os.getenv("JUNK_MIN_ANTEIL", "0.5"))


# ---------------------------------------------------------------- reine Logik
def unsichtbarer_name(name: str | None) -> bool:
    """Besteht der Anzeigename nur aus Zeichen, die nichts darstellen?

    Ein leerer oder fehlender Name ist NICHT verdaechtig (27 Mails im Bestand
    haben keinen) — verdaechtig ist die Tarnung: etwas, das wie ein Name
    aussieht, aber keiner ist.
    """
    if not name:
        return False
    hat_tarnung = any(ud.category(c) in UNSICHTBARE_KATEGORIEN or c in FUELLZEICHEN for c in name)
    if not hat_tarnung:
        return False
    rest = "".join(c for c in name
                   if ud.category(c) not in UNSICHTBARE_KATEGORIEN and c not in FUELLZEICHEN)
    return not rest.strip()


def punycode_domain(domain: str | None) -> bool:
    """Traegt die Domain ein Punycode-Label (`xn--`)? Dann steht ein anderes
    Schriftsystem dahinter, das lateinisch aussehen kann (`аpple.com`)."""
    if not domain:
        return False
    return any(teil.startswith("xn--") for teil in domain.strip().lower().split("."))


def form_verdacht(von_name: str | None, von_domain: str | None) -> str | None:
    """Formale Pruefung ohne DB. Liefert den Grund oder None."""
    if unsichtbarer_name(von_name):
        return "Absendername besteht nur aus unsichtbaren Zeichen"
    if punycode_domain(von_domain):
        return f"Punycode-Domain {von_domain}"
    return None


# -------------------------------------------------------------- mit Historie
async def junk_verdacht(s: AsyncSession, von_adresse: str | None, von_domain: str | None
                        ) -> str | None:
    """Liegt dieser Absender ueberwiegend im Junk, ohne je in einem Zielordner
    zu liegen?

    Drei Bedingungen, alle noetig: Junk-Historie, KEINE Evidenz, und der Junk
    stellt die Mehrheit seiner Post (`JUNK_MIN_ANTEIL`). Die Evidenz-Gegenprobe
    laeuft ueber `mail_evidenz` — die Sicht enthaelt nur Zielordner, Arbeits-
    ordner (Junk, Spambericht, Move, Sent) sind darin nicht. Wer dort auftaucht,
    wurde von Helmut je abgelegt und ist damit legitimiert.
    """
    adresse = (von_adresse or "").strip().lower()
    if not adresse:
        return None
    r = await s.execute(text("""
        SELECT count(*) FILTER (WHERE m.von_adresse = :a AND o.ist_arbeitsordner
                                  AND (o.pfad ILIKE '%Junk%' OR o.pfad ILIKE '%Spam%')),
               count(*) FILTER (WHERE m.von_adresse = :a),
               count(*) FILTER (WHERE m.von_domain = :d AND o.ist_arbeitsordner
                                  AND (o.pfad ILIKE '%Junk%' OR o.pfad ILIKE '%Spam%')),
               count(*) FILTER (WHERE m.von_domain = :d),
               (SELECT count(*) FROM mail_evidenz WHERE von_adresse = :a),
               (SELECT count(*) FROM mail_evidenz WHERE von_domain = :d)
          FROM mail m JOIN ordner o ON o.id = m.ordner_id
         WHERE m.von_adresse = :a OR m.von_domain = :d
    """), {"a": adresse, "d": (von_domain or "").strip().lower()})
    junk_a, ges_a, junk_d, ges_d, ev_a, ev_d = r.fetchone()
    if junk_a and not ev_a and junk_a / max(ges_a, 1) >= JUNK_MIN_ANTEIL:
        return f"{adresse} lag {junk_a} von {ges_a} Mails im Junk und nie in einem Zielordner"
    if (junk_d >= JUNK_MIN_DOMAIN and not ev_d and junk_d / max(ges_d, 1) >= JUNK_MIN_ANTEIL
            and not ist_anbieter(von_domain or "")):
        return f"Domain {von_domain} lag {junk_d} von {ges_d} Mails im Junk und nie in einem Zielordner"
    return None


async def pruefe(s: AsyncSession, mail: dict[str, Any]) -> str | None:
    """Gesamturteil des Vorfilters: Grund fuer die Sperre oder None."""
    return (form_verdacht(mail.get("von_name"), mail.get("von_domain"))
            or await junk_verdacht(s, mail.get("von_adresse"), mail.get("von_domain")))
