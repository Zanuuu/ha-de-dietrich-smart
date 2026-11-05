#!/usr/bin/env bash
# Captures du panneau dans un conteneur Playwright sur un hôte Docker ; les PNG arrivent dans $1.
set -euo pipefail
cd "$(dirname "$0")/.."
# Hôte Docker : CI_HOST dans l'environnement ou dans scripts/local.conf (ignoré par Git).
[[ -f scripts/local.conf ]] && source scripts/local.conf
HOST="${CI_HOST:?définir CI_HOST (hôte Docker en SSH), par exemple dans scripts/local.conf}"
OUT="${1:?usage: scripts/apercu.sh <dossier de sortie>}"
mkdir -p "$OUT"
IMAGE="mcr.microsoft.com/playwright/python:v1.55.0-noble"
git ls-files -co --exclude-standard -z | tar --null -T - -c \
  | ssh -o BatchMode=yes "$HOST" docker run --rm -i "$IMAGE" sh -c "'mkdir /w && cd /w && tar x \
      && (python3 -m http.server 8000 >/dev/null 2>&1 &) && sleep 1 \
      && pip install -q playwright==1.55.0 >&2 && python3 scripts/apercu.py >&2; rc=\$?; tar -C /w/out -c . ; exit \$rc'" \
  | tar -C "$OUT" -x
