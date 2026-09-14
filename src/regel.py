"""Stufe 1 der Kaskade: wo lagen die Mails dieses Absenders bisher?

Portiert aus der Idee von LifeOS `routing.py` (ADR-0006 dort): die Regel wird
nicht gepflegt, sondern aus der Ablage-Historie abgelesen. Erst die Adresse,
dann die Domain mit Rollup auf die Eltern-Domain (`news.firma.de` erbt von
`firma.de`). Eine Historie zaehlt ab `MIN_EVIDENZ` gewichteten Mails und
entscheidet ab `MIN_ANTEIL` Konzentration in einem Ordner.

Zwei Ausnahmen, die hier NIE entscheiden und an die naechsten Stufen
durchreichen:

- **Eigene Domains** (`EIGENE_DOMAINS`, Default `schoeps.de`): ein Kollege
  schreibt zu vielen Themen. Seine Adresse sagt nichts ueber den Ordner, der
  Thread (Stufe 2) oder der Inhalt (Stufe 4) schon.
- **Anbieter-Domains**: geteilte Infrastruktur (Freemail, Ticket-Systeme,
  Versanddienste). Hinter jeder Adresse steckt ein anderer Absender; die
  Adress-Stufe darf noch greifen, der Domain-Rollup nicht.

Das Gewicht kommt aus `mail_evidenz`: handsortiert 2, vom Automaten abgelegt
(`auto-*`-Kategorie) 1 — sonst verstaerkt der Automat seine eigenen Fehler.
"""
from __future__ import annotations

import os
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MIN_EVIDENZ = float(os.getenv("REGEL_MIN_EVIDENZ", "2"))
MIN_ANTEIL = float(os.getenv("REGEL_MIN_ANTEIL", "0.8"))

# Strenge Schwellen fuer Absender der EIGENEN Domain und fuer Betreff-Marken:
# ein Kollege schreibt zu vielen Themen und soll hier nie entscheiden — eine
# Systemadresse (Zendesk-Benachrichtigungen ueber support@schoeps.de) aber
# schon. Trennt sich ueber die Zahlen: 95 % bei mindestens 20 gewichteten
# Mails erreicht kein Mensch, ein Automat immer. Trockenlauf 2026-09-14: 269 der
# 821 Zendesk-Mails kamen von drei schoeps.de-Adressen und fielen bis zur KI durch.
# Gemessen: die Marke '[Schoeps Mikrofone]' liegt zu 95,1 % im Zendesk-Ordner
# (784 gewichtete Mails, der Rest von Hand in Themenordner gezogen) — bei 0.95
# kippte die Entscheidung je nach ausgeblendeter Mail. Deshalb 0.9: bei >= 20
# Mails trennt das immer noch Automat von Mensch (sales@schoeps.de liegt bei 70 %).
MIN_EVIDENZ_STRENG = float(os.getenv("REGEL_MIN_EVIDENZ_STRENG", "20"))
MIN_ANTEIL_STRENG = float(os.getenv("REGEL_MIN_ANTEIL_STRENG", "0.9"))

EIGENE_DOMAINS = {
    d.strip().lower() for d in os.getenv("EIGENE_DOMAINS", "schoeps.de").split(",") if d.strip()
}

MULTI_TLD = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "com.au", "net.au", "org.au",
    "co.nz", "co.za", "co.jp", "or.jp", "ne.jp", "com.br", "com.mx", "com.tr",
    "com.cn", "com.pl", "com.hk", "com.sg", "co.in", "co.kr",
}

ANBIETER_DOMAINS = {
    # Freemail
    "gmail.com", "googlemail.com", "web.de", "gmx.de", "gmx.net", "gmx.at", "gmx.ch",
    "t-online.de", "yahoo.com", "yahoo.de", "hotmail.com", "hotmail.de", "outlook.com",
    "outlook.de", "live.de", "live.com", "icloud.com", "me.com", "aol.com", "freenet.de",
    "posteo.de", "mailbox.org", "protonmail.com", "proton.me", "mail.de", "arcor.de",
    # Ticket-, Zahlungs-, Shop- und Versandplattformen: Identitaet in Subdomain/Local Part
    "zendesk.com", "freshdesk.com", "freshworks.com", "stripe.com", "paypal.com",
    "myshopify.com", "shopify.com", "amazonses.com", "mailgun.org", "sendgrid.net",
    "sendgrid.com", "mandrillapp.com", "mailchimp.com", "mcsv.net", "mcdlv.net",
    "hubspot.com", "hubspotemail.net", "salesforce.com", "intercom-mail.com",
    "mailjet.com", "brevo.com", "sendinblue.com", "cmail19.com", "createsend.com",
    "klaviyomail.com", "emarsys.net", "responsys.net", "exacttarget.com",
    # Kollaboration mit Nutzer-Adressen unter geteilter Domain
    "slack.com", "asana.com", "atlassian.net", "planio.com", "docusign.net",
}


