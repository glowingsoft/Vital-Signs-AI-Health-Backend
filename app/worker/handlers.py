# What the worker does with each kind of job: save its rows to Postgres in one transaction.
# Every insert skips rows that already exist, so running the same job twice is harmless.

from app.shared.db import get_session_factory
from app.shared.jobs import AuditJob, SaveAssessmentJob
from app.worker import repository


async def handle_save_assessment(job: SaveAssessmentJob) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        # session.begin() = one transaction: COMMIT if the block finishes, ROLLBACK on any error.
        # So either every row of this check is saved, or none is.
        async with session.begin():
            assessment = job.assessment

            # a) The reading first, because the assessment points to it (foreign key).
            if job.reading is not None:
                reading_id = await repository.insert_reading(session, job.reading)
                # The reading may already exist with another id (same phone reading sent twice).
                assessment = assessment.model_copy(update={"reading_id": reading_id})

            # b) The assessment, then the rows that point to it.
            await repository.insert_assessment(session, assessment)

            # c) One row per motor that answered.
            for motor_result in job.motor_results:
                await repository.insert_motor_result(session, motor_result)

            # d) One cost row per motor that answered.
            for usage in job.ai_usage:
                await repository.insert_ai_usage(session, usage)

            # e) Who created this record and why.
            await repository.insert_audit_log(session, job.audit_log)


async def handle_audit(job: AuditJob) -> None:
    session_factory = get_session_factory()
    async with session_factory() as session:
        async with session.begin():
            await repository.insert_audit_log(session, job.audit_log)
