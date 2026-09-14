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
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MIN_EVIDENZ = float(os.getenv("REGEL_MIN_EVIDENZ", "2"))
MIN_ANTEIL = float(os.getenv("REGEL_MIN_ANTEIL", "0.8"))

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


async def nach_adresse(s: AsyncSession, adresse: str, ohne_mail_id: str | None = None
                       ) -> dict[str, Any] | None:
    adresse = (adresse or "").strip().lower()
    if not adresse or "@" not in adresse:
        return None
    zeilen = await _verteilung(s, "von_adresse = :a", {"a": adresse, "ohne": ohne_mail_id})
    t = auswerten(zeilen)
    if not t:
        return None
    return {**t, "stufe": "adresse", "schluessel": adresse,
            "begruendung": f"{t['treffer']:g} von {t['gesamt']:g} gewichteten Mails von "
                           f"{adresse} liegen in {t['ziel_pfad']}"}


async def nach_domain(s: AsyncSession, domain: str, ohne_mail_id: str | None = None
                      ) -> dict[str, Any] | None:
    domain = (domain or "").strip().lower()
    if not domain or ist_eigene(domain) or ist_anbieter(domain):
        return None
    for kand in domain_kandidaten(domain):
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
                               ohne_mail_id: str | None = None) -> dict[str, Any] | None:
    """Stufe 1 komplett: Adresse, dann Domain. Eigene Domains ueberspringen beides."""
    if not von_adresse:
        return None
    if von_domain and ist_eigene(von_domain):
        return None
    t = await nach_adresse(s, von_adresse, ohne_mail_id)
    if t:
        return t
    return await nach_domain(s, von_domain or von_adresse.split("@", 1)[-1], ohne_mail_id)