# ---------------------------------------------------------------- reine Logik
def domain_kandidaten(domain: str) -> list[str]:
    """Domain und ihre Eltern, vom Spezifischen zum Allgemeinen.

    'info.ecotrend.ista.com' -> ['info.ecotrend.ista.com', 'ecotrend.ista.com', 'ista.com']
    Stoppt vor der reinen TLD und vor mehrteiligen Public Suffixes.
    """
    domain = (domain or "").strip().lower().rstrip(".")
    if not domain or "." not in domain:
        return []
    teile = domain.split(".")
    aus: list[str] = []
    for i in range(len(teile) - 1):
        kand = ".".join(teile[i:])
        if kand in MULTI_TLD or kand.count(".") < 1:
            break
        aus.append(kand)
    return aus


def regel_kandidaten(domain: str) -> list[str]:
    """Domain-Kandidaten fuer die Regel-Stufe: der Rollup stoppt VOR der Anbieter-Ebene.

    Bei Anbieter-Domains ist die Subdomain die Identitaet: `schoeps.zendesk.com`
    darf entscheiden (alle Tickets liegen im Zendesk-Ordner), `zendesk.com` nicht —
    dahinter stecken beliebige Firmen. Trockenlauf 2026-09-14: weil die ganze
    Domain uebersprungen wurde, fiel ein Zendesk-Ticket bis zur KI durch und
    landete falsch.
    """
    aus: list[str] = []
    for kand in domain_kandidaten(domain):
        if kand in ANBIETER_DOMAINS:
            break
        aus.append(kand)
    return aus


def ist_eigene(domain: str) -> bool:
    return any(k in EIGENE_DOMAINS for k in domain_kandidaten(domain))


def ist_anbieter(domain: str) -> bool:
    return any(k in ANBIETER_DOMAINS for k in domain_kandidaten(domain))


def auswerten(zeilen: list[tuple[str, str, float]], min_anteil: float = MIN_ANTEIL,
              min_evidenz: float = MIN_EVIDENZ) -> dict[str, Any] | None:
    """Gewichtete Verteilung (ordner_id, pfad, gewicht), absteigend sortiert -> Gewinner oder None."""
    if not zeilen:
        return None
    gesamt = sum(g for _, _, g in zeilen)
    if gesamt < min_evidenz:
        return None
    oid, pfad, top = zeilen[0]
    anteil = top / gesamt
    if anteil < min_anteil:
        return None
    return {
        "ordner_id": oid, "ziel_pfad": pfad,
        "treffer": round(top, 1), "gesamt": round(gesamt, 1), "anteil": round(anteil, 3),
        "kandidaten": [{"pfad": p, "gewicht": round(g, 1)} for _, p, g in zeilen[:3]],
    }


# -------------------------------------------------------------- DB-Abfragen
_SQL_VERTEILUNG = """
    SELECT ordner_id, pfad, sum(gewicht)::float AS gew
      FROM mail_evidenz
     WHERE {bedingung}
       AND (CAST(:ohne AS text) IS NULL OR mail_id <> CAST(:ohne AS text))
     GROUP BY ordner_id, pfad
     ORDER BY gew DESC
"""


async def _verteilung(s: AsyncSession, bedingung: str, params: dict[str, Any]
                      ) -> list[tuple[str, str, float]]:
    r = await s.execute(text(_SQL_VERTEILUNG.format(bedingung=bedingung)), params)
    return [(row[0], row[1], float(row[2])) for row in r.fetchall()]


async def nach_adresse(s: AsyncSession, adresse: str, ohne_mail_id: str | None = None,
                       streng: bool = False) -> dict[str, Any] | None:
    adresse = (adresse or "").strip().lower()
    if not adresse or "@" not in adresse:
        return None
    zeilen = await _verteilung(s, "von_adresse = :a", {"a": adresse, "ohne": ohne_mail_id})
    t = (auswerten(zeilen, MIN_ANTEIL_STRENG, MIN_EVIDENZ_STRENG) if streng else auswerten(zeilen))
    if not t:
        return None
    return {**t, "stufe": "adresse", "schluessel": adresse,
            "begruendung": f"{t['treffer']:g} von {t['gesamt']:g} gewichteten Mails von "
                           f"{adresse} liegen in {t['ziel_pfad']}" + (" (Systemadresse)" if streng else "")}


