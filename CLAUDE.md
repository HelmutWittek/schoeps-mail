# CLAUDE.md — Schoeps-Mail

Mail-Automation fuer das Schoeps-Postfach `wittek@schoeps.de`. Stand 2026-09-15:
Phase 0 (Zugang) und Phase 1 (Index + Messung) erledigt, **Phase 2 gebaut und der
Worker laeuft dauerhaft auf dem VPS (`schoeps-mail-worker`, seit 2026-09-15 09:38)
— noch mit `DRY_RUN=1`**: er protokolliert, was er mit `Posteingang/Move` taete,
bewegt aber nichts. Scharf schalten = `DRY_RUN='0'` in `/opt/schoeps-mail/.env`
und `docker compose up -d worker` (die .env wird nur beim Erzeugen gelesen).
Betrieb: `docker compose logs -f worker`, Heartbeats in `worker_heartbeat`
(`sortierer` jeder Zyklus, `index` beim Voll-Sync alle 8 Zyklen).

## Was steht (Code)

| Modul | Aufgabe |
|---|---|
| `src/graph.py` | Graph-Client: client_credentials, Retry-After, `IdType=ImmutableId` ueberall, Delta je Ordner (seitenweise), Move, Kategorien, Ordner anlegen, Mailtext holen |
| `src/index.py` | Ordnerbaum spiegeln (Pfade, Arbeitsordner ueber Well-Known-Namen, Vererbung ausser `inbox`), Delta-Sync mit Commit je Seite; ein Ordnerfehler laesst die anderen durch, Zyklus gilt als gescheitert |
| `src/regel.py` | Stufe 1: Adresse, Domain-Rollup, `MIN_EVIDENZ`=2, `MIN_ANTEIL`=0.8, eigene und Anbieter-Domains entscheiden nie |
| `src/konversation.py` | Stufe 2 Thread (`conversationId`, eine Mail reicht, streut er → Kandidaten), Stufe 3 Absender-Bezug (nur Kandidaten) |
| `src/profil.py` | Ordnerprofile (Haiku, 2–3 Saetze, woechentlich, `profil_manuell` bleibt), parallel 6 |
| `src/urteil.py` | Stufe 4: Ordnerbaum + Profile im Systemprompt (cache_control), Kandidaten mit Vorrang, `sicher|unsicher|nirgends`, unbekannter Pfad = unsicher |
| `src/kaskade.py` | fuehrt 1→4 zusammen, `bewegt()`/`kategorie()`, `protokolliere()` nach `regel_entscheidung` |
| `src/llm.py` | Haiku (`claude-haiku-4-5`) mit Structured Outputs, Ausfall = None |
| `src/worker.py` | Schleife: Index + Profil-Auffrischung, Heartbeat, DRY_RUN |
| `migrations/001_index.sql` | `ordner`, `mail`, Sicht `mail_evidenz` (Gewicht Hand=2/auto=1), `regel_entscheidung`, `worker_heartbeat` |
| `scripts/` | `index_lauf.py`, `profil_lauf.py`, `trockenlauf.py` (Messung), `test_regel.py` (19 Pruefungen ohne DB) |

Betrieb: `/opt/schoeps-mail` auf dem VPS (Klon von GitHub `HelmutWittek/schoeps-mail`,
oeffentlich), `.env` dort (chmod 600), DB `schoepsmail` mit Rolle `schoepsmail` in
`lifeos-postgres`, Compose-Netz `lifeos_default` extern. Skripte:
`docker compose run --rm -T --no-deps worker python scripts/<x>.py`. Erstlauf des Index:
20 Minuten fuer 114.352 Mails. **Der ANTHROPIC_API_KEY ist derselbe wie bei LifeOS**
(aus `/opt/lifeos/.env` uebernommen).

## Phase 1: Befund (2026-09-14)

- Index: **114.352 Mails, 517 Ordner (97 Arbeitsordner), 76.975 Evidenz-Mails,
  5.523 Absender, 67.813 Konversationen, aelteste Mail 2005.** Groesste Zielordner:
  `Posteingang/SCHOEPS intern` 7.762, `Posteingang/Redmine, Planio, Slite` 4.075,
  `…/AI, Automation/Auto emails` 2.798, `❾ List/MicBuilder Yahoo` 2.499.
- Profile: 420 Zielordner, 391 per Haiku (630k Tokens ein, 58k aus, ~0,90 USD),
  29 zu klein (< 3 Mails, nur Name). Stichprobe gelesen: treffend und konkret.
- **Trockenlauf ohne KI, 500 Mails aus 12 Monaten:** Adresse 321 Entscheidungen,
  **99,4 %** richtig; Domain 5, 100 %; Thread 56, **80,4 %**; 118 unklar. Zusammen
  wuerden 76 % bewegt, davon 96,6 % richtig.
