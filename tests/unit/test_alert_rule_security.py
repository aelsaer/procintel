import pytest
from fastapi import HTTPException

from apps.api.routers.alert_rules import (
    AlertRuleCreateRequest,
    AlertTargetRequest,
    _validate,
)


async def test_alert_rule_rejects_private_webhook_target():
    body = AlertRuleCreateRequest(
        name="Private webhook",
        event_types=["opportunity.created"],
        delivery_channels=["WEBHOOK"],
        targets=[
            AlertTargetRequest(
                channel_type="WEBHOOK",
                target="http://127.0.0.1/internal",
            )
        ],
    )

    with pytest.raises(HTTPException, match="public HTTP") as error:
        await _validate(body)

    assert error.value.status_code == 422
