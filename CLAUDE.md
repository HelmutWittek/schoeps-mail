# CLAUDE.md — Schoeps-Mail

Mail-Automation fuer das Schoeps-Postfach `wittek@schoeps.de`. Stand 2026-09-14:
Plan abgestimmt, Phase 0 (Zugang) erledigt, **Phase 1 noch nicht begonnen**.

Sprache: Antworten, Kommentare und Docstrings auf Deutsch, knapp. Keine
Aufwandsschaetzungen in Stunden oder Tagen (Umfang und Risiko nennen, Laufzeit
nur als echte Minuten). Committen, pushen und deployen darf Claude selbst,
nach jedem getesteten Schritt; nur eigene Dateien stagen, kein `git add -A`.

## Was das hier ist und was nicht

Ein eigener Worker, der Mails aus `Posteingang/Move` in die von Hand gepflegten
Ordner sortiert, aus der Ablage-Historie lernt, neue Unterordner vorschlaegt
und jede automatisch bewegte Mail mit einer `auto-*`-Kategorie markiert.

**Bewusst getrennt von LifeOS** (`C:\PROJEKTE\LifeOS`): LifeOS ist auf ein
Postfach gebaut (`source_uri = imap://<ordner>/uid/<uid>` ohne Konto, Routing-
Views ohne Konto-Dimension). Es gibt keine gemeinsame Tabelle und keinen Aufruf
zwischen beiden. Uebernommen wird nur die **Idee** der Routing-Statistik aus
`LifeOS/src/routing.py` (Stufen 1–3) und das Worker-Pattern (Heartbeat,
Dedup vor teuren Calls, NUL-Strip, Savepoint je Datensatz). Die private
Email-Automation (`C:\PROJEKTE\Email-Verarbeitung`, n8n + IMAP) ist NICHT die
Vorlage: Exchange Online braucht OAuth, und n8n kommt hier nicht vor.

**Datenhaltung:** nur Metadaten (Absender, Anzeigename, Betreff, Datum, Ordner,
Kategorien, conversationId, Graph-ID, `bodyPreview` 255 Zeichen). Volle Texte
werden fuer die LLM-Entscheidung geholt, nie gespeichert. Freigabe der
Datenhaltung gegenueber Schoeps ist Helmuts Sache und **stand am 2026-09-14
noch aus** — Phase 1 liest nur und verschiebt nichts, darf also laufen.

## Zugang (Phase 0, erledigt 2026-09-14)

- **MS Graph mit Application Permissions**, nicht IMAP, nicht delegiert.
  App-Registration `LifeOS-Exchange-Reader` (Helmuts eigene, multi-tenant),
  Client-ID `1efdf29e-47c3-4f27-b423-50dadb4b48a3`, Tenant
  `a4941ae5-fcbb-4e45-85f2-fcbc3d7d7079`. Rollen im Token: `Mail.ReadWrite`,
  `MailboxSettings.ReadWrite`, `Calendars.ReadWrite` (Read wuerde reichen).
  Token: `client_credentials`, Scope `https://graph.microsoft.com/.default`.
  Pfade immer `/users/wittek@schoeps.de/...`, nie `/me` (gibt es ohne User nicht).
- **Client-Secret** (24 Monate, laeuft ~2028-09 ab) liegt lokal in
  `C:\PROJEKTE\graph_secret.txt` — ausserhalb des Repos, nach Eintrag in die
  VPS-.env loeschen. Der Worker soll 30 Tage vor Ablauf warnen.
