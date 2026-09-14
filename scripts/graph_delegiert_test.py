"""Phase 0 Schoeps-Mail: Zugangstest fuer delegiertes Mail.ReadWrite an der
bestehenden App-Registration LifeOS-Exchange-Reader (Device-Code-Flow).

Prueft:
  1. Geht der Consent fuer Mail.ReadWrite ohne Admin durch?
  2. Welche Scopes stehen tatsaechlich im Token?
  3. Lesen: /me, Ordnerbaum unter dem Posteingang (gibt es 'Move'?), eine Mail.
  4. Schreiben wird NICHT getestet (nur Rechte-Check ueber den Scope).

Das Refresh-Token wird nur mit --output <datei> gespeichert, sonst verworfen.
"""
from __future__ import annotations

import argparse
import sys
import time

import httpx

SCOPE = "Mail.ReadWrite Calendars.Read User.Read offline_access"
GRAPH = "https://graph.microsoft.com/v1.0"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--client-id", required=True)
    p.add_argument("--tenant", required=True)
    p.add_argument("--output", default=None, help="Datei fuer das .env-Snippet (optional)")
    args = p.parse_args()

    authority = f"https://login.microsoftonline.com/{args.tenant}"
    r = httpx.post(f"{authority}/oauth2/v2.0/devicecode",
                   data={"client_id": args.client_id, "scope": SCOPE}, timeout=15.0)
    if r.status_code != 200:
        print(f"FEHLER Device-Code: {r.status_code}\n{r.text}", flush=True)
        return 2
    flow = r.json()
    print("=" * 60, flush=True)
    print(flow.get("message"), flush=True)
    print("=" * 60, flush=True)

    interval = int(flow.get("interval", 5))
    deadline = time.time() + int(flow.get("expires_in", 900))
    token = None
    while time.time() < deadline:
        time.sleep(interval)
        try:
            tr = httpx.post(f"{authority}/oauth2/v2.0/token", data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": args.client_id,
                "device_code": flow["device_code"],
            }, timeout=30.0)
        except httpx.HTTPError as exc:
            # Wackliges WLAN: Netzfehler sind kein Login-Fehler, weiter pollen.
            print(f"  (Netzaussetzer, versuche weiter: {type(exc).__name__})", flush=True)
            continue
        if tr.status_code == 200:
            token = tr.json()
            break
        body = tr.json()
        err = body.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5
            continue
        print(f"FEHLER beim Login: {err}\n{body.get('error_description', '')}", flush=True)
        return 3
    if token is None:
        print("FEHLER: Code abgelaufen.", flush=True)
        return 4

    print("\nLOGIN OK", flush=True)
    print("Scopes im Token:", token.get("scope"), flush=True)
    print("Access-Token gueltig (s):", token.get("expires_in"), flush=True)
    print("Refresh-Token erhalten:", bool(token.get("refresh_token")), flush=True)

    h = {"Authorization": f"Bearer {token['access_token']}", "Prefer": 'IdType="ImmutableId"'}

    def get(path: str, **params):
        resp = httpx.get(f"{GRAPH}{path}", headers=h, params=params, timeout=30.0)
        return resp.status_code, (resp.json() if resp.content else {})

    st, me = get("/me", **{"$select": "userPrincipalName,mail,displayName"})
    print(f"\n/me -> {st}: {me.get('userPrincipalName')} ({me.get('displayName')})", flush=True)

    st, wurzel = get("/me/mailFolders", **{"$top": "50", "$select": "id,displayName,totalItemCount,childFolderCount"})
    print(f"\n/me/mailFolders -> {st}", flush=True)
    if st != 200:
        print(wurzel, flush=True)
    for f in wurzel.get("value", []):
        print(f"  {f['displayName']:<40} {f.get('totalItemCount', '?'):>6} Mails, {f.get('childFolderCount', 0)} Unterordner", flush=True)

    st, kinder = get("/me/mailFolders/inbox/childFolders",
                     **{"$top": "100", "$select": "displayName,totalItemCount,childFolderCount"})
    print(f"\nPosteingang/* -> {st}", flush=True)
    namen = []
    for f in kinder.get("value", []):
        namen.append(f["displayName"])
        print(f"  {f['displayName']:<40} {f.get('totalItemCount', '?'):>6} Mails, {f.get('childFolderCount', 0)} Unterordner", flush=True)
    print("Ordner 'Move' unter Posteingang vorhanden:", "Move" in namen, flush=True)

    st, mails = get("/me/messages", **{"$top": "1", "$select": "subject,from,receivedDateTime,conversationId,categories,parentFolderId"})
    print(f"\n/me/messages?$top=1 -> {st}", flush=True)
    for m in mails.get("value", []):
        frm = ((m.get("from") or {}).get("emailAddress") or {}).get("address")
        print(f"  {m.get('receivedDateTime')}  von {frm}  Betreff: {(m.get('subject') or '')[:40]!r}  "
              f"conversationId={'ja' if m.get('conversationId') else 'nein'}  Kategorien={m.get('categories')}", flush=True)

    st, kat = get("/me/outlook/masterCategories")
    print(f"\n/me/outlook/masterCategories -> {st} "
          f"({'ohne MailboxSettings-Scope erwartet 403' if st == 403 else str(len(kat.get('value', []))) + ' Kategorien'})",
          flush=True)

    if args.output and token.get("refresh_token"):
        with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(f"MSGRAPH_CLIENT_ID={args.client_id}\n")
            fh.write(f"MSGRAPH_TENANT_ID={args.tenant}\n")
            fh.write(f"MSGRAPH_MAIL_REFRESH_TOKEN={token['refresh_token']}\n")
        print(f"\nRefresh-Token nach {args.output} geschrieben.", flush=True)
    else:
        print("\nRefresh-Token verworfen (kein --output).", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
