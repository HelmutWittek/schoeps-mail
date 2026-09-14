import httpx, re
txt = open(r"C:\PROJEKTE\Slack token.txt", encoding="utf-8-sig").read()
m = re.search(r"xoxb-[A-Za-z0-9-]+", txt)
if not m:
    raise SystemExit("kein xoxb-Token in der Datei")
tok = m.group(0)
print("Bot-Token gefunden, Laenge:", len(tok), "| xoxp-Token noch in Datei:", "xoxp-" in txt)
h = {"Authorization": f"Bearer {tok}"}
def call(meth, **d):
    return httpx.post(f"https://slack.com/api/{meth}", headers=h, data=d, timeout=30.0).json()
K = "C0C18020FN3"
a = call("auth.test"); print("auth.test:", {k: a.get(k) for k in ("ok","error","team","user","bot_id")})
if a.get("ok"):
    c = call("conversations.info", channel=K); ch = c.get("channel", {})
    print("kanal:", {"ok": c.get("ok"), "error": c.get("error"), "name": ch.get("name"), "privat": ch.get("is_private"), "bot_ist_mitglied": ch.get("is_member")})
    if c.get("ok") and not ch.get("is_member") and not ch.get("is_private"):
        j = call("conversations.join", channel=K); print("join:", j.get("ok"), j.get("error"))
    p = call("chat.postMessage", channel=K, text="Testnachricht vom Mail-Sortierer: Slack-Anbindung steht. (Phase 0, 2026-09-14)")
    print("postMessage:", {"ok": p.get("ok"), "error": p.get("error")})
