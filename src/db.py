"""Async-Session gegen die Datenbank `schoepsmail` (SQLAlchemy + asyncpg).

Dieselbe Bauart wie in LifeOS: `get_session()` committet am Ende, rollt bei
Ausnahme zurueck. Parameter-Fallen siehe LifeOS-CLAUDE.md (CAST(:x AS type)
statt ::, keine :wort-Schreibweise in SQL-Kommentaren, Listen als Python-Liste).
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://schoepsmail:changeme@localhost:5432/schoepsmail",
)

_engine = create_async_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=5)
_Session = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    async with _Session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
