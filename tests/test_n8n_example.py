"""docs/n8n/ — import 가능한 n8n 워크플로우 JSON 과 절차서 (phase 7 step 7). Docker·n8n 없이 파일 내용만 검사한다.

검사 기준: ADR-0010 의 노드 4개(Webhook → HTTP Request → Wait → Slack), CONTRACT 12절(입구 경로·본문 필드·`ChainCallback` 필드),
github fixture 의 라벨 값, 비밀값 없음(test_deploy_files 와 같은 접두사 규칙), GLOSSARY 금지 표현·n8n 비판 문구 없음.
노드 파라미터 이름·option 값은 n8n 저장소(packages/nodes-base)에서 2026-09-22 확인한 것이다.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
N8N_DIR = ROOT / "docs" / "n8n"
WORKFLOW_JSON = N8N_DIR / "runloom-handoff.json"
README = N8N_DIR / "README.md"
DOCS_README = ROOT / "docs" / "README.md"
GITHUB_FIXTURE = ROOT / "src" / "workflow" / "adapters" / "task_source_fixtures" / "github.json"

NODE_TYPES = ("n8n-nodes-base.webhook", "n8n-nodes-base.httpRequest", "n8n-nodes-base.wait", "n8n-nodes-base.slack")
# n8n 저장소 master(2026-09-22)의 version 배열 — 예시 파일의 typeVersion 은 이 안에 있어야 import 된다
KNOWN_TYPE_VERSIONS = {
    "n8n-nodes-base.webhook": (1, 1.1, 2, 2.1),
    "n8n-nodes-base.httpRequest": (1, 2, 3, 4, 4.1, 4.2, 4.3, 4.4, 4.5),
    "n8n-nodes-base.wait": (1, 1.1),
    "n8n-nodes-base.slack": (1, 2, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7),
}
SECRET_PATTERNS = (r"\bwfs_[A-Za-z0-9_-]{20,}", r"\bwfc_[A-Za-z0-9_-]{8,}", r"\bsk-[A-Za-z0-9_-]{8,}")
# GLOSSARY 금지 표현 + n8n 비판·대체 표현 + 제품 흐름을 그리는 단어 (ADR-0009·0010)
FORBIDDEN_PHRASES = ("webhook secret", "api key", "inbound token", "whitelist", "대체", "graph", "dag", "pipeline")


@pytest.fixture(scope="module")
def workflow() -> dict:
    return json.loads(WORKFLOW_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def nodes(workflow) -> list[dict]:
    return workflow["nodes"]


@pytest.fixture(scope="module")
def readme() -> str:
    return README.read_text(encoding="utf-8")


# --- 워크플로우 JSON ---------------------------------------------------------------------------


def test_workflow_has_four_nodes_in_adr_order_with_importable_shape(workflow):
    assert workflow["name"] == "Runloom handoff"
    assert workflow["settings"] == {"executionOrder": "v1"}
    assert [n["type"] for n in workflow["nodes"]] == list(NODE_TYPES)
    assert len({n["name"] for n in workflow["nodes"]}) == 4
    for node in workflow["nodes"]:
        assert re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", node["id"]), node["name"]
        assert node["typeVersion"] in KNOWN_TYPE_VERSIONS[node["type"]], (node["name"], node["typeVersion"])
        assert isinstance(node["position"], list) and len(node["position"]) == 2
        assert isinstance(node["parameters"], dict)


def test_connections_link_1_to_2_to_3_to_4_only(workflow):
    names = [n["name"] for n in workflow["nodes"]]
    connections = workflow["connections"]
    for src, dst in zip(names, names[1:]):
        assert connections[src] == {"main": [[{"node": dst, "type": "main", "index": 0}]]}, src
    assert names[-1] not in connections
    assert set(connections) == set(names[:-1])


def test_webhook_trigger_is_post_runloom_demo_responding_immediately(nodes):
    node = nodes[0]
    assert node["parameters"]["httpMethod"] == "POST"
    assert node["parameters"]["path"] == "runloom-demo"
    assert node["parameters"]["responseMode"] == "onReceived"
    assert node["webhookId"]


def test_http_request_posts_inbound_chain_request_with_header_auth_credential(nodes):
    node = nodes[1]
    params = node["parameters"]
    assert params["method"] == "POST"
    assert params["url"].endswith("/sources/n8n/chains")
    assert params["url"].startswith("http://host.docker.internal:")
    assert params["authentication"] == "genericCredentialType"
    assert params["genericAuthType"] == "httpHeaderAuth"
    assert params["sendBody"] is True and params["contentType"] == "json" and params["specifyBody"] == "json"
    body = params["jsonBody"]
    assert body.startswith("={{") and body.rstrip().endswith("}}")  # n8n 표현식
    for needle in (
        "JSON.stringify", "contract_version: 1", "callback_url: $execution.resumeUrl", "items:",
        "$json.body.run_id", "blocked_by", '"incident"', '"workflow:daily-report"', '"run:" +',
        '"bug"', '"repo:demo-report-repo"',
    ):
        assert needle in body, needle
    # 자격 증명은 참조만 — 헤더 값(Bearer wfs_…)은 n8n 의 자격 증명 저장소에 있고 JSON 엔 없다
    assert set(node["credentials"]) == {"httpHeaderAuth"}
    assert set(node["credentials"]["httpHeaderAuth"]) == {"id", "name"}
    assert node["credentials"]["httpHeaderAuth"]["name"] == "Runloom source token"
    assert "Bearer" not in json.dumps(node, ensure_ascii=False)


def test_http_request_labels_are_the_github_fixture_labels(nodes):
    """항목 2개의 라벨은 fixture #41·#42 와 같다 — 그래서 매핑·구성 규칙이 가져오기와 같은 결과를 낸다.
    `run:<run_id>` 만 Webhook 본문에서 오므로 접두사로 본다."""
    fixture = {issue["key"]: issue for issue in json.loads(GITHUB_FIXTURE.read_text(encoding="utf-8"))}
    body = nodes[1]["parameters"]["jsonBody"]
    for key in ("#41", "#42"):
        for label in fixture[key]["labels"]:
            needle = '"run:" +' if label.startswith("run:") else f'"{label}"'
            assert needle in body, (key, label)
    assert '"kind:' not in body and "scope" not in body  # 입구 계약은 라벨 규칙 하나


def test_wait_resumes_on_webhook_post_without_time_limit(nodes):
    node = nodes[2]
    assert node["parameters"]["resume"] == "webhook"
    assert node["parameters"]["httpMethod"] == "POST"
    assert node["parameters"].get("limitWaitTime", False) is False
    assert node["webhookId"]


def test_slack_is_disabled_and_reads_chain_callback_fields(nodes):
    node = nodes[3]
    assert node["disabled"] is True
    params = node["parameters"]
    assert params["resource"] == "message" and params["operation"] == "post"
    assert params["select"] == "channel"
    assert params["channelId"]["value"] == "#runloom"
    text = params["text"]
    assert text.startswith("=")
    for needle in (
        "{{ $json.body.title }}", "{{ $json.body.human_gate.status_label }}",
        "{{ $json.body.human_gate.reason }}", "{{ $json.body.chain_url }}",
    ):
        assert needle in text, needle
    assert "credentials" not in node


@pytest.mark.parametrize("path", [WORKFLOW_JSON, README], ids=["json", "readme"])
def test_example_files_hold_no_secret_looking_values(path):
    text = path.read_text(encoding="utf-8")
    for pattern in SECRET_PATTERNS:
        assert not re.search(pattern, text), (path.name, pattern)
    assert "OPENAI_API_KEY=" not in text


# --- README ----------------------------------------------------------------------------------


def test_readme_has_the_seven_sections_in_order(readme):
    headings = [
        "## 1. 무엇을 하는가", "## 2. 준비", "## 3. Runloom 에서", "## 4. n8n 에서",
        "## 5. 실행", "## 6. 동작 규칙", "## 7. 한계",
    ]
    positions = [readme.index(h) for h in headings]
    assert positions == sorted(positions)


def test_readme_uses_the_same_commands_paths_and_settings_as_the_product(readme):
    for needle in (
        "docker run -d --name runloom-n8n -p 5678:5678 -v runloom_n8n_data:/home/node/.n8n docker.n8n.io/n8nio/n8n",
        "runloom-handoff.json",
        "/sources",
        "/agents/register",
        "WORKFLOW_CALLBACK_HOSTS=localhost:5678",
        "WORKFLOW_PUBLIC_URL=http://127.0.0.1:8000",
        "scripts/local_stack.py --scripted --callback-hosts localhost:5678 --public-url http://127.0.0.1:18000",
        "http://host.docker.internal:8000/sources/n8n/chains",
        "curl -X POST http://localhost:5678/webhook/runloom-demo",
        '{"run_id":"daily-0920-0900"}',
        "$execution.resumeUrl",
        "import:workflow --input=",
        "import:credentials --input=",
        "publish:workflow --id=",
        "update:workflow --id=",
        "httpHeaderAuth",
        "Limit Wait Time",
        "--add-host",
        "0010-n8n-inbox-and-callback.md",
        "CONTRACT.md",
        "DEPLOY.md",
    ):
        assert needle in readme, needle
    # 포트 표 — 로컬 스택 18000 / 개발 서버 8000 (HTTP Request 노드 URL 을 맞춘다)
    assert "18000" in readme and "8000" in readme


def test_readme_states_the_callback_rules_and_public_demo_limit(readme):
    for needle in ("체인당 1회", "사람 차례", "started", "start_error", "허용 목록", "runloom.duckdns.org", "Error Trigger"):
        assert needle in readme, needle
    # 공개 데모 VM 은 허용 목록이 비어 callback 이 오지 않는다 — 거기서 따라 하라고 적지 않는다
    vm_line = next(line for line in readme.splitlines() if "runloom.duckdns.org" in line)
    assert "비어" in vm_line and "오지 않" in vm_line


def test_readme_avoids_forbidden_phrases_and_n8n_criticism(readme):
    lowered = readme.lower()
    for phrase in FORBIDDEN_PHRASES:
        assert not re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", lowered), phrase


def test_docs_readme_lists_the_n8n_directory():
    text = DOCS_README.read_text(encoding="utf-8")
    assert "[n8n/](n8n/README.md)" in text
    assert text.count("n8n/") >= 1
