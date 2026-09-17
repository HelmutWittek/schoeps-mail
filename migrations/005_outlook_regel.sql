-- Migration 005: Spiegel der Outlook-Posteingangsregeln.
--
-- Warum ueberhaupt: die Regeln im Postfach sind eine zweite, bis hierher
-- unsichtbare Automatik. Sie laufen bei der Zustellung, also vor allem, was
-- dieser Worker tut, und sie sind explizites Wissen von Helmut („dieser
-- Absender gehoert in diesen Ordner") — anders als die Statistik, die es aus
-- der Ablage erst ableiten muss. Gespiegelt wird nur gelesen; geaendert wird
-- im Postfach, nie hier.
--
-- Achtung: per Regel abgelegte Mails kommen im Index ohne `auto-*`-Kategorie
-- an und zaehlen damit als Handablage (Gewicht 2). Das ist gewollt — eine
-- Regel ist Helmuts Hand, nur vorab niedergeschrieben.
--
--   docker exec -i lifeos-postgres psql -U schoepsmail -d schoepsmail < migrations/005_outlook_regel.sql

CREATE TABLE IF NOT EXISTS outlook_regel (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    sequenz         INTEGER,
    aktiv           BOOLEAN NOT NULL DEFAULT false,
    hat_fehler      BOOLEAN NOT NULL DEFAULT false,
    bedingungen     JSONB NOT NULL DEFAULT '{}'::jsonb,
    ausnahmen       JSONB NOT NULL DEFAULT '{}'::jsonb,
    aktionen        JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Ziel getrennt gehalten, damit man ohne JSON-Gefummel joinen kann.
    ziel_ordner_id  TEXT,
    erstmals_am     TIMESTAMPTZ NOT NULL DEFAULT now(),
    gesehen_am      TIMESTAMPTZ NOT NULL DEFAULT now(),
    geaendert_am    TIMESTAMPTZ,
    -- Regel im Postfach geloescht: Zeile bleibt stehen, damit der Bericht
    -- „war mal da" zeigen kann.
    verschwunden_am TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_regel_ziel  ON outlook_regel (ziel_ordner_id) WHERE verschwunden_am IS NULL;
CREATE INDEX IF NOT EXISTS idx_regel_aktiv ON outlook_regel (aktiv) WHERE verschwunden_am IS NULL;

-- Welcher Absender wird von welcher Regel wohin gelegt? Eine Zeile je
-- Absenderangabe: `fromAddresses` liefert ganze Adressen, `senderContains`
-- Textmuster (meist eine Domain, immer schon in Grossschreibung von Outlook).
-- Empfaengerbedingungen (`sentToAddresses`) stehen bewusst nicht drin — sie
-- sagen nichts ueber den Absender.
CREATE OR REPLACE VIEW outlook_regel_absender AS
    SELECT r.id AS regel_id, r.name, r.aktiv, r.ziel_ordner_id,
           lower(a->'emailAddress'->>'address') AS adresse,
           NULL::text AS muster
      FROM outlook_regel r,
           LATERAL jsonb_array_elements(COALESCE(r.bedingungen->'fromAddresses', '[]'::jsonb)) a
     WHERE r.verschwunden_am IS NULL
       AND r.ziel_ordner_id IS NOT NULL
       AND (a->'emailAddress'->>'address') LIKE '%@%'
    UNION ALL
    SELECT r.id, r.name, r.aktiv, r.ziel_ordner_id,
           NULL::text,
           lower(s #>> '{}')
      FROM outlook_regel r,
           LATERAL jsonb_array_elements(COALESCE(r.bedingungen->'senderContains', '[]'::jsonb)) s
     WHERE r.verschwunden_am IS NULL
       AND r.ziel_ordner_id IS NOT NULL;
