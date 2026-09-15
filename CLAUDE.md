# CLAUDE.md — Schoeps-Mail

Mail-Automation fuer das Schoeps-Postfach `wittek@schoeps.de`. **Stand 2026-09-15
abends: Phase 2 laeuft scharf im Dauerbetrieb.** Der Worker `schoeps-mail-worker`
auf dem VPS sortiert `Posteingang/Move` mit Stufe 1–3 (seit 09:59, `DRY_RUN='0'`),
fuehrt das Bewegungslog und zieht Helmuts Handablagen per Nachzieher auf
`Move`, `Unbestimmt` und `SCHOEPS intern` nach (seit 14:43, `NACHZIEHER_DRY_RUN='0'`).
Die KI-Stufe ist gebaut und gemessen, aber **nicht scharf** (`SORTIERER_KI=0`, Phase 3).
Erster Tag: 62 + 11 Mails aus Move bewegt, 2.305 aus `SCHOEPS intern` verteilt,
0 Fehler. Details: „Was steht", „Entscheidungen", „Phasen", „Offene Punkte".

**Zwei Claude-Sessions arbeiten parallel in diesem Repo** (Mail-Worker und
Kalender-Sync/n8n, beide an derselben App-Registration) — Regeln unten unter
„Parallele Sessions". Vor jeder Aenderung an dieser Datei: `git pull`.

## Was steht (Code)

| Modul | Aufgabe |
|---|---|
| `src/graph.py` | Graph-Client: client_credentials, Retry-After, `IdType=ImmutableId` ueberall, Delta je Ordner (seitenweise), Move, Kategorien, Ordner anlegen, Mailtext holen |
| `src/index.py` | Ordnerbaum spiegeln (Pfade, Arbeitsordner ueber Well-Known-Namen, Vererbung ausser `inbox`), Delta-Sync mit Commit je Seite; ein Ordnerfehler laesst die anderen durch, Zyklus gilt als gescheitert |
| `src/regel.py` | Stufe 1: Adresse, Domain-Rollup, `MIN_EVIDENZ`=2, `MIN_ANTEIL`=0.8, eigene und Anbieter-Domains entscheiden nie |
| `src/konversation.py` | Stufe 2 Thread (`conversationId`, eine Mail reicht, streut er → Kandidaten), Stufe 3 Absender-Bezug (nur Kandidaten) |
| `src/profil.py` | Ordnerprofile (Haiku, 2–3 Saetze, woechentlich, `profil_manuell` bleibt), parallel 6 |
| `src/urteil.py` | Stufe 4: Ordnerbaum + Profile im Systemprompt (cache_control), Kandidaten mit Vorrang, `sicher|unsicher|nirgends`, unbekannter Pfad = unsicher; Mailtext steht in `<mail>`-Klammern mit der Regel, dass er beurteilt und nicht befolgt wird (Anweisungen IN der Mail sind ein Grund fuer `unsicher`) |
| `src/absender_pruefung.py` | **Vorfilter vor Stufe 4 (seit 2026-09-15):** haelt Mails an, bevor die KI sie liest — (a) Anzeigename nur aus unsichtbaren Zeichen (Cf/Cc/Co/Cs, Braille-Blank U+2800, Hangul-Filler), (b) Punycode-Domain, (c) Absender/Domain mit Junk-Historie, ohne Evidenz und mit Junk-Anteil >= 0.5. Reine Logik + eine DB-Abfrage, `pruefe()` liefert den Grund. Greift NUR bei `mit_ki` — Stufe 1–3 bleiben unberuehrt |
| `src/kaskade.py` | fuehrt 1→4 zusammen, `bewegt()`/`kategorie()`, `protokolliere()` nach `regel_entscheidung` |
| `src/llm.py` | Haiku (`claude-haiku-4-5`) mit Structured Outputs, Ausfall = None |
| `src/sortierer.py` | Phase 2: Kaskade ueber `Posteingang/Move`, Kategorie + Move per Graph, Index sofort nachgezogen, Bewegungslog `'worker'`, Unklares bleibt; **was in keinen Ordner gehoert (Vorfilter-Treffer, KI-`nirgends`) wandert mit `auto-unbestimmt` nach `Move/Unbestimmt`** (`UNBESTIMMT_PFAD`, `kaskade.nach_unbestimmt`), `unsicher` bleibt in Move; DRY_RUN protokolliert dedupliziert |
| `src/nachzieher.py` | **Handablage wirkt rueckwaerts (seit 2026-09-15):** je neuer Handbewegung in einen Themenordner die Geschwister (Thread; Absender per Juengste-Hand-Regel) aus den Quell-Ordnern `Move`, `Move/*`, Sammelordnern nachziehen. Andere Themenordner werden nie angefasst (Phase 4: Vorschlag), Posteingang ist keine Quelle. `NACHZIEHER_DRY_RUN=1` (Default) protokolliert nur und laesst die Bewegungen offen; `NACHZIEHER_MAX_JE_LAUF`=150. **Scharf seit 2026-09-15 14:43** (`NACHZIEHER_DRY_RUN='0'` in der VPS-.env): Test mit 6 Handbewegungen (5 aus `Unbestimmt`, 1 aus Posteingang) → 2 Thread-Geschwister aus `SCHOEPS intern` nachgezogen, von Helmut als richtig bestaetigt |
| `src/slack.py` | `sende`/`alarm` (gedrosselt je Schluessel, 60 min) in `#mail-sortierer` |
| `src/worker.py` | Schleife: je Zyklus Move-Delta + Sortieren; alle 8 Zyklen Voll-Sync, Profile, Nachzieher; Heartbeats `sortierer`/`index`; Slack-Alarm ab 3 Fehlern; Secret-Ablauf-Warnung |
| `migrations/001_index.sql` | `ordner`, `mail`, Sicht `mail_evidenz` (Gewicht Hand=2/auto=1), `regel_entscheidung`, `worker_heartbeat` |
| `migrations/002_evidenz_betreff.sql` | `betreff` in der Evidenz-Sicht + Ausdrucks-Index fuer die Betreff-Marke |
| `migrations/003_mail_bewegung.sql` | **Bewegungslog**: jeder Ordnerwechsel, `quelle` `'hand'` (per Delta gesehen) oder `'worker'` (eigener Move, in `sortierer.verschiebe` geschrieben — der Index sieht die Mail danach schon im Ziel und meldet keinen Wechsel). Graph kennt kein „wer"; das Audit-Log von Exchange waere Purview-only mit 24 h Verzug |
| `migrations/004_betreff_tag_ci.sql` | Marken-Index case-insensitiv (`Re:` neben `RE:`); Ausdruck buchstabengleich zu `regel.nach_betreff_tag` |
| `scripts/` | `index_lauf.py`, `profil_lauf.py`, `trockenlauf.py` (Messung, `--nur-ki`), `intern_verteilen.py` (Sammelordner aufloesen), `test_regel.py` (27 Pruefungen ohne DB) |

