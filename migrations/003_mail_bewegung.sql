-- Migration 003: Bewegungslog — wer hat eine Mail wohin verschoben?
--
-- Graph kennt kein „wer": ein Move erscheint im Delta nur als „weg in A, neu in
-- B". Der Worker weiss aber, was er selbst bewegt hat (`quelle = 'worker'`,
-- geschrieben in sortierer.verschiebe). Jeder Ordnerwechsel, den der Index per
-- Delta sieht und nicht selbst verursacht hat, ist Helmuts Hand (`'hand'`).
-- Darauf bauen die Juengste-Hand-Regel (regel.py) und der Nachzieher.
--
--   docker exec -i lifeos-postgres psql -U schoepsmail -d schoepsmail < migrations/003_mail_bewegung.sql

CREATE TABLE IF NOT EXISTS mail_bewegung (
    id              BIGSERIAL PRIMARY KEY,
    mail_id         TEXT NOT NULL,
    von_ordner_id   TEXT,
    nach_ordner_id  TEXT,
    quelle          TEXT NOT NULL CHECK (quelle IN ('hand', 'worker')),
    am              TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Vom Nachzieher abgearbeitet (nur fuer 'hand' relevant).
    verarbeitet_am  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_bewegung_mail   ON mail_bewegung (mail_id, am DESC);
CREATE INDEX IF NOT EXISTS idx_bewegung_offen  ON mail_bewegung (quelle, verarbeitet_am) WHERE verarbeitet_am IS NULL;
CREATE INDEX IF NOT EXISTS idx_bewegung_am     ON mail_bewegung (am DESC);
