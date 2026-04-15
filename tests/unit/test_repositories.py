from __future__ import annotations

from app.db.schema import init_db
from app.store.repositories import IdempotencyRepository, OperationRepository


def test_idempotency_reserve_then_duplicate(sqlite_db) -> None:
    init_db()

    idem = IdempotencyRepository()
    ops = OperationRepository()

    first = idem.reserve_or_get_existing(
        scs_as_id="scs1",
        idempotency_key="k1",
        payload_fingerprint="fp1",
        operation_id="op1",
    )
    assert first["reserved"] is True

    ops.create(
        operation_id="op1",
        scs_as_id="scs1",
        idempotency_key="k1",
        payload_fingerprint="fp1",
        status="completed",
    )

    second = idem.reserve_or_get_existing(
        scs_as_id="scs1",
        idempotency_key="k1",
        payload_fingerprint="fp1",
        operation_id="op2",
    )
    assert second["reserved"] is False
    assert second["operation_id"] == "op1"
    assert second["status"] == "completed"