**Juengste-Hand-Regel** (`regel.nach_juengster_hand`, vor der Adress-Statistik, nicht fuer
eigene Domain): zeigen die letzten `REGEL_JUENGSTE_HAND_N`=3 Handbewegungen von Mails
eines Absenders in denselben Themenordner, gewinnt dieser — sonst wuerde ein neuer
Ordner erst greifen, wenn die Mehrheit der alten Historie umgekippt ist. Greift erst,
seit das Bewegungslog gefuellt wird (ab 2026-09-15 14:21).

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
werden fuer die LLM-Entscheidung geholt, nie gespeichert. **Freigabe der
Datenhaltung durch Helmut am 2026-09-14** (siehe Entscheidungen).

## Zugang (Phase 0, erledigt 2026-09-14)

- **MS Graph mit Application Permissions**, nicht IMAP, nicht delegiert.
  App-Registration `LifeOS-Exchange-Reader` (Helmuts eigene, multi-tenant),
  Client-ID `1efdf29e-47c3-4f27-b423-50dadb4b48a3`, Tenant
  `a4941ae5-fcbb-4e45-85f2-fcbc3d7d7079`. Rollen im Token: `Mail.ReadWrite`,
  `MailboxSettings.ReadWrite`, `Calendars.Read` (seit 2026-09-15). **Die frueher
  hier notierte `Calendars.ReadWrite` stand nie im Token** — aus dem JWT
  dekodiert waren es nur die beiden Mail-Rollen, `/users/<UPN>/calendarView`
  antwortete 403 `ErrorAccessDenied`. Vermutlich war sie damals als DELEGIERTE
  Berechtigung eingetragen, und die taucht in einem App-only-Token nie auf.
  Wer eine Rolle vermisst: Token holen, Mittelteil base64-dekodieren, `roles`
  lesen — das ist die einzige verlaessliche Quelle, die Portal-Ansicht nicht.
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

