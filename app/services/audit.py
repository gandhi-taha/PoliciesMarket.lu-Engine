from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditInference


def record_audit(
    session: Session | None,
    *,
    provider: str,
    model: str,
    prompt_name: str,
    prompt_version: str,
    input_payload: dict[str, Any],
    output_payload: dict[str, Any] | None,
    status: str,
    error_message: str | None,
) -> None:
    if session is None:
        return

    audit = AuditInference(
        provider=provider,
        model=model,
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        input_payload=input_payload,
        output_payload=output_payload,
        status=status,
        error_message=error_message,
    )
    session.add(audit)
    session.commit()
