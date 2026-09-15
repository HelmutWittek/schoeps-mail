"""Tests ohne DB und ohne LLM fuer den Absender-Vorfilter vor Stufe 4.

    python scripts/test_absender_pruefung.py

Die DB-Haelfte (`junk_verdacht`) ist hier nicht abgedeckt — sie braucht den
Index. Gemessen wurde sie am 2026-09-15 gegen den Bestand: 255 Junk-Absender,
davon 27 mit Evidenz in einem Zielordner (die bleiben erlaubt).
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("EIGENE_DOMAINS", "schoeps.de")

from src import absender_pruefung as ap  # noqa: E402

FAELLE = 0


def pruefe(bedingung: bool, name: str) -> None:
    global FAELLE
    FAELLE += 1
    if not bedingung:
        print(f"FEHLT: {name}")
        sys.exit(1)
    print(f"ok    {name}")


# Unsichtbare Namen — der Fall aus der privaten Email-Automation
pruefe(ap.unsichtbarer_name("⠀"), "Braille-Blank allein ist getarnt")
pruefe(ap.unsichtbarer_name("​​"), "Zero-Width-Space allein ist getarnt")
pruefe(ap.unsichtbarer_name("﻿ ⁠"), "BOM + Word Joiner mit Leerzeichen ist getarnt")
pruefe(ap.unsichtbarer_name("ㅤ"), "Hangul-Filler allein ist getarnt")
pruefe(ap.unsichtbarer_name("‮"), "Right-to-Left-Override allein ist getarnt")

# Echte Namen duerfen nie anschlagen
pruefe(not ap.unsichtbarer_name("Helmut Wittek"), "gewoehnlicher Name ist sauber")
pruefe(not ap.unsichtbarer_name("Karin Fléing"), "Akzent ist sauber")
pruefe(not ap.unsichtbarer_name("- -"), "Satzzeichen sind sichtbar")
pruefe(not ap.unsichtbarer_name("."), "einzelner Punkt ist sichtbar")
pruefe(not ap.unsichtbarer_name("園田　義人"),
       "japanischer Name mit U+3000 als Trenner bleibt sauber (Bestand: ysonoda@ktmail.tokai-u.jp)")
pruefe(not ap.unsichtbarer_name("SCHOEPS​ Mikrofone"),
       "Zero-Width-Space IM Namen allein macht ihn nicht getarnt")
pruefe(not ap.unsichtbarer_name(""), "leerer Name ist nicht verdaechtig")
pruefe(not ap.unsichtbarer_name(None), "fehlender Name ist nicht verdaechtig")
pruefe(not ap.unsichtbarer_name("   "), "nur Leerzeichen ist Schlamperei, keine Tarnung")

# Punycode
pruefe(ap.punycode_domain("xn--pple-43d.com"), "Punycode-Label erkannt")
pruefe(ap.punycode_domain("mail.xn--80ak6aa92e.com"), "Punycode in der Subdomain erkannt")
pruefe(not ap.punycode_domain("schoeps.de"), "gewoehnliche Domain ist sauber")
pruefe(not ap.punycode_domain("xnview.com"), "'xn' ohne '--' ist kein Punycode")
pruefe(not ap.punycode_domain(None), "fehlende Domain ist nicht verdaechtig")

# Gesamturteil der formalen Pruefung
pruefe(ap.form_verdacht("⠀", "hauptmikrofon.de") is not None,
       "getarnter Name schlaegt an, auch bei echter Domain")
pruefe(ap.form_verdacht("IONOS", "xn--ionos-x2a.de") is not None, "Punycode schlaegt an")
pruefe(ap.form_verdacht("Sennheiser", "sennheiser.com") is None, "unauffaellige Mail passiert")
pruefe(ap.form_verdacht(None, None) is None, "leere Felder passieren")

print(f"\n{FAELLE} Pruefungen gruen.")