## Kalender-Sync nach Google (n8n, seit 2026-09-15)

Kein Code in diesem Repo — der Sync lebt vollstaendig in n8n
(`n8n.hauptmikrofon.de`, Workflow `Exchange → Google Kalender Sync`,
ID `eZq2tZusJSgqIjCH`, Schedule alle 10 Minuten). Er steht trotzdem hier, weil
er an derselben App-Registration haengt wie der Mail-Worker und dessen
Berechtigungen mitbenutzt.

- **Richtung nur Exchange → Google**, nie zurueck. Ziel ist der Google-Kalender
  „SCHOEPS sync" (`1db00735…de30f094@group.calendar.google.com`) im Konto
  `hawittek@gmail.com`. Fenster −60/+365 Tage, das sind 178 Termine (2026-09-15).
- Gelesen wird `/users/wittek@schoeps.de/calendarView` mit **denselben Client
  Credentials** wie hier (n8n-Credential Typ `oAuth2Api`, Grant
  `clientCredentials`, Scope `.default`). Der n8n-Outlook-Node kann nur
  delegiert — daran waere der Sync alle 3–4 Wochen an der MFA-Erzwingung des
  Tenants gestorben, wie der LifeOS-Kalender-Worker.
- Uebertragen werden **nur Titel, Start/Ende und Ort**. Keine Beschreibung,
  keine Teilnehmer, keine Teams-Links. Als `private`/`confidential` markierte
  Termine werden zu „Belegt" — greift derzeit nie, keiner der 178 ist so
  markiert. Wer einen Termin verbergen will, muss ihn in Outlook kennzeichnen.
- **`calendarView` ohne Kalenderangabe liefert nur den Standardkalender.** Das
  Postfach hat sieben (`Karin Fléing`, `Privat Google`, `Geburtstage`, Asana,
  openHAB, Feiertage). Helmuts Entscheidung 2026-09-15: **nur der
  Standardkalender**. `Privat Google` waere ohnehin eine Rueckspiegelung seines
  Google-Kalenders und wuerde jeden privaten Termin doppeln. Die
  Urlaubsbalken der Kollegen liegen in keinem der sieben und waeren fuer die
  App auch nicht lesbar — die Access Policy begrenzt sie auf sein Postfach.
- **Google drosselt SCHREIBzugriffe je Kalender hart.** Mit dem Batching aus
  der Vorlage (10 Anfragen/s) kam jeweils die erste durch, der Rest bekam
  `403 Rate Limit Exceeded`: von 64 Terminen landeten 34 im Kalender — und die
  Ausfuehrung galt trotzdem als **erfolgreich**, weil die Schreib-Nodes auf
  `onError: continueRegularOutput` stehen. Jetzt 1 Anfrage / 1,1 s mit 3
  Wiederholungen à 5 s. **Lehre: bei `continueRegularOutput` sagt der gruene
  Haken nichts** — die Fehler stehen als `json.error` an den einzelnen Items
  des Execution-Protokolls, und geprueft wird am Endzustand, nicht am Status.
- Abgleich ueber `extendedProperties.private.graphKey` (FNV-1a-Hash der
  Graph-ID, die selbst zu lang fuer das Feld ist) und `sig`
  (Titel|Start|Ende|Ort|transparency). Angefasst wird nur, was
  `graphSource=schoeps` traegt — Handgemachtes im Zielkalender bleibt.
  Alles idempotent: was scheitert, wird im naechsten Zyklus nachgeholt.
- Abgesagte und von Helmut abgelehnte Termine werden uebersprungen,
  `showAs: free` wird als `transparency: transparent` uebernommen.
