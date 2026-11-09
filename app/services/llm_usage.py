"""Per-tenant daily LLM call budget."""

from datetime import date

from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LlmUsage


async def reserve_call(session: AsyncSession, tenant_id: str, day: date, cap: int) -> bool:
    """Count one LLM call against today's cap. False if the cap is already reached.

    The check and the increment are one atomic statement, so concurrent workers cannot both
    slip past the cap. A reservation is spent even if the call then fails: providers may bill
    failed calls, and a failing loop should not be able to call without limit.
    """
    if cap <= 0:
        return False
    stmt = (
        insert(LlmUsage)
        .values(tenant_id=tenant_id, day=day, calls=1)
        .on_conflict_do_update(
            index_elements=[LlmUsage.tenant_id, LlmUsage.day],
            set_={"calls": LlmUsage.calls + 1, "updated_at": func.now()},
            where=LlmUsage.calls < cap,
        )
        .returning(LlmUsage.calls)
    )
    return (await session.scalar(stmt)) is not None


async def record_tokens(
    session: AsyncSession, tenant_id: str, day: date, input_tokens: int, output_tokens: int
) -> None:
    await session.execute(
        update(LlmUsage)
        .where(LlmUsage.tenant_id == tenant_id, LlmUsage.day == day)
        .values(
            input_tokens=LlmUsage.input_tokens + input_tokens,
            output_tokens=LlmUsage.output_tokens + output_tokens,
        )
    )