- **Befund zum Thread-Fehler:** 10 der 11 Fehlgriffe betreffen `Posteingang/SCHOEPS
  intern` — die Mail liegt dort, der Thread zeigt in den Themenordner (oder
  umgekehrt). Helmuts bisherige Ablage legt Kollegen-Antworten in den
  Sammelordner, sein Wunsch fuer den Automaten ist der Themenordner. Das ist also
  kein Fehler der Stufe, sondern ein **Konflikt zwischen alter Praxis und neuer
  Regel**; offen ist, ob `SCHOEPS intern` (und andere Posteingang-Sammelordner) aus
  der Thread-Evidenz ausgenommen werden soll — Entscheidung Helmut.
- **Nach der SCHOEPS-intern-Entscheidung (Sammelordner raus aus Evidenz und
  Stichprobe), 500 Mails ohne KI:** Adresse 422 / **99,1 %**, Domain 5 / 100 %,
  Thread 33 / **97,0 %**, 40 unklar → **92 % wuerden bewegt, 98,9 % davon richtig.**
  Dazu zwei neue Regeln in Stufe 1, beide aus Zendesk-Fehlgriffen: (a) Absender der
  EIGENEN Domain duerfen entscheiden, wenn sie Systemadressen sind (>= 20 gewichtete
  Mails, >= 90 % in einem Ordner — `MIN_*_STRENG`; `sales@schoeps.de` liegt bei 70 %
  und faellt korrekt durch); (b) **Betreff-Marke** `[Schoeps Mikrofone] #…`
  (`betreff_tag`, Migration 002 haengt `betreff` an die Sicht + Ausdrucks-Index):
  784 gewichtete Mails, 95,1 % im Zendesk-Ordner — bei Schwelle 0.95 kippte die
  Entscheidung je nach ausgeblendeter Mail, deshalb 0.9. **SQL-Falle dabei:** der
  Regex `(?:AW|RE…` im `text()`-Statement wurde als Bind-Parameter `:AW` gelesen —
  Doppelpunkte im SQL als `\:` escapen, der Ausdruck muss buchstabengleich zum Index sein.