- **Abnahme 2026-09-15:** nach der Drosselung 98 Termine in 110 s nachgefuellt,
  KW39 stimmt 7:7 gegen Exchange, die beiden Folgelaeufe erzeugten **null**
  Anweisungen (ein einzelner Patch davor war eine echte Aenderung in Exchange,
  kein Churn). `saveDataSuccessExecution` steht danach wieder auf `none` —
  eine Ausfuehrung legt die vollen Exchange- und Google-Antworten ab, knapp
  1 MB, das waeren bei 144 Laeufen/Tag ueber 130 MB taeglich in der
  n8n-Datenbank. Zum Debuggen voruebergehend auf `all` stellen.

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
  per Graph, preset2/8/10/11) und seit 2026-09-15 **`auto-unbestimmt`** (vom
  Worker beim Start angelegt, fuer alles, was nach `Move/Unbestimmt` wandert).

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
  **Ergaenzung 2026-09-15:** Helmut hat `Posteingang/Move/Unbestimmt` angelegt
  fuer Mails, die er selbst nicht sortieren kann. Der Ordner erbt ueber den
  Pfad die Arbeitsordner-Eigenschaft von `Move` — nie Ziel, nie Evidenz (sonst
  wuerde der Automat lernen, Absender dorthin zu sortieren: die Sackgasse, die
  LifeOS bei `INBOX/Unbekannt` ausgeschlossen hat). Der Sortierer bearbeitet nur
  `Move` selbst, nicht seine Unterordner. Erster Tag scharf: 62 Mails bewegt
  (38 Adresse, 6 Domain, 18 Thread), 0 Fehler, 31 in `Unbestimmt`, 1 in `Move`.
- **Post, die in keinen Ordner GEHOERT, raeumt der Sortierer nach
  `Move/Unbestimmt` weg** (Entscheidung Helmut 2026-09-15, auf die Frage, wohin
  eingehende Kaltakquise soll). Marke `auto-unbestimmt`. Zwei Faelle:
  Absender-Vorfilter hat angehalten, oder die KI sagt `nirgends`. **Nicht**
  `unsicher` — da passt das Thema und nur der Ordner ist unklar, das soll Helmut
  sehen; und nicht `unklar` ohne Vorfilter, das wartet nur auf Evidenz. Der
  Ordner bleibt Arbeitsordner: die KI kann ihn nicht waehlen (er steht nicht im
  Ordnerbaum), und er wird nie Evidenz. Wirksam mit `SORTIERER_KI=1`.
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

## Betrieb (Ist-Stand 2026-09-15)

- **Wo:** VPS `root@212.227.161.180` (SSH unter Windows: `ssh -i
  "/c/Users/Wittek/.ssh/id_ed25519" root@…`), Repo `/opt/schoeps-mail` (Klon von
  GitHub, oeffentlich), Container `schoeps-mail-worker` (`docker compose up -d`),
  DB `schoepsmail` / Rolle `schoepsmail` in `lifeos-postgres`, Netz
  `lifeos_default` (extern). Bestaetigungsseite hinter Caddy: noch nicht gebaut (Phase 4).
- **`.env`** (chmod 600, Werte in Single-Quotes, nie per `source`):
  `SCHOEPSMAIL_DB_PASSWORD`, `GRAPH_CLIENT_SECRET`, `GRAPH_SECRET_ABLAUF`
  (`2028-09-14`), `SLACK_BOT_TOKEN`, `ANTHROPIC_API_KEY` (derselbe wie LifeOS),
  Schalter `DRY_RUN='0'`, `NACHZIEHER_DRY_RUN='0'`, `SORTIERER_KI` (fehlt = 0).
  **Die .env wird nur beim Erzeugen des Containers gelesen** — nach einer
  Aenderung `docker compose up -d worker`, ein `restart` reicht nicht.
- **Deploy von Code:** `cd /opt/schoeps-mail && git pull && docker compose restart
  worker` — `src/`, `scripts/`, `migrations/` sind Bind-Mounts, kein Build noetig.
  Build nur bei `requirements.txt`/`Dockerfile`. Migrationen von Hand:
  `docker exec -i lifeos-postgres psql -U schoepsmail -d schoepsmail <
  migrations/00N_x.sql` — eingespielt: 001, 002, 003, 004.
- **Skripte:** `docker compose run --rm -T --no-deps worker python scripts/<x>.py`
  (`PYTHONPATH=/app` steht in Compose und Dockerfile, `PYTHONUTF8=1` wegen `❶`).
