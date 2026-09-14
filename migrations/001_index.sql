-- Schoeps-Mail, Migration 001: der Metadaten-Index des Postfachs.
--
-- Gespeichert wird, was die Entscheidung braucht und nicht mehr: Absender,
-- Anzeigename, Empfaenger, Betreff, Kurzvorschau (255 Zeichen, so liefert sie
-- Graph), Ordner, Kategorien, Konversation. Keine Mailtexte.
--
-- Einspielen (einmalig, auf dem VPS):
--   docker exec -i lifeos-postgres psql -U schoepsmail -d schoepsmail < migrations/001_index.sql

-- ------------------------------------------------------------------
-- Ordnerbaum: Spiegel des Postfachs, Quelle der Wahrheit bleibt Exchange.
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ordner (
    id              TEXT PRIMARY KEY,           -- Graph-Ordner-ID (stabil)
    eltern_id       TEXT REFERENCES ordner(id) ON DELETE SET NULL,
    name            TEXT NOT NULL,
    pfad            TEXT NOT NULL,              -- 'Posteingang/Move', '❶ Produkte/…'
    anzahl          INTEGER NOT NULL DEFAULT 0, -- totalItemCount laut Graph
    -- Arbeits- und Altablage-Ordner sind nie Routing-Ziel und zaehlen nicht als
    -- Evidenz (Posteingang, Move, Entwuerfe, Gesendete, Geloeschte, Junk, …).
    ist_arbeitsordner BOOLEAN NOT NULL DEFAULT FALSE,
    -- Delta-Link je Ordner: damit holt der naechste Lauf nur Aenderungen.
    delta_link      TEXT,
    delta_am        TIMESTAMPTZ,
    -- Ordnerprofil (Phase 1): 2-3 Saetze, wofuer dieser Ordner steht. Von
    -- Haiku erzeugt; `profil_manuell` = Handtext, wird nie ueberschrieben.
    profil          TEXT,
    profil_manuell  BOOLEAN NOT NULL DEFAULT FALSE,
    profil_am       TIMESTAMPTZ,
    -- Ordner, den Graph nicht mehr liefert (geloescht/verschoben): bleibt fuer
    -- die Historie stehen, wird aber nicht mehr angeboten.
    verschwunden_am TIMESTAMPTZ,
    gesehen_am      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ordner_pfad ON ordner (pfad);

-- ------------------------------------------------------------------
-- Mails: eine Zeile je Nachricht, Schluessel ist die unveraenderliche Graph-ID
-- (Header `Prefer: IdType="ImmutableId"` auf JEDEM Aufruf — ohne ihn aendert
-- sich die ID beim Verschieben und die Mail waere doppelt).
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mail (
    id                  TEXT PRIMARY KEY,
    ordner_id           TEXT REFERENCES ordner(id) ON DELETE SET NULL,
    conversation_id     TEXT,
    internet_message_id TEXT,
    von_adresse         TEXT,                   -- lower()
    von_name            TEXT,
    von_domain          TEXT,                   -- lower(), Teil hinter dem @
    an                  TEXT[] NOT NULL DEFAULT '{}',   -- Adressen im An-Feld, lower()
    betreff             TEXT,
    vorschau            TEXT,                   -- bodyPreview, max. 255 Zeichen
    empfangen_am        TIMESTAMPTZ,
    gesendet_am         TIMESTAMPTZ,
    kategorien          TEXT[] NOT NULL DEFAULT '{}',
    ist_gelesen         BOOLEAN,
    hat_anhang          BOOLEAN,
    -- Von Graph als entfernt gemeldet (geloescht oder aus dem Ordner bewegt und
    -- noch nicht im Ziel gesehen). Bleibt stehen, zaehlt nicht mehr.
    entfernt_am         TIMESTAMPTZ,
    gesehen_am          TIMESTAMPTZ NOT NULL DEFAULT now(),
    aktualisiert_am     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mail_ordner        ON mail (ordner_id);
CREATE INDEX IF NOT EXISTS idx_mail_von_adresse   ON mail (von_adresse);
CREATE INDEX IF NOT EXISTS idx_mail_von_domain    ON mail (von_domain);
CREATE INDEX IF NOT EXISTS idx_mail_conversation  ON mail (conversation_id);
CREATE INDEX IF NOT EXISTS idx_mail_empfangen     ON mail (empfangen_am DESC);
CREATE INDEX IF NOT EXISTS idx_mail_imid          ON mail (internet_message_id);

-- ------------------------------------------------------------------
-- Evidenz-Sicht: welche Mail liegt wo, mit welchem Gewicht.
--
-- Handsortiert = Gewicht 2, vom Automaten abgelegt (Kategorie `auto-*`) =
-- Gewicht 1. Sonst verstaerkt der Automat seine eigenen Fehler. Eine
-- weggeschobene auto-Mail behaelt ihre Kategorie — sie liegt dann aber in
-- einem anderen Ordner als dem, den der Automat gewaehlt hat; das erkennt
-- `regel_entscheidung` (siehe unten), nicht diese Sicht.
-- ------------------------------------------------------------------
CREATE OR REPLACE VIEW mail_evidenz AS
SELECT m.id           AS mail_id,
       m.ordner_id,
       o.pfad,
       m.von_adresse,
       m.von_domain,
       m.conversation_id,
       m.empfangen_am,
       CASE WHEN EXISTS (SELECT 1 FROM unnest(m.kategorien) k WHERE k LIKE 'auto-%')
            THEN 1 ELSE 2 END AS gewicht
  FROM mail m
  JOIN ordner o ON o.id = m.ordner_id
 WHERE m.entfernt_am IS NULL
   AND o.verschwunden_am IS NULL
   AND NOT o.ist_arbeitsordner
   AND m.von_adresse IS NOT NULL;

-- ------------------------------------------------------------------
-- Protokoll jeder Entscheidung, auch im Trockenlauf. Daraus liest die
-- Bestaetigungsseite spaeter „warum liegt das hier", und die Korrektur-
-- Erkennung vergleicht `ziel_ordner_id` mit dem heutigen Ordner der Mail.
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS regel_entscheidung (
    id              BIGSERIAL PRIMARY KEY,
    mail_id         TEXT NOT NULL,
    stufe           TEXT NOT NULL,              -- adresse | domain | thread | ki | unklar
    ziel_ordner_id  TEXT,
    ziel_pfad       TEXT,
    sicherheit      TEXT,                       -- sicher | unsicher | NULL
    anteil          REAL,
    begruendung     TEXT,
    dry_run         BOOLEAN NOT NULL DEFAULT TRUE,
    ausgefuehrt     BOOLEAN NOT NULL DEFAULT FALSE,
    am              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_regel_mail ON regel_entscheidung (mail_id, am DESC);

-- ------------------------------------------------------------------
-- Heartbeat wie in LifeOS: „laeuft" ist nicht „hat zuletzt funktioniert".
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS worker_heartbeat (
    worker               TEXT PRIMARY KEY,
    last_run_at          TIMESTAMPTZ,
    last_success_at      TIMESTAMPTZ,
    last_error           TEXT,
    last_items           INTEGER,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
