-- Migration 006: Nachrichtentyp je Mail (Graph `@odata.type`).
--
-- Anlass (2026-10-05): neue Einladungen haben bewusst keine Outlook-Regel —
-- Helmut soll sie im Posteingang beantworten. Danach zieht er sie selbst nach
-- `Move`, und dort fand sie keine Stufe: Kollegen-Absender entscheidet Stufe 1
-- nie, und eine Einladung beginnt einen neuen Thread. Dass eine Mail eine
-- Einladung ist, steht nur im Nachrichtentyp, nicht im Betreff. Graph liefert
-- ihn im Delta ohnehin mit (`#microsoft.graph.eventMessageRequest`); der Index
-- hat ihn bisher weggeworfen.
--
-- Gespeichert wird der Typ ohne Namensraum (`eventMessageRequest`,
-- `eventMessageResponse`, `eventMessage`), NULL fuer gewoehnliche Mails und fuer
-- alles, was vor dieser Migration indiziert und nicht nachgetragen wurde
-- (`scripts/nachrichtentyp_nachtragen.py`).
--
--   docker exec -i lifeos-postgres psql -U schoepsmail -d schoepsmail < migrations/006_nachrichtentyp.sql

ALTER TABLE mail ADD COLUMN IF NOT EXISTS nachrichtentyp TEXT;
