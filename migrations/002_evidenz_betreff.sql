-- Migration 002: Betreff in die Evidenz-Sicht, fuer die Betreff-Marken-Regel
-- (Stufe 1b in src/regel.py). Ticket- und Projektsysteme kennzeichnen jede Mail
-- mit einer eckigen Marke am Betreffanfang, waehrend Adresse und Domain wechseln.
--
--   docker exec -i lifeos-postgres psql -U schoepsmail -d schoepsmail < migrations/002_evidenz_betreff.sql

CREATE OR REPLACE VIEW mail_evidenz AS
SELECT m.id           AS mail_id,
       m.ordner_id,
       o.pfad,
       m.von_adresse,
       m.von_domain,
       m.conversation_id,
       m.empfangen_am,
       CASE WHEN EXISTS (SELECT 1 FROM unnest(m.kategorien) k WHERE k LIKE 'auto-%')
            THEN 1 ELSE 2 END AS gewicht,
       m.betreff
  FROM mail m
  JOIN ordner o ON o.id = m.ordner_id
 WHERE m.entfernt_am IS NULL
   AND o.verschwunden_am IS NULL
   AND NOT o.ist_arbeitsordner
   AND m.von_adresse IS NOT NULL;

-- Ausdrucks-Index auf die Marke, sonst scannt jede Anfrage 70.000 Betreffe.
CREATE INDEX IF NOT EXISTS idx_mail_betreff_tag ON mail (
    lower(substring(betreff FROM '^\s*(?:(?:AW|RE|WG|FW|FWD|Antwort|Zugesagt|Abgelehnt|Angenommen)\s*:\s*)*(\[[^\]]{2,60}\])'))
);
