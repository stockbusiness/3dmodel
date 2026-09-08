# 仕様第3章：web と worker を同じイメージから起動する
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /srv/app

# uv は PyPI から版を固定して入れる
RUN pip install --no-cache-dir "uv==0.8.17"

# 依存はロックファイルどおりに固定して入れる
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY app ./app
COPY migrations ./migrations
COPY fixtures ./fixtures
COPY alembic.ini VERSION ./

# 保存物とDBは公開ディレクトリ外のボリュームに置く（仕様第12章）
RUN mkdir -p /srv/data && \
    useradd --system --uid 10001 --home /srv/app app && \
    chown -R app:app /srv/app /srv/data
USER app

ENV APP_DATA_DIR=/srv/data

EXPOSE 8000

# 既定は web。worker は compose で command を差し替える
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
