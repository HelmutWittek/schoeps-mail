"""MS-Graph-Client fuer das Schoeps-Postfach (Application Permissions).

Was man wissen muss, bevor man hier etwas aendert:

- Token per `client_credentials` gegen den Tenant, Scope
  `https://graph.microsoft.com/.default`. Kein User, kein `/me` — alle Pfade
  gehen ueber `/users/<UPN>/…`. Die Application Access Policy in Exchange
  begrenzt die App auf genau dieses Postfach.
- `Prefer: IdType="ImmutableId"` steht auf JEDEM Aufruf. Ohne ihn wechselt die
  Message-ID beim Verschieben, und dieselbe Mail waere im Index zweimal.
- Delta je Ordner (`/mailFolders/<id>/messages/delta`): der erste Lauf liefert
  alles seitenweise, der Delta-Link am Ende merkt sich den Stand. Entfernte
  Mails kommen als `@removed`. Verschobene erscheinen als entfernt im alten und
  als neu im neuen Ordner.
- 429/503 mit `Retry-After` werden abgewartet, sonst nichts wiederholt, was
  schreibt (ein Move darf nicht zweimal laufen).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, AsyncIterator

import httpx

log = logging.getLogger("schoepsmail.graph")

TENANT = os.getenv("GRAPH_TENANT_ID", "a4941ae5-fcbb-4e45-85f2-fcbc3d7d7079")
CLIENT_ID = os.getenv("GRAPH_CLIENT_ID", "1efdf29e-47c3-4f27-b423-50dadb4b48a3")
CLIENT_SECRET = os.getenv("GRAPH_CLIENT_SECRET", "")
UPN = os.getenv("POSTFACH_UPN", "wittek@schoeps.de")

BASIS = "https://graph.microsoft.com/v1.0"
SEITENGROESSE = int(os.getenv("GRAPH_PAGE_SIZE", "200"))
MAX_RETRIES = 4

# Felder, die der Index je Mail braucht — und nicht mehr. Kein `body`.
MAIL_FELDER = ",".join([
    "id", "conversationId", "internetMessageId", "parentFolderId",
    "from", "toRecipients", "subject", "bodyPreview",
    "receivedDateTime", "sentDateTime", "categories", "isRead", "hasAttachments",
])

# Bekannte Systemordner, die Graph ueber einen Namen statt einer ID adressiert.
# Ueber sie werden Arbeits- und Altablage-Ordner erkannt, unabhaengig von der
# Anzeigesprache des Postfachs.
WELL_KNOWN = [
    "inbox", "drafts", "sentitems", "deleteditems", "junkemail", "outbox",
    "archive", "conversationhistory", "syncissues", "recoverableitemsdeletions",
    "msgfolderroot",
]


class GraphFehler(RuntimeError):
    def __init__(self, status: int, text: str, pfad: str):
        super().__init__(f"Graph {status} auf {pfad}: {text[:300]}")
        self.status = status


class Graph:
    def __init__(self, upn: str = UPN):
        if not CLIENT_SECRET:
            raise RuntimeError("GRAPH_CLIENT_SECRET fehlt (siehe .env.example)")
        self.upn = upn
        self._token: str | None = None
        self._token_bis: float = 0.0
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=20.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ Auth
    async def _access_token(self) -> str:
        if self._token and time.time() < self._token_bis - 120:
            return self._token
        r = await self._client.post(
            f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "scope": "https://graph.microsoft.com/.default",
            },
        )
        daten = r.json()
        if r.status_code != 200 or "access_token" not in daten:
            raise GraphFehler(r.status_code, daten.get("error_description") or r.text, "token")
        self._token = daten["access_token"]
        self._token_bis = time.time() + int(daten.get("expires_in", 3600))
        log.info("Access-Token erneuert, gueltig %ss", daten.get("expires_in"))
        return self._token

    async def _headers(self, extra_prefer: str | None = None) -> dict[str, str]:
        prefer = 'IdType="ImmutableId"'
        if extra_prefer:
            prefer += ", " + extra_prefer
        return {
            "Authorization": f"Bearer {await self._access_token()}",
            "Prefer": prefer,
            "Accept": "application/json",
        }

    # -------------------------------------------------------------- Transport
    def _url(self, pfad: str) -> str:
        if pfad.startswith("http"):
            return pfad
        return f"{BASIS}/users/{self.upn}{pfad}"

    async def _anfrage(self, methode: str, pfad: str, *, params: dict | None = None,
                       json: Any = None, prefer: str | None = None,
                       wiederholen: bool = True) -> dict[str, Any]:
        url = self._url(pfad)
        versuch = 0
        while True:
            versuch += 1
            try:
                r = await self._client.request(
                    methode, url, params=params, json=json, headers=await self._headers(prefer)
                )
            except httpx.HTTPError as exc:
                if not wiederholen or versuch > MAX_RETRIES:
                    raise
                warte = 2 * versuch
                log.warning("Netzfehler %s auf %s, warte %ss", type(exc).__name__, pfad, warte)
                await asyncio.sleep(warte)
                continue
            if r.status_code in (429, 503, 504) and wiederholen and versuch <= MAX_RETRIES:
                warte = int(r.headers.get("Retry-After", "5") or 5)
                log.warning("Graph %s auf %s, warte %ss", r.status_code, pfad, warte)
                await asyncio.sleep(warte)
                continue
            if r.status_code == 401 and versuch == 1:
                # Token abgelaufen oder widerrufen: einmal frisch holen.
                self._token = None
                continue
            if r.status_code >= 400:
                raise GraphFehler(r.status_code, r.text, pfad)
            if r.status_code == 204 or not r.content:
                return {}
            return r.json()

    async def get(self, pfad: str, **params: Any) -> dict[str, Any]:
        return await self._anfrage("GET", pfad, params=params or None)

    async def alle_seiten(self, pfad: str, **params: Any) -> AsyncIterator[dict[str, Any]]:
        """Folgt `@odata.nextLink`, liefert die Eintraege einzeln."""
        daten = await self.get(pfad, **params)
        while True:
            for eintrag in daten.get("value", []):
                yield eintrag
            weiter = daten.get("@odata.nextLink")
            if not weiter:
                return
            daten = await self._anfrage("GET", weiter)

    # ------------------------------------------------------------------ Ordner
    async def well_known_ids(self) -> dict[str, str]:
        """Name -> Ordner-ID fuer die Systemordner, die es in diesem Postfach gibt."""
        aus: dict[str, str] = {}
        for name in WELL_KNOWN:
            try:
                d = await self.get(f"/mailFolders/{name}", **{"$select": "id"})
                aus[name] = d["id"]
            except GraphFehler as exc:
                if exc.status != 404:
                    raise
        return aus

    async def alle_ordner(self) -> list[dict[str, Any]]:
        """Kompletter Ordnerbaum, rekursiv ueber childFolders. Felder:
        id, displayName, parentFolderId, totalItemCount, childFolderCount."""
        select = "id,displayName,parentFolderId,totalItemCount,childFolderCount"
        aus: list[dict[str, Any]] = []

        async def _kinder(eltern_id: str | None) -> None:
            pfad = "/mailFolders" if eltern_id is None else f"/mailFolders/{eltern_id}/childFolders"
            async for o in self.alle_seiten(pfad, **{"$top": "100", "$select": select,
                                                     "includeHiddenFolders": "true"}):
                aus.append(o)
                if o.get("childFolderCount", 0) > 0:
                    await _kinder(o["id"])

        await _kinder(None)
        return aus

    async def lege_ordner_an(self, eltern_id: str, name: str) -> dict[str, Any]:
        return await self._anfrage("POST", f"/mailFolders/{eltern_id}/childFolders",
                                   json={"displayName": name}, wiederholen=False)

    # ------------------------------------------------------------------- Mails
    async def delta(self, ordner_id: str, delta_link: str | None) -> tuple[list[dict[str, Any]], str]:
        """Eine Delta-Runde fuer einen Ordner.

        Liefert alle Aenderungen seit `delta_link` (None = alles) und den neuen
        Delta-Link. Verarbeitet seitenweise, haelt aber alle Eintraege im
        Speicher — beim ersten Lauf ueber 'Gesendete Elemente' sind das ~34.000
        Metadatensaetze, das ist vertretbar. Der Aufrufer schreibt sie in die DB.
        """
        if delta_link:
            daten = await self._anfrage("GET", delta_link, prefer=f"odata.maxpagesize={SEITENGROESSE}")
        else:
            daten = await self._anfrage(
                "GET", f"/mailFolders/{ordner_id}/messages/delta",
                params={"$select": MAIL_FELDER}, prefer=f"odata.maxpagesize={SEITENGROESSE}",
            )
        eintraege: list[dict[str, Any]] = []
        while True:
            eintraege.extend(daten.get("value", []))
            if "@odata.deltaLink" in daten:
                return eintraege, daten["@odata.deltaLink"]
            weiter = daten.get("@odata.nextLink")
            if not weiter:
                raise RuntimeError("Delta-Antwort ohne nextLink und ohne deltaLink")
            daten = await self._anfrage("GET", weiter, prefer=f"odata.maxpagesize={SEITENGROESSE}")

    async def delta_seiten(self, ordner_id: str, delta_link: str | None
                           ) -> AsyncIterator[tuple[list[dict[str, Any]], str | None]]:
        """Wie `delta`, aber seitenweise: (Eintraege, deltaLink|None). Der
        Delta-Link kommt erst mit der letzten Seite; wer zwischendurch
        committet, darf ihn erst dann speichern."""
        prefer = f"odata.maxpagesize={SEITENGROESSE}"
        if delta_link:
            daten = await self._anfrage("GET", delta_link, prefer=prefer)
        else:
            daten = await self._anfrage("GET", f"/mailFolders/{ordner_id}/messages/delta",
                                        params={"$select": MAIL_FELDER}, prefer=prefer)
        while True:
            fertig = daten.get("@odata.deltaLink")
            yield daten.get("value", []), fertig
            if fertig:
                return
            weiter = daten.get("@odata.nextLink")
            if not weiter:
                raise RuntimeError("Delta-Antwort ohne nextLink und ohne deltaLink")
            daten = await self._anfrage("GET", weiter, prefer=prefer)

    async def mail_text(self, mail_id: str, max_zeichen: int = 3000) -> str:
        """Volltext einer Mail als Klartext, gekuerzt. Wird nie gespeichert."""
        d = await self._anfrage("GET", f"/messages/{mail_id}",
                                params={"$select": "body"},
                                prefer='outlook.body-content-type="text"')
        text = ((d.get("body") or {}).get("content") or "").replace("\x00", "")
        return text[:max_zeichen]

    async def mail_header(self, mail_id: str) -> list[dict[str, str]]:
        d = await self._anfrage("GET", f"/messages/{mail_id}",
                                params={"$select": "internetMessageHeaders"})
        return d.get("internetMessageHeaders") or []

    async def verschiebe(self, mail_id: str, ziel_ordner_id: str) -> dict[str, Any]:
        """MOVE — nie wiederholt, damit eine Mail nicht zweimal wandert."""
        return await self._anfrage("POST", f"/messages/{mail_id}/move",
                                   json={"destinationId": ziel_ordner_id}, wiederholen=False)

    async def setze_kategorien(self, mail_id: str, kategorien: list[str]) -> None:
        await self._anfrage("PATCH", f"/messages/{mail_id}", json={"categories": kategorien},
                            wiederholen=False)

    # -------------------------------------------------------------- Kategorien
    async def master_kategorien(self) -> dict[str, str]:
        d = await self.get("/outlook/masterCategories")
        return {k["displayName"]: k.get("color", "none") for k in d.get("value", [])}

    async def lege_kategorie_an(self, name: str, farbe: str) -> None:
        await self._anfrage("POST", "/outlook/masterCategories",
                            json={"displayName": name, "color": farbe}, wiederholen=False)

    # ---------------------------------------------------- Posteingangsregeln
    async def posteingangs_regeln(self) -> list[dict[str, Any]]:
        """Die serverseitigen Regeln des Postfachs (`mailFolders/inbox/messageRules`).

        Braucht `MailboxSettings.Read(Write)`. Nur Posteingangsregeln — reine
        Client-Regeln von Outlook Desktop liegen nicht im Postfach und sind hier
        unsichtbar. Sie laufen bei der Zustellung, also vor allem, was dieser
        Worker tut.
        """
        d = await self.get("/mailFolders/inbox/messageRules")
        return d.get("value", [])
