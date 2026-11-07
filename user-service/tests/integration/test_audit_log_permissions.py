"""Integration tests for audit_log permission enforcement.

The service runs with the `bizbrain_app` role which should only have
SELECT and INSERT privileges on the `audit_log` table. Attempting to
UPDATE or DELETE must raise a permission error.
"""

import uuid
import pytest
import sqlalchemy
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_audit_log_cannot_update(db_session: AsyncSession):
    # Insert a dummy audit row first (allowed)
    insert_sql = """
        INSERT INTO audit_log (audit_id, actor_type, actor_id, action, entity_type, entity_id, "before", "after", request_id, occurred_at)
        VALUES (:audit_id, 'USER', :actor_id, 'test', 'user', :entity_id, '{}', '{}', :request_id, now())
    """
    await db_session.execute(
        sqlalchemy.text(insert_sql),
        {
            "audit_id": uuid.uuid4(),
            "actor_id": str(uuid.uuid4()),
            "entity_id": uuid.uuid4(),
            "request_id": "test-req-audit",
        },
    )
    await db_session.commit()

    # Attempt to UPDATE the row – should raise a permission error
    update_sql = "UPDATE audit_log SET action = 'tampered' WHERE action = 'test'"
    with pytest.raises(sqlalchemy.exc.ProgrammingError) as excinfo:
        await db_session.execute(sqlalchemy.text(update_sql))
        await db_session.commit()
    # Ensure the underlying DB error indicates insufficient privilege
    assert "permission denied" in str(excinfo.value).lower()


@pytest.mark.asyncio
async def test_audit_log_cannot_delete(db_session: AsyncSession):
    delete_sql = "DELETE FROM audit_log WHERE 1=1"
    with pytest.raises(sqlalchemy.exc.ProgrammingError) as excinfo:
        await db_session.execute(sqlalchemy.text(delete_sql))
        await db_session.commit()
    assert "permission denied" in str(excinfo.value).lower()