- **KI-Stufe gezielt gemessen (`--nur-ki`, 84 Mails, die Stufe 1–3 offen lassen,
  gleiche Stichprobe fuer beide Modelle):** Haiku `sicher` 71 Entscheidungen,
  **66 %** richtig (0,12 USD); Sonnet 5 (`effort: low`) `sicher` 63, **76 %**
  richtig, 18 `unsicher` (0,15 USD). Beide unter dem 90-%-Ziel — aber die
  Fehlgriffe sind zu einem grossen Teil **Konventionen, die nicht in der Mail
  stehen**: Bahn- und Hotelbuchungen liegen bei Helmut im Ordner der Veranstaltung
  (`VDT/TMT 2025`, `sonst./JTSE`), nicht in `Reisen, Bahn`; GitHub-Belege liegen
  historisch in DREI Ordnern (`IT`, `Software`, `AI, Automation`) — da gibt es kein
  „richtig"; Eltern/Kind (`Bekannte, Branche` vs. `…/Promi Users`, `AES` vs.
  `AES/German Section`) und Partner-vs-Produkt (`Illusonic` vs. `SuperCMIT`). Der
  gebaute Hebel dafuer sind die **handgeschriebenen Profile** („Reisen, Bahn: nur
  Buchungen ohne Veranstaltungsbezug") — Phase 4 macht sie editierbar. Haiku hat
  6x einen **nicht existierenden Pfad** erfunden (`Posteingang/Slite` statt
  `Posteingang/Redmine, Planio, Slite`) — wird als unsicher gezaehlt, nie bewegt.
  Gesamtbild mit 200er-Zufallsstichprobe: Stufe 1–3 decken 92 % ab, die KI den
  Rest; **Gesamtpraezision 97,5 % (Haiku) bzw. 98,4 % (Sonnet)** bei 96–98 %
  Abdeckung. Einmal `500 Internal server error` je Lauf (transient).
- **Trockenlauf mit KI, 200 Mails — abgebrochen durch leeres Anthropic-Guthaben**
  (`credit balance is too low`, nach ~50 Mails; derselbe Key wie LifeOS, dessen
  Extraktor/Spiegel/Urteile/Briefing damit ebenfalls stehen, bis aufgeladen ist).
  Bis dahin: Adresse 131/131, Thread 14/18, **KI `sicher` 16 Entscheidungen, 7
  richtig (43,8 %)** — aber 6 der 9 Fehlgriffe sind wieder der SCHOEPS-intern-
  Konflikt (Urlaubsantrag → `Verwaltung/Personal/AIDA`, FWC26-Shipments →
  `Leihgaben, Rental/FIFA, UEFA`: thematisch richtig, historisch „falsch").
  Ohne diesen Konflikt 7 von 10. Zu klein fuer ein Urteil; **Messung mit 200
  KI-Entscheidungen wiederholen, sobald Guthaben da ist.** Ein echter Fehler
  dabei: Zendesk-Ticket fiel bis zur KI durch, weil `zendesk.com` als
  Anbieter-Domain die GANZE Domain-Stufe sperrte → `regel_kandidaten` stoppt den
  Rollup jetzt nur VOR der Anbieter-Ebene, `schoeps.zendesk.com` darf entscheiden.
  Einmal `400 Invalid request data` von Anthropic bei einer Mail — Ursache offen
  (vermutlich Inhalt), bei der Wiederholung beobachten.
- **Access Policy greift seit 2026-09-15 vollstaendig:** Kollegen-Postfach 403 fuer
  `/messages`, `/mailFolders` und `/calendarView`, eigenes Postfach 200. Die
  Mail-Seite brauchte also ueber Nacht (> 95 min, < 16 h), kein RBAC-Umbau noetig.
- **Access Policy griff am 14.09. nur halb (Stand 17:20, 95 min nach Anlage):** auf das
  Kollegen-Postfach liefert `/calendarView` **403** (Policy wirkt), aber
  `/messages` und `/mailFolders` weiter **200**. `Test-ApplicationAccessPolicy`
  sagt „Abgelehnt". Also kein Konfigurationsfehler am Scope, sondern die
  Mail-Seite zieht nicht nach — entweder langsamere Propagation oder eine
  Luecke des alten Mechanismus. Naechste Schritte, vor Phase 2: am Folgetag
  `scripts/graph_policy_wait.py` erneut; bleibt es bei 200, auf **RBAC for
  Applications** umstellen (Exchange Online: `New-ServicePrincipal`,
  `New-ManagementScope` auf das Postfach, `New-ManagementRoleAssignment -App …
  -Role "Application Mail.ReadWrite" -CustomResourceScope …`, danach die
  Graph-Application-Permission `Mail.ReadWrite` in Entra ENTFERNEN — bei RBAC
  autorisiert Exchange, nicht Graph). Bis dahin gilt: der Worker liest nur
  `/users/wittek@schoeps.de`, die technische Sperre fehlt fuer Mail.

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

- **Datenhaltung freigegeben (2026-09-14):** Metadaten + Vorschauen auf dem VPS,
  volle Texte unklarer Mails zur Entscheidung an Anthropic.
- **`Posteingang/SCHOEPS intern` ist kein bevorzugter Zielordner (2026-09-14).**
  Die 7.762 Mails darin sollen nach 1. Thread und 2. Thema auf die Themenordner
  verteilt werden. Umgesetzt: `SAMMELORDNER_PFADE` in `index.py` macht ihn (und
  seinen Unterordner `Done`) zum Arbeitsordner — weiter synchronisiert, aber
  weder Ziel noch Evidenz; `scripts/intern_verteilen.py` verteilt den Bestand
  (Trockenlauf als Default, `--ausfuehren` bewegt mit auto-Kategorie und zieht den
  Index sofort nach, juengste Mail je Thread zuerst, damit sie den Rest nachzieht).
  Trockenlauf nur Thread: **2.076 von 7.762 (27 %)** finden ihren Ordner ueber den
  Thread (441 → Einladungen, 168 → Buchhaltung, 116 → Vertriebspartner …), 5.686
  brauchen das Thema (Haiku, Groessenordnung 15–20 USD fuer den ganzen Bestand).
  **Ausgefuehrt 2026-09-14 abends, Weg 1 (nur Thread + Statistik, keine KI), Go von
  Helmut:** Pilot 60 Mails (17 bewegt, per Graph gegengeprueft: Zielordner und
  Kategorie `auto-thread` stimmen), dann voller Lauf ueber 7.745 Mails: **2.305
  bewegt** (1.924 Thread, 377 Adresse, 4 Domain), **0 Fehler**, 597 nach
  `Einladungen`, 192 nach `Buchhaltung, Verwaltung`, 115 nach `Vertriebspartner,
  Händler`. **5.457 bleiben in `SCHOEPS intern`** und warten auf die KI-Verteilung
  (Weg 2 nach Phase 4, wenn Profile wie „Reisen, Bahn" von Hand geschaerft sind).
  Beobachtung: `adresse` stieg von 41 (Trockenlauf) auf 377, weil bewegte Mails
  sofort als Evidenz (Gewicht 1) zaehlen und ab zwei Mails desselben Absenders eine
  Regel bilden — gewollte Rueckkopplung, bei Thread-Qualitaet 97 % vertretbar.
  Jede Bewegung steht in `regel_entscheidung` (`ausgefuehrt = true`).
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
| 1 | Index (Ordnerbaum, Metadaten aller Ordner, Delta je Ordner), Konversationen, Ordnerprofile, **Trockenlauf-Messung**: 200 Mails aus Ordnern ziehen, Ordner verstecken, alle vier Stufen raten lassen; Ziel >= ~90 % Treffer bei `sicher` | gebaut + gelaufen 2026-09-14, siehe Befund |
| 2 | Stufen 1–3 scharf, Kategorien, Schalter `DRY_RUN` | gebaut 2026-09-15 (`sortierer.py`, `slack.py`, `worker.py`); Worker laeuft mit DRY_RUN=1, Live-Test mit Helmuts Testmails in Move steht aus, dann DRY_RUN=0 |
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
