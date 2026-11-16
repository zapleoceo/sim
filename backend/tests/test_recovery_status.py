from datetime import datetime, timedelta

from sim.adapters.db.models import Job
from sim.services.reply_schedule import WRITING, plan_reason


def test_expired_claim_does_not_claim_the_model_is_still_writing():
    now = datetime(2026, 11, 16, 17, 46, 37)
    job = Job(status="running", locked_until=now - timedelta(seconds=1))
    assert "проверяем выполнение" in plan_reason(job, {}, "UTC", now)
    job.locked_until = now + timedelta(seconds=90)
    assert plan_reason(job, {}, "UTC", now) == WRITING
