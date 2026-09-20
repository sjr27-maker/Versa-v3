FROM python:3.12-slim
RUN pip install uv
WORKDIR /app
COPY . .
RUN uv sync --frozen
# No entrypoint on purpose: the web UI/server was removed ahead of a
# redesign. Set CMD (and EXPOSE/PORT) once the new server exists.
