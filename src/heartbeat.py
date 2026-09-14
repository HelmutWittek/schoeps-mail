"""Zyklus-Quittung des Workers. „Container laeuft" ist nicht „hat zuletzt
funktioniert" — Lehre aus LifeOS (sieben Wochen stiller OAuth-Ausfall).

Beide Funktionen schlucken eigene Fehler: ein kaputter Heartbeat-Write darf
nie die Schleife killen, die er ueberwacht.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from src.db import get_session

log = logging.getLogger("schoepsmail.heartbeat")
MAX_ERROR_CHARS = 500


def _kurz(fehler: BaseException | str) -> str:
    s = f"{type(fehler).__name__}: {fehler}" if isinstance(fehler, BaseException) else str(fehler)
    s = s.replace("\x00", "")
    return s[:MAX_ERROR_CHARS] + (" …[gekuerzt]" if len(s) > MAX_ERROR_CHARS else "")


async def record_success(worker: str, items: int = 0) -> None:
    try:
        async with get_session() as s:
            await s.execute(text("""
                INSERT INTO worker_heartbeat (worker, last_run_at, last_success_at, last_error,
                                              last_items, consecutive_failures, updated_at)
                VALUES (:w, now(), now(), NULL, :n, 0, now())
                ON CONFLICT (worker) DO UPDATE SET
                    last_run_at = now(), last_success_at = now(), last_error = NULL,
                    last_items = EXCLUDED.last_items, consecutive_failures = 0, updated_at = now()
            """), {"w": worker, "n": items})
    except Exception as exc:  # noqa: BLE001
        log.warning("Heartbeat (success) nicht geschrieben: %s", exc)


async def record_failure(worker: str, fehler: BaseException | str) -> None:
    try:
        async with get_session() as s:
            await s.execute(text("""
                INSERT INTO worker_heartbeat (worker, last_run_at, last_error,
                                              consecutive_failures, updated_at)
                VALUES (:w, now(), :e, 1, now())
                ON CONFLICT (worker) DO UPDATE SET
                    last_run_at = now(), last_error = EXCLUDED.last_error,
                    consecutive_failures = worker_heartbeat.consecutive_failures + 1,
                    updated_at = now()
            """), {"w": worker, "e": _kurz(fehler)})
    except Exception as exc:  # noqa: BLE001
        log.warning("Heartbeat (failure) nicht geschrieben: %s", exc)
