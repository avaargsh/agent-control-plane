FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY schemas ./schemas

RUN pip install --no-cache-dir .

ENTRYPOINT ["agent-control-plane"]
CMD ["--help"]