- **Beobachten:** `docker compose logs -f worker`; `worker_heartbeat` (`sortierer`
  jeder Zyklus, `index` beim Voll-Sync); `regel_entscheidung` (jede Entscheidung,
  `ausgefuehrt`/`dry_run`); `mail_bewegung` (hand/worker). Slack-Alarm in
  `#mail-sortierer` ab 3 Fehlzyklen in Folge und 30 Tage vor Secret-Ablauf,
  gedrosselt auf einen je Stunde und Schluessel.
- **Takt:** Move alle 120 s, Voll-Sync + Profile + Nachzieher alle 8 Zyklen (16 min).
  Handbewegungen werden also mit bis zu 16 min Verzug erkannt.

## Parallele Sessions (seit 2026-09-15)

Helmut faehrt mehrere Claude-Sessions gleichzeitig, auch in diesem Ordner
(Mail-Worker; Kalender-Sync nach Google in n8n). Beide schreiben in diese Datei
und committen auf `main`. Deshalb, wie in LifeOS gelernt:

- **Vor dem Bearbeiten von `CLAUDE.md` immer `git pull`**, danach zeitnah
  committen und pushen — sonst ueberschreibt der naechste Edit fremde Absaetze.
- **Nur selbst geaenderte Dateien stagen**, Pfade ausschreiben, nie `git add -A`.
  Kein History-Rewrite, kein `--force`, fremde Commits erklaeren statt umschreiben.
- Auf dem VPS vor `git pull` pruefen, ob fremde Commits mitkommen; der Worker
  liest Code per Bind-Mount erst beim `restart`, ein Pull allein aendert nichts.
- Fremde Fakten in dieser Datei nicht „korrigieren", ohne sie zu pruefen:
  Beispiel Calendars-Rolle am 2026-09-15 — eine Session sah `Calendars.ReadWrite`,
  die naechste keine Kalender-Rolle, die dritte `Calendars.Read`; alle drei
  stimmten zu ihrem Zeitpunkt, Helmut hatte die Berechtigung zwischendurch
  umgestellt. Massgeblich ist immer das aktuelle Token (`roles` im JWT).

## Spam und Stufe 4 (Befund 2026-09-15)

Anlass war die private Email-Automation (`C:\PROJEKTE\Email-Verarbeitung`), wo
eine Spam-Mail mit dem Anzeigenamen U+2800 (Braille-Blank) und gefaelschter
Absenderadresse eine Akte erzeugt hat. Frage: kann das hier auch passieren?

- **In Phase 2 nicht.** Stufe 1–3 entscheiden ausschliesslich aus der
  Ablage-Historie. Ein Spam-Absender hat keine, keinen Thread und keine
  Betreff-Marke — er faellt durch alle drei und bleibt in `Move` liegen. Helmuts
  Entscheidung „Unklares bleibt liegen" wirkt hier als Spamfilter. Auch Spoofing
  der eigenen Domain traegt nicht: `wittek@schoeps.de` liegt mit 10.824
  gewichteten Mails nur zu 52 % im Top-Ordner und reisst die strenge Schwelle
  (90 %, `MIN_ANTEIL_STRENG`) nicht.
- **Ab Phase 3 schon.** Stufe 4 ist die einzige Stufe, die OHNE Historie
  entscheidet — also genau die, die jede Spam-Mail erreicht. Deshalb der
  Vorfilter `absender_pruefung.py` davor.
- **Der Tarnzeichen-Fall selbst kommt hier nicht vor:** 0 Treffer unter 463
  Absendernamen mit Nicht-ASCII-Zeichen, 0 Punycode-Domains, 0 in den 6.132
  Mails der letzten 12 Monate. Die Regel ist Vorsorge, keine Reparatur. Die
  zwei einzigen Namen mit „unsichtbaren" Zeichen sind japanische Absender
  (`ysonoda@ktmail.tokai-u.jp`, `ken-usami@capcom.com`) mit U+3000 als Trenner —
  sie haben echten Text daneben und schlagen nicht an. **Falle:** U+3000 und
  U+00A0 entfernt `str.strip()` bereits, die Fuellzeichenliste braucht sie nicht.
