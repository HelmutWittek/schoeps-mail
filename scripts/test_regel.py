"""Tests ohne DB und ohne LLM fuer die reine Logik von Stufe 1 und der Kaskaden-Hilfen.

    python scripts/test_regel.py
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("EIGENE_DOMAINS", "schoeps.de")

from src import kaskade, regel  # noqa: E402

FAELLE = 0


def pruefe(bedingung: bool, name: str) -> None:
    global FAELLE
    FAELLE += 1
    if not bedingung:
        print(f"FEHLT: {name}")
        sys.exit(1)
    print(f"ok    {name}")


# Domain-Rollup
pruefe(regel.domain_kandidaten("info.ecotrend.ista.com") == ["info.ecotrend.ista.com", "ecotrend.ista.com", "ista.com"],
       "Rollup ueber drei Ebenen")
pruefe(regel.domain_kandidaten("firma.co.uk") == ["firma.co.uk"], "mehrteilige TLD stoppt den Rollup")
pruefe(regel.domain_kandidaten("localhost") == [], "ohne Punkt keine Kandidaten")
pruefe(regel.domain_kandidaten("SCHOEPS.DE.") == ["schoeps.de"], "Normalisierung Kleinbuchstaben + Punkt")

# Eigene und Anbieter-Domains
pruefe(regel.ist_eigene("schoeps.de") and regel.ist_eigene("mail.schoeps.de"), "eigene Domain inkl. Subdomain")
pruefe(not regel.ist_eigene("schoeps-fan.de"), "aehnlicher Name ist nicht eigene Domain")
pruefe(regel.ist_anbieter("support.zendesk.com") and regel.ist_anbieter("gmail.com"), "Anbieter inkl. Subdomain")
pruefe(not regel.ist_anbieter("sennheiser.com"), "Hersteller ist kein Anbieter")

pruefe(regel.regel_kandidaten("schoeps.zendesk.com") == ["schoeps.zendesk.com"],
       "Anbieter: Subdomain darf entscheiden, Rollup stoppt vor zendesk.com")
pruefe(regel.regel_kandidaten("gmail.com") == [], "Anbieter selbst: keine Kandidaten")
pruefe(regel.regel_kandidaten("news.sennheiser.com") == ["news.sennheiser.com", "sennheiser.com"],
       "normale Domain rollt voll")

# Betreff-Marke
pruefe(regel.betreff_tag("[Schoeps Mikrofone] #64814: ORTF 3D Cable issues") == "[schoeps mikrofone]",
       "Marke am Betreffanfang")
pruefe(regel.betreff_tag("AW: [Schoeps Mikrofone] #64694: AW: Genelec 3D mic rig") == "[schoeps mikrofone]",
       "Antwort-Praefix vor der Marke wird ueberlesen")
pruefe(regel.betreff_tag("WG: Re: [Redmine #123] Aufgabe") == "[redmine #123]", "mehrere Praefixe")
pruefe(regel.betreff_tag("Mikrofon Anfrage [dringend]") is None, "Marke nur am Anfang zaehlt")
pruefe(regel.betreff_tag("") is None and regel.betreff_tag(None) is None, "leer -> None")

# Auswertung
Z = [("o1", "A", 8.0), ("o2", "B", 2.0)]
pruefe(regel.auswerten(Z)["ziel_pfad"] == "A" and regel.auswerten(Z)["anteil"] == 0.8, "80 % reichen genau")
pruefe(regel.auswerten([("o1", "A", 7.0), ("o2", "B", 3.0)]) is None, "70 % reichen nicht")
pruefe(regel.auswerten([("o1", "A", 1.0)]) is None, "eine einzelne auto-Mail (Gewicht 1) ist keine Evidenz")
pruefe(regel.auswerten([("o1", "A", 2.0)])["anteil"] == 1.0, "eine handsortierte Mail (Gewicht 2) reicht")
pruefe(regel.auswerten([]) is None, "leer -> None")
pruefe(regel.auswerten([("o1", "A", 1.0)], 0.8, 1.0) is not None, "Thread-Schwelle: eine Mail reicht")

# Kaskaden-Hilfen
pruefe(kaskade.bewegt({"stufe": "adresse", "ordner_id": "x"}), "Statistik bewegt")
pruefe(kaskade.bewegt({"stufe": "ki", "ordner_id": "x", "sicherheit": "sicher"}), "KI sicher bewegt")
pruefe(not kaskade.bewegt({"stufe": "ki", "ordner_id": "x", "sicherheit": "unsicher"}), "KI unsicher bewegt nicht")
pruefe(not kaskade.bewegt({"stufe": "unklar", "ordner_id": None}), "unklar bewegt nicht")
pruefe(kaskade.kategorie({"stufe": "domain"}) == "auto-regel" and kaskade.kategorie({"stufe": "thread"}) == "auto-thread",
       "Kategorien je Stufe")

print(f"\n{FAELLE} Pruefungen gruen.")
