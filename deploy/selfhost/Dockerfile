# 셀프호스트 중앙 이미지 (ADR-0016). central·worker 가 같은 이미지를 명령만 달리 쓴다 — compose.yaml.
# 빌드 컨텍스트는 저장소 루트(.dockerignore). 비밀값은 넣지 않는다 — .env 는 compose env_file 로 실행 때만 주입한다.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

RUN useradd --create-home --uid 1000 workflow && mkdir /data && chown workflow:workflow /data

COPY pyproject.toml /build/
COPY src /build/src
RUN pip install --no-cache-dir /build && rm -rf /build

USER workflow
WORKDIR /data
EXPOSE 8000
CMD ["uvicorn", "workflow.server.app:app", "--host", "0.0.0.0", "--port", "8000"]
