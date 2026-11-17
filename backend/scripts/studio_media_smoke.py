"""Only called by the guarded disposable PostgreSQL CI entry point."""
import asyncio
from uuid import uuid4

from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from sim.adapters.crypto import SecretBox
from sim.adapters.db.models import AdminUser
from sim.adapters.db.session import make_session_factory, transaction
from sim.domain.enums import Role
from sim.ports.speech import SpeechResult
from sim.services.studio_media import MediaRequest, StudioConflict, StudioMedia


async def studio_media_checks(url):
    engine = create_async_engine(url)
    factory = make_session_factory(engine)
    box = SecretBox(Fernet.generate_key().decode())
    entered, finish = asyncio.Event(), asyncio.Event()

    class FakeSpeech:
        calls = 0

        async def synthesize(self, text, **kwargs):
            self.calls += 1
            entered.set()
            await finish.wait()
            return SpeechResult(b"synthetic", "audio/wav")

    fake = FakeSpeech()
    service = StudioMedia(factory, box, fake, None, enabled=True)
    try:
        async with transaction(factory) as session:
            owner = AdminUser(role=Role.OWNER, display_name="Synthetic Studio owner")
            session.add(owner)
            await session.flush()
            owner_id = owner.id
        request = MediaRequest(operation_id=uuid4(), kind="speech", text="Synthetic")
        first = asyncio.create_task(service.execute(owner_id, request))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            # Independent service/session sees committed intent while provider is blocked.
            restarted = StudioMedia(factory, box, fake, None, enabled=True)
            same = await asyncio.wait_for(restarted.execute(owner_id, request), 5)
            assert same.status == "unknown" and fake.calls == 1
            try:
                await asyncio.wait_for(restarted.execute(owner_id,
                    request.model_copy(update={"operation_id": uuid4()})), 5)
                raise AssertionError("new UUID bypassed unknown receipt")
            except StudioConflict:
                pass
            finish.set()
            done = await asyncio.wait_for(first, 5)
            assert done.status == "complete"
            assert (await restarted.execute(owner_id, request)).status == "complete"
            assert fake.calls == 1
        finally:
            if not first.done():
                first.cancel()
            await asyncio.gather(first, return_exceptions=True)
        # Simulate process cancellation while a second provider attempt is in progress.
        entered.clear()
        finish.clear()
        second_request = request.model_copy(update={"operation_id": uuid4()})
        second = asyncio.create_task(service.execute(owner_id, second_request))
        try:
            await asyncio.wait_for(entered.wait(), 5)
        finally:
            second.cancel()
            await asyncio.gather(second, return_exceptions=True)
        assert (await restarted.execute(owner_id, second_request)).status == "unknown"
        assert fake.calls == 2
        print("PostgreSQL Studio committed intent, concurrent duplicate, restart and cancellation PASS")
    finally:
        await engine.dispose()


async def discard_synthetic_studio_history(url):
    # Only the guarded disposable CI runner may erase these synthetic receipts.
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DELETE FROM studio_operation"))
    finally:
        await engine.dispose()
