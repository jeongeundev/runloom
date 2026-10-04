import pytest
from pydantic import ValidationError

from workflow.contracts.responsibility import Responsibility


def test_system_identifier_cannot_be_a_local_path():
    with pytest.raises(ValidationError):
        Responsibility(system_id='/tmp/private', request_kind='investigation', recipient_member_id='m',
                       judgment_member_id='m', agent_id=None)
