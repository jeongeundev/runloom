"""사내 요청의 접수 계약. 실행 명령이나 접근 권한을 포함하지 않는다."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from workflow.contracts.v1 import Identifier, NonEmptyStr


class InternalRequestCreate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    system_id: Identifier
    request_kind: Identifier
    recipient_member_id: NonEmptyStr
    expected_directory_revision: int = Field(ge=1)
    submission_key: str = Field(min_length=1, max_length=128)
    purpose: str = Field(min_length=1, max_length=4000)

    @field_validator('purpose', 'submission_key')
    @classmethod
    def non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('빈 값은 허용되지 않습니다.')
        return value


class InternalRequestAccept(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_revision: int = Field(ge=1)


class InternalInvestigationStart(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_revision: int = Field(ge=1)
    kind: Identifier
    scope_value: NonEmptyStr


class InternalResultReturn(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_revision: int = Field(ge=1)
    execution_id: NonEmptyStr


class InternalRequestReject(InternalRequestAccept):
    reason: str = Field(min_length=1, max_length=4000)

    @field_validator('reason')
    @classmethod
    def non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('사유를 입력하세요.')
        return value


class InternalRequestReroute(InternalRequestAccept):
    expected_directory_revision: int = Field(ge=1)
    recipient_member_id: NonEmptyStr


class InternalInformationAnswer(InternalRequestAccept):
    text: str = Field(min_length=1, max_length=4000)

    @field_validator('text')
    @classmethod
    def non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('질문 또는 답변을 입력하세요.')
        return value


class InternalInformationQuestion(InternalInformationAnswer):
    submission_key: str = Field(min_length=1, max_length=128)

    @field_validator('submission_key')
    @classmethod
    def non_blank_key(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('접수 키를 입력하세요.')
        return value


class InternalJudgmentRequest(InternalRequestAccept):
    submission_key: str = Field(min_length=1, max_length=128)
    execution_id: NonEmptyStr
    issue: str = Field(min_length=1, max_length=4000)

    @field_validator('issue', 'submission_key')
    @classmethod
    def non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError('판단할 쟁점과 접수 키를 입력하세요.')
        return value


class InternalJudgmentResponse(InternalRequestReject):
    decision: Literal['approve', 'reject']
