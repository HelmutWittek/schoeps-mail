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

from src import absender_pruefung as ap, kaskade, urteil  # noqa: E402

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

# Urteil: "nirgends" im Pfad-Feld statt in `sicherheit` — Haiku haelt das Schema
# nicht immer ein (4 der 28 Move-Mails am 2026-09-15)
pruefe(urteil.ziel_leer("nirgends"), "'nirgends' im Pfadfeld zaehlt als kein Ordner")
pruefe(urteil.ziel_leer("") and urteil.ziel_leer(None), "leerer Pfad zaehlt als kein Ordner")
pruefe(urteil.ziel_leer(" NIRGENDS. ") and urteil.ziel_leer("'nirgends'"),
       "Gross/Klein, Punkt und Anfuehrungszeichen stoeren nicht")
pruefe(urteil.ziel_leer("-") and urteil.ziel_leer("kein Ordner"), "Strich und 'kein Ordner'")
pruefe(not urteil.ziel_leer("❼ privat"), "echter Pfad ist nicht leer")
pruefe(not urteil.ziel_leer("Posteingang/Standby"), "Sammelordner ist ein echter Pfad")

# Wegraeumen nach Move/Unbestimmt (Entscheidung Helmut 2026-09-15)
pruefe(kaskade.nach_unbestimmt({"stufe": "unklar", "vorfilter": True}),
       "Vorfilter-Treffer wird weggeraeumt")
pruefe(kaskade.nach_unbestimmt({"stufe": "ki", "sicherheit": "nirgends"}),
       "KI-`nirgends` wird weggeraeumt")
pruefe(not kaskade.nach_unbestimmt({"stufe": "ki", "sicherheit": "unsicher"}),
       "`unsicher` bleibt in Move, damit Helmut es sieht")
pruefe(not kaskade.nach_unbestimmt({"stufe": "unklar", "vorfilter": False}),
       "unklar ohne Vorfilter wartet auf Evidenz und bleibt liegen")
pruefe(not kaskade.nach_unbestimmt({"stufe": "adresse", "ordner_id": "x"}),
       "eine Statistik-Entscheidung wird nie weggeraeumt")
pruefe(kaskade.kategorie({"stufe": "ki", "sicherheit": "nirgends"}) == "auto-unbestimmt",
       "weggeraeumte Mails bekommen auto-unbestimmt")
pruefe(kaskade.kategorie({"stufe": "ki", "sicherheit": "sicher"}) == "auto-ki",
       "ein sicheres KI-Urteil behaelt auto-ki")

# Pfad ohne Bereichsmarke (Haiku laesst `❶ ` weg — 5 von 34 Mails am 2026-09-15)
pruefe(urteil.normpfad("❶ Produkte/Digital/Illusonic") == "produkte/digital/illusonic",
       "Bereichsmarke faellt weg")
pruefe(urteil.normpfad("Produkte/Digital/Illusonic") == urteil.normpfad("❶ Produkte/Digital/Illusonic"),
       "mit und ohne Marke ergeben denselben Kern")
pruefe(urteil.normpfad("❻ Verwaltung/Hardware,  Software") == "verwaltung/hardware, software",
       "doppelter Leerraum wird normiert")
pruefe(urteil.normpfad("Posteingang/Standby") == "posteingang/standby", "Pfad ohne Marke bleibt")
pruefe(urteil.normpfad("❾ List/") == "list", "leeres Endsegment faellt weg")
# Jahreszahlen sind Ordnernamen, keine Marken — eine erste Fassung mit `0-9`
# in der Marke erzeugte 13 Kollisionen im echten Ordnerbaum.
pruefe(urteil.normpfad("❺ Ausstellung/AES/2006-2024/2006 San Francisco")
       != urteil.normpfad("❺ Ausstellung/AES/2006-2024/2008 San Francisco"),
       "zwei AES-Jahrgaenge bleiben unterscheidbar")
pruefe(urteil.normpfad("❺ Ausstellung/IBC/2026").endswith("/2026"),
       "reiner Jahresordner behaelt sein Segment")
pruefe(urteil.normpfad("❺ Ausstellung/AES/2006-2024") == "ausstellung/aes/2006-2024",
       "Jahresbereich bleibt vollstaendig")
pruefe(urteil.normpfad("❺ Ausstellung/IBC") != urteil.normpfad("❺ Ausstellung/IBC/2026"),
       "Ordner und sein Jahres-Unterordner bleiben verschieden")
pruefe(urteil.normpfad(None) == "" and urteil.normpfad("") == "", "leer bleibt leer")
pruefe(urteil.normpfad("❸ Vertrieb") != urteil.normpfad("❸ Vertrieb/Bekannte"),
       "Eltern- und Unterordner bleiben verschieden")

print(f"\n{FAELLE} Pruefungen gruen.")
