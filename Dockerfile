FROM python:3.12-slim
RUN pip install uv
WORKDIR /app
COPY . .
RUN uv sync --frozen
# No entrypoint yet: there is no server. Set CMD (and EXPOSE/PORT) once
# one exists.