_TAG = re.compile(r"^\s*(?:(?:AW|RE|WG|FW|FWD|Antwort|Zugesagt|Abgelehnt|Angenommen)\s*:\s*)*(\[[^\]]{2,60}\])")


def betreff_tag(betreff: str | None) -> str | None:
    """Fuehrende eckige Marke im Betreff: '[Schoeps Mikrofone] #64814: …' -> '[schoeps mikrofone]'.

    Ticket- und Projektsysteme (Zendesk, Redmine, Asana) kennzeichnen jede Mail
    so, waehrend Absender-Adresse und Domain wechseln koennen. Antwort-Praefixe
    davor werden ueberlesen. Kein Tag -> None.
    """
    if not betreff:
        return None
    m = _TAG.match(betreff)
    return m.group(1).strip().lower() if m else None


async def nach_betreff_tag(s: AsyncSession, betreff: str | None, ohne_mail_id: str | None = None
                           ) -> dict[str, Any] | None:
    """Stufe 1b: wohin gingen bisher Mails mit derselben Betreff-Marke? Strenge Schwellen."""
    tag = betreff_tag(betreff)
    if not tag:
        return None
    # Doppelpunkte im Regex als `\:` escapen — SQLAlchemys text() liest `(?:AW`
    # sonst als Bind-Parameter `:AW` (bekannte Falle, siehe LifeOS-CLAUDE.md).
    # Der Ausdruck muss buchstabengleich mit dem Index aus Migration 002 sein.
    zeilen = await _verteilung(
        s, "lower(substring(betreff FROM '^\\s*(?\\:(?\\:AW|RE|WG|FW|FWD|Antwort|Zugesagt|Abgelehnt|Angenommen)"
           "\\s*\\:\\s*)*(\\[[^\\]]{2,60}\\])')) = :tag",
        {"tag": tag, "ohne": ohne_mail_id},
    )
    t = auswerten(zeilen, MIN_ANTEIL_STRENG, MIN_EVIDENZ_STRENG)
    if not t:
        return None
    return {**t, "stufe": "adresse", "schluessel": tag,
            "begruendung": f"{t['treffer']:g} von {t['gesamt']:g} gewichteten Mails mit Betreff-Marke "
                           f"{tag} liegen in {t['ziel_pfad']}"}


async def nach_domain(s: AsyncSession, domain: str, ohne_mail_id: str | None = None
                      ) -> dict[str, Any] | None:
    domain = (domain or "").strip().lower()
    if not domain or ist_eigene(domain):
        return None
    for kand in regel_kandidaten(domain):
        zeilen = await _verteilung(
            s, "(von_domain = :d OR von_domain LIKE :sub)",
            {"d": kand, "sub": "%." + kand, "ohne": ohne_mail_id},
        )
        t = auswerten(zeilen)
        if t:
            return {**t, "stufe": "domain", "schluessel": kand,
                    "begruendung": f"{t['treffer']:g} von {t['gesamt']:g} gewichteten Mails der "
                                   f"Domain {kand} liegen in {t['ziel_pfad']}"}
        if zeilen:
            # Es gibt Historie, aber sie streut: nicht weiter nach oben rollen,
            # sonst entscheidet die Eltern-Domain ueber eine Subdomain, die
            # nachweislich anders abgelegt wird.
            return None
    return None


async def entscheide_statistik(s: AsyncSession, von_adresse: str | None, von_domain: str | None,
                               ohne_mail_id: str | None = None, betreff: str | None = None
                               ) -> dict[str, Any] | None:
    """Stufe 1 komplett: Adresse, Betreff-Marke, Domain.

    Eigene Domains: nur die strenge Adress-Regel (Systemadressen) und die
    Betreff-Marke — nie die Domain, nie die lockere Adress-Statistik.
    """
    if not von_adresse:
        return None
    eigene = bool(von_domain and ist_eigene(von_domain))
    t = await nach_adresse(s, von_adresse, ohne_mail_id, streng=eigene)
    if t:
        return t
    t = await nach_betreff_tag(s, betreff, ohne_mail_id)
    if t:
        return t
    if eigene:
        return None
    return await nach_domain(s, von_domain or von_adresse.split("@", 1)[-1], ohne_mail_id)
