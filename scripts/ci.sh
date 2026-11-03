#!/usr/bin/env bash
# Lint et tests dans un conteneur jetable sur hote-docker : le poste n'a ni pip ni Python 3.14.
# Usage : scripts/ci.sh lint|test|all|fix [arguments pytest]
# L'image de test est construite une fois par contenu de requirements_test.txt.
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="${CI_HOST:-ops@hote-docker}"
MODE="${1:-all}"; shift || true
TAG="dd-smart-test:$(sha256sum requirements_test.txt | cut -c1-12)"

if ! ssh -o BatchMode=yes "$HOST" docker image inspect "$TAG" >/dev/null 2>&1; then
  tmp=$(mktemp -d)
  cp requirements_test.txt "$tmp/"
  cat > "$tmp/Dockerfile" <<'DOCKER'
FROM python:3.14-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY requirements_test.txt /tmp/
RUN pip install --root-user-action=ignore --no-cache-dir -r /tmp/requirements_test.txt
DOCKER
  tar -C "$tmp" -c . | ssh -o BatchMode=yes "$HOST" docker build -q -t "$TAG" - >/dev/null
  rm -rf "$tmp"
fi

case "$MODE" in
  lint) cmd="ruff check . && ruff format --check . && mypy custom_components" ;;
  test) cmd="python -m pytest -q -p no:cacheprovider $*" ;;
  all) cmd="ruff check . && ruff format --check . && mypy custom_components && python -m pytest -q -p no:cacheprovider $*" ;;
  fix)
    # Corrige et formate dans le conteneur, puis rapatrie les fichiers Python modifiés.
    git ls-files -co --exclude-standard -z | tar --null -T - -c \
      | ssh -o BatchMode=yes "$HOST" docker run --rm -i "$TAG" sh -c \
        "'mkdir /w && cd /w && tar x && (ruff check --fix -q . || true) >&2 && ruff format -q . >&2 && find . -name \"*.py\" -print0 | tar --null -T - -c'" \
      | tar x
    exit 0 ;;
  *) echo "usage: $0 lint|test|all|fix" >&2; exit 2 ;;
esac

# Envoie l'arbre de travail (fichiers suivis et nouveaux, hors ignorés), pas seulement HEAD.
git ls-files -co --exclude-standard -z | tar --null -T - -c \
  | ssh -o BatchMode=yes "$HOST" docker run --rm -i "$TAG" sh -c "'mkdir /w && cd /w && tar x && $cmd'"
