"""監査ログ（仕様第9章）。秘密情報を含めない。"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent, Operator


def record(
    db: Session,
    *,
    operator: Operator | None,
    target_kind: str,
    target_id: str,
    action: str,
    reason: str = "",
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        operator_id=operator.id if operator else None,
        target_kind=target_kind,
        target_id=target_id,
        action=action,
        reason=reason,
        before_json=json.dumps(before, ensure_ascii=False) if before is not None else None,
        after_json=json.dumps(after, ensure_ascii=False) if after is not None else None,
    )
    db.add(event)
    return event
