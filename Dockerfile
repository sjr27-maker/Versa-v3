# Versa server for Cloud Run: the API (versa serve) plus the web build of the
# app at "/" (for the invite page's browser fallback and laptops -- the demo
# itself is the Android app, which talks to the same URL).
#
#   docker build -t versa .                      # web app built from app/config/firebase.json
#   docker build --build-arg WEB=off -t versa .  # API only
#
# Sign-in is on unless VERSA_AUTH=off, and `versa serve` refuses to listen
# publicly without it (accounts.py). Everything secret comes from the
# environment at run time (Secret Manager on Cloud Run), never the image.

# The Flutter SDK the app is built with, pinned to the version pubspec needs
# (the public image's "stable" can lag behind). scripts/build_apk.ps1 builds
# this stage alone (--target flutter) and reuses it for the Android APK.
ARG FLUTTER_BASE=ghcr.io/cirruslabs/flutter:stable
ARG FLUTTER_VERSION=3.47.5

# ---------------------------------------------------------------- flutter sdk
FROM ${FLUTTER_BASE} AS flutter
ARG FLUTTER_VERSION
RUN root="$(dirname "$(dirname "$(readlink -f "$(command -v flutter)")")")" \
 && git config --global --add safe.directory "$root" \
 && git -C "$root" fetch --depth 1 origin "refs/tags/${FLUTTER_VERSION}:refs/tags/${FLUTTER_VERSION}" \
 && git -C "$root" checkout -q "${FLUTTER_VERSION}" \
 && flutter --version && flutter precache --web --android

# ---------------------------------------------------------------- web app
FROM flutter AS web
ARG WEB=on
WORKDIR /src/app
COPY app/pubspec.yaml app/pubspec.lock ./
RUN if [ "$WEB" = "on" ]; then flutter pub get; fi
COPY app/ ./
# The API is the page's own origin, so VERSA_API isn't needed for the web build.
RUN mkdir -p build/web && if [ "$WEB" = "on" ]; then \
      DEFINES=""; \
      if [ -f config/firebase.json ]; then DEFINES="--dart-define-from-file=config/firebase.json"; fi; \
      flutter build web --release --no-web-resources-cdn $DEFINES; \
    fi

# ---------------------------------------------------------------- server
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
RUN pip install --no-cache-dir uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ ./src/
RUN uv sync --frozen --no-dev
COPY --from=web /src/app/build/web ./app/build/web
# Cloud Run sends traffic to $PORT and runs behind its own proxy.
ENV PORT=8080 \
    FORWARDED_ALLOW_IPS=*
EXPOSE 8080
# Migrations run separately (a Cloud Run job: `versa migrate`), so a new
# revision never races another over the schema. `versa serve` refuses to
# start while any are pending.
CMD ["sh", "-c", "exec uv run --no-sync versa serve --host 0.0.0.0 --port ${PORT}"]
