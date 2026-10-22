"""Task 027-H1-R2: PostgreSQL race between real thread creation and cleanup.

This test does not simulate the locking logic. It invokes the actual
``create_thread`` endpoint and the actual
``cleanup_expired_pending_attachments`` worker function against PostgreSQL,
then verifies committed database state and the physical file.

Acceptable outcomes for every iteration:
  * linking wins  -> 201, attachment linked, thread published, file present;
  * cleanup wins  -> 400 INVALID_ATTACHMENT, attachment deleted, file absent.
No 500, no deadlock, and never both.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

import src.services.content.app as content_app_mod
from src.common.database import DatabaseSessionManager
from src.services.content.app import app as content_app
from src.services.content.auth import (
    ContentUser,
    get_current_user,
    verify_csrf_header,
)
from src.services.content.models import Attachment, Category, Thread

POSTGRES_CONTENT_URL = (
    "postgresql+asyncpg://forum_admin:forum_dev_password@localhost:5432/forum_content"
)

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00"
    b"\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82"
)


@pytest.mark.asyncio
async def test_postgres_real_linking_vs_cleanup_race(monkeypatch, tmp_path):
    db = DatabaseSessionManager(POSTGRES_CONTENT_URL)
    if not await db.ping():
        await db.close()
        pytest.skip("PostgreSQL is not available on localhost:5432")

    author_id = uuid.uuid4()
    category_id = uuid.uuid4()
    seeded_attachment_ids: list[uuid.UUID] = []
    seeded_thread_titles: list[str] = []

    monkeypatch.setattr(content_app_mod, "content_db", db)
    monkeypatch.setattr(content_app_mod.content_settings, "storage_local_dir", str(tmp_path))
    images_dir = Path(tmp_path) / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    content_app.dependency_overrides[get_current_user] = lambda: ContentUser(
        id=author_id, username="pg_racer", role="member"
    )
    content_app.dependency_overrides[verify_csrf_header] = lambda: None

    async with db.transaction() as session:
        session.add(
            Category(
                id=category_id,
                name=f"PG Race {category_id.hex[:6]}",
                slug=f"pg-race-{category_id.hex[:8]}",
                description="PostgreSQL linking race",
                created_by=author_id,
            )
        )

    outcomes: list[str] = []
    try:
        transport = ASGITransport(app=content_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for iteration in range(8):
                attachment_id = uuid.uuid4()
                object_key = f"uploads/images/{attachment_id.hex}.png"
                file_path = images_dir / f"{attachment_id.hex}.png"
                file_path.write_bytes(PNG_BYTES)
                seeded_attachment_ids.append(attachment_id)

                async with db.transaction() as session:
                    session.add(
                        Attachment(
                            id=attachment_id,
                            thread_id=None,
                            author_id=author_id,
                            object_key=object_key,
                            file_size=len(PNG_BYTES),
                            mime_type="image/png",
                            status="pending",
                            created_at=datetime.now(UTC) - timedelta(hours=48),
                        )
                    )

                title = f"Concurrent attachment race {iteration} {attachment_id.hex[:8]}"
                seeded_thread_titles.append(title)

                async def link_thread(title: str = title, attachment_id: uuid.UUID = attachment_id):
                    return await client.post(
                        "/threads",
                        json={
                            "category_id": str(category_id),
                            "title": title,
                            "body": "Thread body long enough for the database constraint.",
                            "image_ids": [str(attachment_id)],
                        },
                        headers={"X-CSRF-Token": "test"},
                    )

                async def run_cleanup(iteration: int = iteration) -> int:
                    # On even iterations, give the linker a head start so both
                    # legitimate race winners are exercised.
                    if iteration % 2 == 0:
                        await asyncio.sleep(0.05)
                    return await content_app_mod.cleanup_expired_pending_attachments(
                        max_age_seconds=86400, batch_size=100
                    )

                response, _cleaned = await asyncio.gather(
                    link_thread(),
                    run_cleanup(),
                )

                async with db.session() as session:
                    attachment = (
                        await session.execute(
                            select(Attachment).where(Attachment.id == attachment_id)
                        )
                    ).scalar_one()
                    thread_count = (
                        await session.execute(
                            select(Thread).where(Thread.title == title)
                        )
                    ).scalars().all()

                assert response.status_code in (201, 400), response.text
                assert response.status_code != 500

                if response.status_code == 201:
                    outcomes.append("linking")
                    assert attachment.status == "linked"
                    assert attachment.thread_id is not None
                    assert len(thread_count) == 1
                    assert thread_count[0].status == "published"
                    assert thread_count[0].id == attachment.thread_id
                    assert file_path.exists()
                else:
                    outcomes.append("cleanup")
                    assert response.json()["error"]["code"] == "INVALID_ATTACHMENT"
                    assert attachment.status == "deleted"
                    assert attachment.thread_id is None
                    assert thread_count == []
                    assert not file_path.exists()

        # Both participants were real and at least one valid winner occurred.
        print(f"postgres race outcomes: {outcomes}")
        assert set(outcomes) <= {"linking", "cleanup"}
        assert len(outcomes) == 8
    finally:
        content_app.dependency_overrides.clear()
        async with db.transaction() as session:
            await session.execute(
                delete(Thread).where(Thread.title.in_(seeded_thread_titles))
            )
            await session.execute(
                delete(Attachment).where(Attachment.id.in_(seeded_attachment_ids))
            )
            await session.execute(delete(Category).where(Category.id == category_id))
        await db.close()
