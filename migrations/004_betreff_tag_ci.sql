-- Migration 004: Betreff-Marken-Index ohne Beachtung der Gross-/Kleinschreibung
-- der Antwort-Praefixe ('Re:' neben 'RE:', 'Aw:' neben 'AW:'). Der Ausdruck muss
-- buchstabengleich zu src/regel.py::nach_betreff_tag sein, sonst greift der Index nicht.
--
--   docker exec -i lifeos-postgres psql -U schoepsmail -d schoepsmail < migrations/004_betreff_tag_ci.sql

DROP INDEX IF EXISTS idx_mail_betreff_tag;

CREATE INDEX IF NOT EXISTS idx_mail_betreff_tag ON mail (
    lower(substring(betreff FROM '(?i)^\s*(?:(?:AW|RE|WG|FW|FWD|Antwort|Zugesagt|Abgelehnt|Angenommen)\s*:\s*)*(\[[^\]]{2,60}\])'))
);
