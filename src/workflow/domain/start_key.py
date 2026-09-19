"""start_key — Task 당 실행 중복 방지 키 (ARCHITECTURE "DB 제약과 실행 잠금").

최초 자동 실행은 Task revision 에 연결된 결정적 키를 쓴다. 실패 후 자동 평가가
반복돼도 같은 값이 나오므로 `UNIQUE(task_id, start_key)` 가 중복 실행을 막는다.
직접 실행·명시적 재시도만 요청 ID 로 새 키를 만든다.
"""


def auto_start_key(task_id: str, task_revision: int) -> str:
    return f"auto:{task_id}:r{task_revision}"


def request_start_key(request_id: str) -> str:
    return f"req:{request_id}"
