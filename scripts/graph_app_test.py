"""Phase 0b: Client-Credentials-Zugang pruefen (Application Permissions).

1. Token per client_credentials
2. Ordnerbaum ueber /users/<upn>/mailFolders (positiv)
3. Ordnerbaum eines Kollegen (negativ, Application Access Policy muss 403 liefern)
4. Kategorien anlegen, falls sie fehlen (braucht MailboxSettings.ReadWrite)
"""
import sys, httpx

TENANT = "a4941ae5-fcbb-4e45-85f2-fcbc3d7d7079"
CLIENT = "1efdf29e-47c3-4f27-b423-50dadb4b48a3"
UPN = "wittek@schoeps.de"
FREMD = "fleing@schoeps.de"
G = "https://graph.microsoft.com/v1.0"

secret = open(r"C:\PROJEKTE\graph_secret.txt", encoding="utf-8-sig").read().strip()
tok = httpx.post(f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token", data={
    "grant_type": "client_credentials", "client_id": CLIENT, "client_secret": secret,
    "scope": "https://graph.microsoft.com/.default"}, timeout=30.0).json()
if "access_token" not in tok:
    print("TOKEN FEHLER:", tok.get("error"), (tok.get("error_description") or "")[:300]); sys.exit(1)
print("Token ok, gueltig (s):", tok.get("expires_in"))
# Rollen im Token sichtbar machen (ohne Signaturpruefung, nur zur Anzeige)
import base64, json
payload = tok["access_token"].split(".")[1]
payload += "=" * (-len(payload) % 4)
print("Rollen im Token:", json.loads(base64.urlsafe_b64decode(payload)).get("roles"))

h = {"Authorization": f"Bearer {tok['access_token']}", "Prefer": 'IdType="ImmutableId"'}
def get(path, **p):
    r = httpx.get(f"{G}{path}", headers=h, params=p, timeout=30.0)
    return r.status_code, (r.json() if r.content else {})

st, d = get(f"/users/{UPN}/mailFolders", **{"$top": "5", "$select": "displayName,totalItemCount"})
print(f"\n/users/{UPN}/mailFolders -> {st}:", [f["displayName"] for f in d.get("value", [])] or d.get("error", {}).get("message"))

st, d = get(f"/users/{FREMD}/mailFolders", **{"$top": "1", "$select": "displayName"})
print(f"/users/{FREMD}/mailFolders -> {st} (erwartet 403):", d.get("error", {}).get("code"))

st, d = get(f"/users/{UPN}/mailFolders/inbox/childFolders", **{"$top": "50", "$select": "displayName,totalItemCount"})
namen = {f["displayName"]: f["totalItemCount"] for f in d.get("value", [])}
print(f"\nPosteingang/Move vorhanden: {'Move' in namen}, Mails darin: {namen.get('Move')}")

K = f"{G}/users/{UPN}/outlook/masterCategories"
r = httpx.get(K, headers=h, timeout=30.0)
vorhanden = {k["displayName"]: k["color"] for k in r.json().get("value", [])}
print(f"\nmasterCategories GET -> {r.status_code}, {len(vorhanden)} vorhanden")
belegt = set(vorhanden.values())
frei = [f"preset{i}" for i in range(25) if f"preset{i}" not in belegt]
for name in ["auto-regel", "auto-thread", "auto-ki", "auto-neu"]:
    if name in vorhanden:
        print(f"  {name}: schon da"); continue
    farbe = frei.pop(0) if frei else "none"
    p = httpx.post(K, headers=h, json={"displayName": name, "color": farbe}, timeout=30.0)
    print(f"  POST {name} ({farbe}) -> {p.status_code}", "" if p.status_code < 300 else p.text[:150])
