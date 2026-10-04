"""담당 범위 등록값. 실행 target이나 접근 권한이 아니다."""
from pydantic import BaseModel, ConfigDict, Field
from workflow.contracts.v1 import Identifier, NonEmptyStr


class Responsibility(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    system_id: Identifier
    request_kind: Identifier
    recipient_member_id: NonEmptyStr
    judgment_member_id: NonEmptyStr
    agent_id: NonEmptyStr | None


class ResponsibilitiesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=1)
    entries: list[Responsibility] = Field(max_length=200)


class ResponsibilitySelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    system_id: Identifier
    request_kind: Identifier
    recipient_member_id: NonEmptyStr
    expected_revision: int = Field(ge=1)
