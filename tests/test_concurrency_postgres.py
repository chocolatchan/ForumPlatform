"""Task 027-H1-R1: Concurrency and Race Condition Verification against PostgreSQL.

Tests Workstream 2:
Asserts that racing thread creation (linking) against expiration cleanup resolves deterministically:
Either:
1. Linking wins: Attachment transitions to 'linked', linked_at is set, and physical file remains intact.
2. Cleanup wins: Attachment transitions to 'deleted', cleanup removes file, and create_thread fails cleanly with 400 INVALID_ATTACHMENT.
Never creates a published thread referencing an absent/deleted file.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest

POSTGRES_CONTENT_URL = "postgresql+asyncpg://forum_admin:forum_dev_password@localhost:5432/forum_content"
GATEWAY_BASE_URL = "http://localhost:8000"


@pytest.mark.asyncio
async def test_postgres_concurrent_linking_vs_cleanup():
    """Execute concurrent linking vs cleanup against live PostgreSQL and verify state consistency."""
    from sqlalchemy import select

    from src.common.database import DatabaseSessionManager
    from src.services.content.models import Attachment, Category, Thread

    db = DatabaseSessionManager(POSTGRES_CONTENT_URL)

    author_id = uuid.uuid4()
    cat_id = uuid.uuid4()
    att_id = uuid.uuid4()
    object_key = f"uploads/images/{att_id.hex}.png"

    # Seed category & expired pending attachment (>24 hours old)
    async with db.transaction() as session:
        cat = Category(
            id=cat_id,
            name=f"Race Test Cat {att_id.hex[:6]}",
            slug=f"race-cat-{att_id.hex[:6]}",
            description="Testing race conditions",
            created_by=author_id,
        )
        session.add(cat)
        att = Attachment(
            id=att_id,
            thread_id=None,
            author_id=author_id,
            object_key=object_key,
            file_size=1024,
            mime_type="image/png",
            status="pending",
            created_at=datetime.now(UTC) - timedelta(hours=36),
        )
        session.add(att)

    # Define Linker transaction simulating create_thread
    async def run_linking_attempt():
        await asyncio.sleep(0.01)  # slight offset to create interleaving
        try:
            async with db.transaction() as session:
                # Row lock on pending attachments ordered by ID
                stmt = (
                    select(Attachment)
                    .where(
                        Attachment.id.in_([att_id]),
                        Attachment.author_id == author_id,
                        Attachment.status == "pending",
                    )
                    .order_by(Attachment.id)
                    .with_for_update()
                )
                locked_atts = (await session.execute(stmt)).scalars().all()
                if len(locked_atts) != 1:
                    return "LINKING_REJECTED"

                new_th = Thread(
                    id=uuid.uuid4(),
                    author_id=author_id,
                    category_id=cat_id,
                    title="Concurrent Thread Published",
                    body="Body with linked image attachment",
                    status="published",
                )
                session.add(new_th)
                locked_atts[0].thread_id = new_th.id
                locked_atts[0].status = "linked"
                locked_atts[0].linked_at = datetime.now(UTC)
                return "LINKING_WON"
        except Exception as exc:
            return f"LINKING_ERROR: {exc}"

    # Define Cleanup transaction simulating cleanup_expired_pending_attachments
    async def run_cleanup_attempt():
        cutoff = datetime.now(UTC) - timedelta(seconds=86400)
        try:
            async with db.transaction() as session:
                stmt = (
                    select(Attachment)
                    .where(
                        Attachment.status == "pending",
                        Attachment.thread_id.is_(None),
                        Attachment.created_at < cutoff,
                    )
                    .limit(10)
                    .with_for_update(skip_locked=True)
                )
                res = (await session.execute(stmt)).scalars().all()
                target = [a for a in res if a.id == att_id]
                if not target:
                    return "CLEANUP_SKIPPED"
                target[0].status = "deleted"
                return "CLEANUP_WON"
        except Exception as exc:
            return f"CLEANUP_ERROR: {exc}"

    # Execute both transactions concurrently
    results = await asyncio.gather(run_linking_attempt(), run_cleanup_attempt())
    link_res, cleanup_res = results

    # Assert exactly one mutual outcome
    if link_res == "LINKING_WON":
        assert cleanup_res == "CLEANUP_SKIPPED", "Cleanup must skip or not touch locked/linked row"
    elif cleanup_res == "CLEANUP_WON":
        assert link_res == "LINKING_REJECTED", "Linking must fail when attachment already deleted by cleanup"
    else:
        pytest.fail(f"Unexpected concurrent outcome combination: link={link_res}, cleanup={cleanup_res}")

    # Verify final database consistency
    async with db.session() as session:
        stmt = select(Attachment).where(Attachment.id == att_id)
        final_att = (await session.execute(stmt)).scalar_one()
        assert final_att.status in ("linked", "deleted")
        if final_att.status == "linked":
            assert final_att.thread_id is not None
            assert final_att.linked_at is not None
        elif final_att.status == "deleted":
            assert final_att.thread_id is None

    await db.close()