- **Das wirksame Merkmal ist stattdessen die Junk-Historie.** 840 Mails von 255
  Absendern lagen je im Junk; nur 27 dieser Absender haben auch Evidenz in einem
  Zielordner. Gesperrt wird nur, wer alle drei Bedingungen erfuellt: Junk-Historie,
  KEINE Evidenz, Junk-Anteil >= 0.5. Jede einzelne Bedingung ist noetig:
  `slite.com` liegt 22x im Junk, hat aber 341 Evidenz-Mails; `no-reply@news.lawo.com`
  (Branchen-Hersteller) lag 1 von 3 Mails im Junk und kaeme ohne die
  Anteilsschwelle nie mehr durch. Von 228 sonst betroffenen Absendern liegen 214
  ausschliesslich im Junk — die Schwelle kostet also fast keine Trennschaerfe.
- **Gegenprobe an den 14 Mails, die am 15.09. in `Move`/`Unbestimmt` lagen:** 5
  angehalten (Konferenz-Akquise `cfp@scika.org`, `smart-it.com`, Messe-Newsletter
  `thesaudifoodshow.com`, `shared1.ccsend.com`), 9 durchgelassen — darunter
  `no-reply@news.lawo.com`, `dhd.news@dhd-audio.de` und eine Bewerbung.
- **`Move` ist kein vorgefilterter Eingang** (Helmut am 2026-09-15): er zieht die
  Mails selbst hinein, aber „oft einfach bulkmaessig alles". Damit landet auch
  alles dort, was Exchange durchgelassen hat — der Vorfilter ist also Betrieb,
  nicht Vorsorge. Die Luecke, die er offen laesst: Kaltakquise OHNE Junk-Historie
  (`kayne@mymyaily.co`, `madison.gardner@institutionalinvestments…`,
  `s_vinther@tgdgtm.com`) geht in Phase 3 an die KI. Dort muessen `nirgends` und
  „nur `sicher` bewegt" sie halten.
- **Volle Kaskade mit KI gegen die 28 Mails in `Move`/`Unbestimmt` gemessen**
  (2026-09-15 abends, nichts bewegt): **11 vom Vorfilter angehalten** (alle
  Akquise/Messe-Werbung, keine Fehlalarme), **6 `nirgends`**, **6 `unsicher`** —
  22 der 28 bleiben also zu Recht liegen. **5 `sicher`**, und da liegt die
  eigentliche Schwaeche: neben einer plausiblen Zuordnung (Bewerbung →
  `❽ Jobs/Buchhaltung`) sortiert Haiku **Kaltakquise thematisch ein**:
  `kayne@mymyaily.co` (PCB-Angebot) → `❷ Einkauf, Fertigung/Lieferanten`,
  `s_vinther@tgdgtm.com` (Lead-Gen-Akquise) → `❹ Marketing/Werbung`,
  Recruiting-Anfrage → `❸ Vertrieb/Vertriebspartner, Händler`; einmal
  `Posteingang/Standby` fuer einen chinesischen Fertiger. Thematisch nicht
  absurd, aber es sind Erstkontakte ohne Geschaeftsbeziehung. **Vor Phase 3 von
  Helmut zu klaeren:** gehoert eingehende Kaltakquise in den Themenordner, oder
  braucht die Prompt-Regel „Newsletter und Werbung" einen Zusatz fuer
  unaufgeforderte Erstkontakte? Ohne diese Entscheidung nicht scharfschalten.
- **Dabei gefunden und behoben:** Haiku schreibt statt `sicherheit: nirgends`
  gelegentlich das Wort „nirgends" in das PFAD-Feld (4 der 28 Faelle, 14 %).
  Das landete im Zweig „unbekannter Pfad" und wurde als `unsicher`
  protokolliert — verschoben wurde nichts, aber `nirgends` ist die Quelle der
  Ordnervorschlaege (Phase 4), und dort fehlten die Faelle. Jetzt fangt
  `urteil.ziel_leer()` die Wortformen ab (`PFAD_KEINER`). Ebenfalls gesehen:
  ein erfundener Pfad (`❺ Ausstellung/Ravenna, Netzwerk` fuer den
  Lawo-Newsletter) — wird wie gebaut als `unsicher` verworfen.
