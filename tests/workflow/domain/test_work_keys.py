"""업무 키 주소 — phase 16 step 5 (ARCHITECTURE "업무 화면 — phase 16" 이름 표 `domain/work_keys.py`)."""

from workflow.domain.work_keys import work_path


def test_work_path_opens_the_panel_on_the_work_screen():
    assert work_path("RUN-12") == "/tasks?open=RUN-12"