- **Helmut ist Tenant-Admin** (Consent-Screen zeigte „Zustimmung im Namen Ihrer
  Organisation"). Admin-Consent, Secrets und Exchange-Policies macht er selbst.
- **Application Access Policy** (Exchange Online PowerShell,
  `New-ApplicationAccessPolicy … -PolicyScopeGroupId wittek@schoeps.de
  -AccessRight RestrictAccess`) begrenzt die App auf sein Postfach.
  `Test-ApplicationAccessPolicy`: Wittek Gewaehrt, `fleing@schoeps.de`
  Abgelehnt. **Per Graph griff sie 6 Minuten nach Anlage noch nicht** (200
  statt 403 auf `/users/fleing@schoeps.de/mailFolders`); Microsoft nennt bis
  30 Minuten. Vor dem ersten Worker-Start mit `scripts/graph_policy_wait.py`
  gegenpruefen — solange dort 200 steht, darf nichts Fremdes gelesen werden.
- Warum nicht delegiert: der Schoeps-Tenant erzwingt alle 3–4 Wochen frische
  MFA (`AADSTS50078`), ein headless Worker stirbt daran (der LifeOS-
  Kalender-Worker tut das seit Juli 2026 regelmaessig). Der Device-Code-Weg
  bleibt als Notnagel in `scripts/graph_delegiert_test.py`.
- **Graph-Fallen:** `Prefer: IdType="ImmutableId"` auf JEDEM Aufruf, sonst
  aendert sich die Message-ID beim Verschieben. Kategorien anlegen braucht
  `MailboxSettings.ReadWrite` (mit `Mail.ReadWrite` allein: 403), Zuweisen an
  Mails nicht. Ordnernamen enthalten `❶…❾` — Konsole/Logs auf UTF-8
  (`PYTHONUTF8=1`), sonst `UnicodeEncodeError` beim ersten Ordnerbaum.

## Postfach-Befund (2026-09-14)

- Neun nummerierte Bereiche `❶ Produkte` (19 Unterordner), `❷ Einkauf,
  Fertigung`, `❸ Vertrieb` (11), `❹ Marketing` (13), `❺ Ausstellung` (7),
  `❻ Verwaltung` (14), `❼ privat` (7), `❽ Jobs` (558 Mails, 16), `❾ List` (11).
- `Posteingang/*` sind Absender-Sammelordner: `SCHOEPS intern` 7761,
  `Redmine, Planio, Slite` 4075, `Zendesk` 821, `Placetel` 656,
  `Einladungen` 491, `Asana` 301, `Press newsletter` 22, `Standby` 21,
  `Archiv` 7. **`Posteingang/Move` existiert (leer).**
- `Gesendete Elemente` 34.260 — Quelle fuer „hat Helmut diesem Absender je
  geantwortet" (Partner vs. Automat). `Gelöschte Elemente` 2302,
  `Junk-E-Mail` 831, `Archiv` 579, `GroupWise Archive` (18 Unterordner, alt).
- Jede Mail traegt eine `conversationId`. 16 Master-Kategorien vorhanden
  (SCHOEPS, Presse, Buchhaltung, to do, wichtig, privat, …), dazu seit
  2026-09-14 **`auto-regel`, `auto-thread`, `auto-ki`, `auto-neu`** (angelegt
  per Graph, preset2/8/10/11).

## Entscheidungen von Helmut

- **Unklares bleibt in `Posteingang/Move`.** Kein Unbekannt-Ordner. Der
  Worker bewertet liegengebliebene Mails bei jedem Lauf neu; sobald der
  Absender Historie hat (Helmut sortiert von Hand), greift die Regel.
- **Der Worker legt fehlende Kategorien selbst an.**
- **Newsletter werden normal einsortiert** (alle Stufen), erzeugen aber nie
  Ordnervorschlaege und zaehlen nicht in die thematische Verdichtung.
  **Junk ist nie ein Ziel.**
- **Kommunikation ueber Slack**, nicht Telegram (siehe unten).
- Thematisch sortieren, nicht nur nach Absender: ein Ordner hat einen
  Hauptabsender, aber auch Kollegen-Mails zum Thema. Bereiche mit vielen
  passenden Mails und haeufige Absender (kein Spam/Newsletter) sollen eigene
  Ordner bekommen — als Vorschlag, nie ohne Bestaetigung.

## Entscheidungskaskade (erste belastbare Stufe gewinnt)

1. **Adresse**, dann **Domain mit Subdomain-Rollup**: Mindestevidenz 2,
   Konzentration >= 0.8, Gewicht Hand=2 / auto=1 (erkennbar an der
   `auto-*`-Kategorie — eine weggeschobene auto-Mail ist eine Korrektur).
   **Absender aus `schoeps.de` und Anbieter-Domains werden hier NIE
   entschieden** — ein Kollege schreibt zu vielen Themen.
2. **Konversation:** liegt der Rest des Threads (`conversationId`) in einem
   Ordner, gehoert die Mail dorthin. Kategorie `auto-thread`.
3. **Absender-Ordner-Bezug** (Absender korrespondierte juengst in genau einem
   Ordner mit einem Kollegen) — nur Kandidat mit Vorrang fuer Stufe 4.
4. **Haiku-Urteil** mit Ordnerbaum **plus Ordnerprofilen**, Mailtext
   (~3000 Zeichen), Kandidaten aus 3. Antwort `sicher | unsicher | passt
   nirgends`; nur `sicher` wird bewegt (`auto-ki`), Begruendung ins Protokoll.

**Ordnerprofile:** je Ordner 2–3 Saetze von Haiku aus Top-Absendern, letzten
30 Betreffen und `bodyPreview`s; woechentlich aufgefrischt; auf der
Bestaetigungsseite lesbar und von Hand aenderbar (Handtext lenkt).

## Ordnervorschlaege (immer mit Bestaetigung)

Fluss: Slack-Nachricht mit Link auf Bestaetigungsseite (Basic Auth) →
**Anlegen + verschieben** (Liste der Mails sichtbar) / **anderer Ordner** /
**Nein** (Sperre). Neuer Ordner bekommt sofort ein Profil. Daempfung: max 3
offene Vorschlaege, nur letzte 12 Monate, nie Sent/Geloescht/Junk/Archiv.

- **A** haeufige Absender ohne eigenen Ordner (>= 8 Mails / 90 Tage). Kein
  Vorschlag bei `List-Unsubscribe`, `noreply`-Muster ohne Anzeigename, nie
  beantwortet UND nie direkt adressiert, oder je im Junk gelegen.
- **B** thematische Verdichtung in einem Ordner: erst Threads (viele Mails,
  mehrere Beteiligte), dann Haiku-Cluster ab 8 Mails ueber die letzten 90 Tage.
- **C** zusammengehoerige `Move`-Reste (gleicher Absender oder laut Haiku
  gleiches Thema).

## Slack (eingerichtet 2026-09-14)

App „Mail-Sortierer", Bot-User `mailsortierer` (bot_id `B0C1MDWA83G`),
Scopes `chat:write` + `im:write`, privater Kanal `#mail-sortierer` =
**`C0C18020FN3`**, Bot eingeladen, Testnachricht kam an. Bot-Token (`xoxb-`)
liegt in `C:\PROJEKTE\Slack token.txt` (nur diese Zeile; nach Eintrag in die
VPS-.env loeschen). Ein versehentlich kopierter User-Token wurde widerrufen.
Kein Token-Rotation-Opt-in (Token soll dauerhaft gelten). Stufe 2 spaeter:
Block-Kit-Buttons mit signiertem Endpunkt hinter Caddy.

## Betrieb (geplant)

VPS `root@212.227.161.180` (SSH: `ssh -i "/c/Users/Wittek/.ssh/id_ed25519"`),
Repo unter `/opt/schoeps-mail`, ein Container, eigene Datenbank `schoepsmail`
in der vorhandenen Postgres-Instanz des LifeOS-Stacks (`lifeos-postgres`,
Netz beitreten), Bestaetigungsseite hinter Caddy mit Basic Auth. `.env` mit
Sonderzeichen in Single-Quotes, nie per `source` laden. Heartbeat + Slack-
Alarm bei Fehlern und 30 Tage vor Secret-Ablauf.

## Phasen

| Phase | Inhalt | Stand |
|---|---|---|
| 0 | Zugang, Move, Kategorien, Slack | erledigt 2026-09-14 (Policy-Gegenprobe offen) |
| 1 | Index (Ordnerbaum, Metadaten aller Ordner, Delta je Ordner), Konversationen, Ordnerprofile, **Trockenlauf-Messung**: 200 Mails aus Ordnern ziehen, Ordner verstecken, alle vier Stufen raten lassen; Ziel >= ~90 % Treffer bei `sicher` | offen |
| 2 | Stufen 1–3 scharf, Kategorien, Schalter `DRY_RUN` | offen |
| 3 | Stufe 4 scharf, Protokoll mit Begruendungen | offen |
| 4 | Vorschlaege A–C, Slack, Bestaetigungsseite, Profile editierbar | offen |
| 5 | Heartbeat, Alarme, Doku | offen |

Module (geplant): `graph.py` (Auth, Delta, Move, Kategorien), `index.py`,
`regel.py` (Stufe 1), `konversation.py` (Stufe 2/3), `profil.py`,
`urteil.py` (Stufe 4), `vorschlag.py`, `web.py`, `worker.py`. Tests ohne
Graph und ohne LLM fuer Regel, Konversation und Vorschlagslogik; DB-Tests
begrenzt auf eigene Testdaten, Aufraeumer loeschen nur eigene Spuren.

## Skripte aus Phase 0 (`scripts/`)

- `graph_app_test.py` — Client-Credentials-Token, Ordnerbaum, Negativtest
  fremdes Postfach, Kategorien anlegen. Liest das Secret aus
  `C:\PROJEKTE\graph_secret.txt`.
- `graph_policy_wait.py` — pollt, bis die Access Policy 403 liefert.
- `graph_delegiert_test.py` — Device-Code-Flow (Notnagel), `--output` sichert
  das Refresh-Token in eine Datei.
- `slack_test.py` — Bot-Token pruefen, Testnachricht in den Kanal.
