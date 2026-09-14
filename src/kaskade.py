"""Die Kaskade: Stufe 1 (Absender-Statistik) → 2 (Thread) → 3 (Kandidaten) → 4 (Urteil).

`entscheide` bewegt nichts. Sie liefert ein Ergebnis-Dict mit `stufe`,
`ordner_id`, `ziel_pfad`, `sicherheit`, `begruendung`; `protokolliere`
schreibt es nach `regel_entscheidung`. Was daraus wird (Move, Kategorie),
entscheidet der Worker anhand von DRY_RUN — ab Phase 2.

`ohne_mail_id` blendet eine Mail aus der Evidenz aus. Damit misst der
Trockenlauf gegen die Historie: „haette die Kaskade diese bereits abgelegte
Mail in ihren Ordner sortiert, wenn sie sie nicht schon kennen wuerde?"
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src import konversation, llm, regel, urteil
from src.graph import Graph

log = logging.getLogger("schoepsmail.kaskade")

# Sicherheit, ab der Stufe 4 bewegen darf.
KI_BEWEGT_AB = "sicher"


def _unklar(begruendung: str, kandidaten: list[dict[str, Any]]) -> dict[str, Any]:
    return {"stufe": "unklar", "ordner_id": None, "ziel_pfad": None, "sicherheit": None,
            "anteil": None, "begruendung": begruendung, "kandidaten": kandidaten}


async def entscheide(s: AsyncSession, mail: dict[str, Any], graph: Graph | None = None,
                     ohne_mail_id: str | None = None, mit_ki: bool = True,
                     ordnerliste: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """`mail` braucht: id, von_adresse, von_domain, conversation_id, betreff, vorschau, an, von_name."""
    # Stufe 1
    t = await regel.entscheide_statistik(s, mail.get("von_adresse"), mail.get("von_domain"), ohne_mail_id)
    if t:
        return {**t, "sicherheit": "sicher", "kandidaten": []}

    # Stufe 2
    t, kandidaten = await konversation.nach_thread(s, mail.get("conversation_id"), ohne_mail_id)
    if t:
        return {**t, "sicherheit": "sicher", "kandidaten": []}

    # Stufe 3 — nur Kandidaten
    for k in await konversation.absender_bezug(s, mail.get("von_adresse"), ohne_mail_id):
        if all(k["ordner_id"] != x["ordner_id"] for x in kandidaten):
            kandidaten.append(k)

    # Stufe 4
    if not mit_ki or not llm.aktiv():
        return _unklar("keine Historie, LLM-Stufe aus", kandidaten)
    text_ = ""
    if graph is not None:
        try:
            text_ = await graph.mail_text(mail["id"], urteil.MAX_TEXT)
        except Exception as exc:  # noqa: BLE001 — Vorschau reicht als Rueckfall
            log.warning("Mailtext nicht geholt (%s), nehme Vorschau", exc)
    u = await urteil.urteile(s, mail, text_, kandidaten, ordnerliste)
    if u is None:
        return _unklar("LLM-Urteil ausgefallen", kandidaten)
    return {**u, "anteil": None, "kandidaten": kandidaten}


def bewegt(e: dict[str, Any]) -> bool:
    """Darf dieses Ergebnis eine Mail verschieben?"""
    if not e.get("ordner_id"):
        return False
    if e["stufe"] in ("adresse", "domain", "thread"):
        return True
    return e["stufe"] == "ki" and e.get("sicherheit") == KI_BEWEGT_AB


def kategorie(e: dict[str, Any]) -> str:
    return {"adresse": "auto-regel", "domain": "auto-regel", "thread": "auto-thread", "ki": "auto-ki"}[e["stufe"]]


async def protokolliere(s: AsyncSession, mail_id: str, e: dict[str, Any], dry_run: bool,
                        ausgefuehrt: bool = False) -> None:
    await s.execute(text("""
        INSERT INTO regel_entscheidung (mail_id, stufe, ziel_ordner_id, ziel_pfad, sicherheit,
                                        anteil, begruendung, dry_run, ausgefuehrt)
        VALUES (:m, :st, :oid, :pfad, :sich, :ant, :begr, :dry, :ausg)
    """), {"m": mail_id, "st": e["stufe"], "oid": e.get("ordner_id"), "pfad": e.get("ziel_pfad"),
           "sich": e.get("sicherheit"), "ant": e.get("anteil"),
           "begr": (e.get("begruendung") or "")[:1000], "dry": dry_run, "ausg": ausgefuehrt})
