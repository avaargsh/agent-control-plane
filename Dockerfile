FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY schemas ./schemas

RUN pip install --no-cache-dir . \
    && python -c "from agent_control_plane.validator import schema_directory; assert (schema_directory() / 'evidence-event.schema.json').is_file()"

ENTRYPOINT ["agent-control-plane"]
CMD ["--help"]