- **Prompt-Regel gegen Anbieter-Akquise** (`urteil.REGEL_AKQUISE`, eigene
  Konstante, damit ein Messlauf sie abziehen kann): unaufgeforderte Angebote von
  Anbietern sind `nirgends`, auch wenn ein Ordner thematisch passt; ausgenommen
  Kundenanfragen, Bewerbungen, Presse, Branchen-Einladungen. **A/B an derselben
  Mailmenge gemessen** (beide Fassungen je Mail, weil Haiku zwischen Laeufen
  stark streut — der Lawo-Newsletter bekam in drei Calls drei Antworten):
  - *28 Move-Mails:* 5 Aenderungen, 4 davon gewollt (PCB-Angebot, Fertiger aus
    China, Gulfood-Messe, Personalvermittler → `nirgends`). `unsicher` fiel von
    3 auf 0, `sicher` von 3 auf 2. Mit Zusatz wandern 26 der 28 nach
    `Unbestimmt`. Nebeneffekt: der Lawo-Newsletter kippte von `unsicher` auf
    `sicher ❸ Vertrieb/Bekannte, Branche`.
  - *34 abgelegte Mails aus der Historie (Gegentest „schadet der Zusatz echter
    Post?"):* **`nirgends` blieb bei 1 — keine einzige echte Geschaeftspost
    wurde als Akquise verworfen.** Das war das Risiko, es ist nicht eingetreten.
    Trefferquote bei `sicher` nominell 16/28 (57 %) ohne, 14/28 (50 %) mit
    Zusatz; die Unterschiede liegen aber bei Faellen ohne Akquise-Bezug
    (`❽ Jobs/Entwicklung…` → `❽ Jobs`, Illusonic-Mails springen zwischen
    `❶ Produkte/Digital/Illusonic` und `❸ Vertrieb/Bekannte, Branche`) — bei 28
    Entscheidungen sind 2 Faelle Rauschen, kein Signal.
- **Beide Fassungen liegen bei 50–57 % und damit weit unter dem 90-%-Ziel**
  (dokumentiert waren 66 % auf anderer Stichprobe). Phase 3 ist unabhaengig vom
  Akquise-Zusatz noch nicht scharfschaltbar.
- **Haiku laesst das Bereichspraefix weg** — `Produkte/Digital/Illusonic` statt
  `❶ Produkte/…`, `Posteingang/Slite` statt `Posteingang/Redmine, Planio,
  Slite`. Der Pfad gilt dann als unbekannt und die Entscheidung wird verworfen
  (5x in einem Lauf ueber 34 Mails). Ein toleranter Abgleich — Praefixziffern
  und `❶…❾` ignorieren, nur bei genau EINEM Treffer zuordnen — wuerde einen Teil
  zurueckholen. Noch nicht gebaut, Entscheidung Helmut.
- **Prompt-Injection:** der Mailtext ging bis dahin unmarkiert in den Urteils-
  Prompt. Jetzt in `<mail>`-Klammern, mit der Regel, dass Anweisungen darin ein
  Merkmal der Mail sind (Grund fuer `unsicher`), kein Auftrag. Der Schaden waere
  ohnehin begrenzt — das Urteil kann nur Pfade aus der Liste waehlen.

## Offene Punkte

- **Phase 3, KI-Stufe scharf** (`SORTIERER_KI=1`): gebaut, gemessen (Haiku 66 %,
  Sonnet 76 % auf reinen KI-Faellen). Vorher Profile fuer Konventions-Ordner von
  Hand schaerfen (`Reisen, Bahn`, `IT`/`Software`/`AI, Automation`), sonst bleibt
  die Quote dort. Modellwahl offen: Sonnet praeziser, doppelter Preis. Der
  Absender-Vorfilter dafuer steht bereits, ebenso das Wegraeumen nach
  `Move/Unbestimmt` (Entscheidungen, siehe dort). Was beim Scharfschalten zu
  beobachten ist: Haiku sortiert Erstkontakt-Werbung mit `sicher` in
  Themenordner (3 der 5 `sicher`-Faelle in der 28er-Messung, z. B. PCB-Akquise →
  `❷ Einkauf, Fertigung/Lieferanten`). Das Wegraeumen faengt nur die
  `nirgends`-Faelle — diese hier nicht.
- **Phase 4:** Ordnervorschlaege A–C, Slack-Push mit Link, Bestaetigungsseite
  hinter Caddy (Basic Auth), Profile editierbar, Nachzieher-Vorschlaege fuer
  Geschwister in anderen Themenordnern.
- **`SCHOEPS intern`:** 5.457 Mails warten auf die KI-Verteilung (Weg 2); der
  Nachzieher leert den Ordner nebenbei ueber Helmuts Handablagen.
- **`Move/Unbestimmt`** als KI-Warteschlange nutzen, sobald Phase 3 laeuft.
- **Lokale Geheimnis-Dateien** `C:\PROJEKTE\graph_secret.txt` und
  `C:\PROJEKTE\Slack token.txt` liegen noch — beide stehen in der VPS-.env;
  Loeschen ist Helmuts Entscheidung.
- Einmal je Lauf `400 Invalid request data` von Anthropic (Ursache offen, jetzt
  mit Textlaenge/-anfang geloggt); Haiku erfindet gelegentlich Pfade (wird als
  unsicher verworfen). Test `scripts/test_regel.py` ist der einzige automatische
  Test; DB-Tests fuer Bewegungslog/Nachzieher fehlen (Live-Test am 15.09. statt).

## Phasen

| Phase | Inhalt | Stand |
|---|---|---|
| 0 | Zugang, Move, Kategorien, Slack | erledigt 2026-09-14 (Policy-Gegenprobe offen) |
| 1 | Index (Ordnerbaum, Metadaten aller Ordner, Delta je Ordner), Konversationen, Ordnerprofile, **Trockenlauf-Messung**: 200 Mails aus Ordnern ziehen, Ordner verstecken, alle vier Stufen raten lassen; Ziel >= ~90 % Treffer bei `sicher` | gebaut + gelaufen 2026-09-14, siehe Befund |
| 2 | Stufen 1–3 scharf, Kategorien, Schalter `DRY_RUN`; **dazu Bewegungslog, Juengste-Hand-Regel, Nachzieher** (Helmuts Wunsch vom 15.09.: Handablage soll auch rueckwaerts wirken) | **scharf seit 2026-09-15** (Sortierer 09:59, Nachzieher 14:43); Live-Tests 22 bzw. 6 Mails, alle Entscheidungen von Helmut bestaetigt |
| 3 | Stufe 4 (KI) scharf, `Unbestimmt` als KI-Warteschlange, Protokoll mit Begruendungen | offen — Profile vorher schaerfen |
| 4 | Vorschlaege A–C, Slack-Push, Bestaetigungsseite, Profile editierbar, Nachzieher-Vorschlaege fuer andere Themenordner | offen |
| 5 | Alarme vervollstaendigen, Doku, DB-Tests | teils (Heartbeat + Slack-Alarm laufen) |

Tests: `scripts/test_regel.py` (27 Pruefungen ohne DB) im Container laufen lassen.
Regel fuer kuenftige DB-Tests: nur eigene Testdaten (`ZZTEST…`), Aufraeumer loeschen
nur eigene Spuren, ein Lauf mit gestelltem Urteil wird auf die Testdaten begrenzt.

## Skripte (`scripts/`)

- `index_lauf.py [--nur-ordner]` — Ordnerbaum spiegeln, alle Ordner per Delta
  nachziehen, Kennzahlen. Erstlauf 20 min.
- `profil_lauf.py [--nur-fehlende]` — Ordnerprofile erzeugen/auffrischen (Haiku).
- `trockenlauf.py [--n 200] [--ohne-ki] [--nur-ki] [--monate 12]` — Messung gegen
  die Historie je Stufe; `--nur-ki` misst die KI auf Faellen, die Stufe 1–3 offen
  lassen; `-e ANTHROPIC_MODELL=claude-sonnet-5` fuer den Modellvergleich.
- `intern_verteilen.py [--ohne-ki] [--ausfuehren] [--limit] [--monate]` —
  Sammelordner aufloesen, Trockenlauf als Default.
- `test_regel.py` — Logik-Tests ohne DB (27 Pruefungen).
- `test_absender_pruefung.py` — Vorfilter-Logik ohne DB (23 Pruefungen: Tarnzeichen, echte Namen, Punycode).
- Phase 0: `graph_app_test.py` (Client-Credentials, Ordnerbaum, Negativtest,
  Kategorien), `graph_policy_wait.py` (pollt bis 403), `graph_delegiert_test.py`
  (Device-Code-Notnagel), `slack_test.py` (Bot-Token, Testnachricht). Lesen das
  Secret aus `C:\PROJEKTE\graph_secret.txt`.
