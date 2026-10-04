import pytest
from pydantic import ValidationError


def test_request_rejects_blank_purpose_and_executable_input():
    from workflow.contracts.internal_request import InternalRequestCreate

    body = dict(system_id='billing', request_kind='investigation', recipient_member_id='mem-1',
                expected_directory_revision=1, submission_key='request-1', purpose='조사 요청')
    for invalid in ({**body, 'purpose': '   '}, {**body, 'command': 'run'}, {**body, 'system_id': '/tmp/local'}):
        with pytest.raises(ValidationError):
            InternalRequestCreate.model_validate(invalid)


def test_investigation_contract_rejects_command_and_returned_summary_input():
    from workflow.contracts.internal_request import InternalInvestigationStart, InternalResultReturn

    with pytest.raises(ValidationError):
        InternalInvestigationStart.model_validate(dict(expected_revision=2, kind='investigate', scope_value='billing', command='run'))
    with pytest.raises(ValidationError):
        InternalResultReturn.model_validate(dict(expected_revision=3, execution_id='exec-1', summary='unverified'))
