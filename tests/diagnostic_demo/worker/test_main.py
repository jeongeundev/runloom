"""worker.__main__ — 모델 팩토리 배선만 검사한다. 워커 루프·OpenAI 는 여기서 돌리지 않는다."""

from diagnostic_demo.settings import load_settings
from diagnostic_demo.worker.__main__ import model_factory
from diagnostic_demo.worker.model import FakeModelClient


def test_fake_factory_passes_turn_seconds_from_settings():
    settings = load_settings({"DIAG_API_TOKEN": "tok", "DIAG_MODEL": "fake", "DIAG_FAKE_TURN_SECONDS": "2.5"})

    client = model_factory(settings)()

    assert isinstance(client, FakeModelClient) and client.turn_seconds == 2.5
    assert len(client.script) > 0


def test_fake_factory_defaults_to_no_delay():
    client = model_factory(load_settings({"DIAG_API_TOKEN": "tok", "DIAG_MODEL": "fake"}))()
    assert client.turn_seconds == 0.0
