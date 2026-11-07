"""Integration test to verify that CHAR column types match the specification.

Ensures:
- `businesses.base_currency` is CHARACTER(3)
- `refresh_tokens.token_hash` is CHARACTER(64)
"""

import pytest
import sqlalchemy
from sqlalchemy import text


@pytest.mark.asyncio
async def test_column_types(db_session: sqlalchemy.ext.asyncio.AsyncSession):
    # Query information_schema for the two columns
    query = text("""
        SELECT data_type, character_maximum_length
        FROM information_schema.columns
        WHERE table_name = :table AND column_name = :col
    """)
    # base_currency
    result = await db_session.execute(query, {"table": "businesses", "col": "base_currency"})
    row = result.fetchone()
    assert row is not None, "base_currency column missing"
    assert row.data_type == "character", "base_currency should be CHAR"
    assert row.character_maximum_length == 3, "base_currency length should be 3"

    # token_hash
    result = await db_session.execute(query, {"table": "refresh_tokens", "col": "token_hash"})
    row = result.fetchone()
    assert row is not None, "token_hash column missing"
    assert row.data_type == "character", "token_hash should be CHAR"
    assert row.character_maximum_length == 64, "token_hash length should be 64"
