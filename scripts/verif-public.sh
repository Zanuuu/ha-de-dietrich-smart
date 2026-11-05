#!/usr/bin/env bash
# Le dépôt est publié : ni donnée personnelle ni détail d'infrastructure, dans les fichiers comme dans les commits.
# Usage : scripts/verif-public.sh [history]
#   sans argument : arbre de travail et commits absents de origin/main ;
#   history : toutes les références (contenu, auteurs, messages, tags).
# Termes propres à l'installation : un par ligne, hors du dépôt, dans $PUBLIC_DENYLIST.
set -uo pipefail
cd "$(dirname "$0")/.."
DENY="${PUBLIC_DENYLIST:-${XDG_CONFIG_HOME:-$HOME/.config}/dd-smart/public-denylist}"
IP='\b(10(\.[0-9]{1,3}){3}|192\.168(\.[0-9]{1,3}){2}|172\.(1[6-9]|2[0-9]|3[01])(\.[0-9]{1,3}){2})\b'
EMAIL='[[:alnum:]._%+-]+@[[:alnum:].-]+\.[[:alpha:]]{2,}'
ALLOWED='@example\.(org|com)$|@users\.noreply\.github\.com$|noreply@anthropic\.com$|@2x\.png$'
VENDOR=':(exclude)custom_components/de_dietrich_smart/frontend/lit-all.min.js'
terms=$(mktemp)
trap 'rm -f "$terms"' EXIT
if [[ -f "$DENY" ]]; then
  grep -v '^[[:space:]]*$' "$DENY" > "$terms"
else
  echo "verif-public : $DENY absent, contrôle limité aux adresses IP et aux e-mails" >&2
fi

# Sur l'entrée standard : du texte. Affiche les IP privées, les e-mails non autorisés et les termes interdits.
scan_text() {
  local text
  text=$(cat)
  grep -nE "$IP" <<<"$text"
  grep -noE "$EMAIL" <<<"$text" | grep -vE "$ALLOWED"
  grep -nFi -f "$terms" <<<"$text"
}

# En argument : la référence à fouiller, ou rien pour l'arbre de travail (fichiers suivis et nouveaux).
scan_tree() {
  local opts=(--untracked) ref=()
  [[ $# -gt 0 ]] && opts=() ref=("$1")
  git grep "${opts[@]}" -InE "$IP" "${ref[@]}" -- . "$VENDOR"
  git grep "${opts[@]}" -InoE "$EMAIL" "${ref[@]}" -- . "$VENDOR" | grep -vE "$ALLOWED"
  [[ -s "$terms" ]] && git grep "${opts[@]}" -InFi -f "$terms" "${ref[@]}" -- . "$VENDOR"
}

commit_text() {
  git log "$@" --format='%an <%ae>%n%cn <%ce>%n%B'
}

report=$(
  if [[ "${1:-}" == history ]]; then
    for c in $(git rev-list --all); do scan_tree "$c"; done
    commit_text --all | scan_text
    git for-each-ref refs/tags --format='%(taggername) %(taggeremail:trim)%0a%(contents)' | scan_text
  else
    scan_tree
    if git rev-parse -q --verify origin/main >/dev/null; then
      commit_text origin/main..HEAD | scan_text
    fi
  fi
)
if [[ -n "$report" ]]; then
  echo "verif-public : $(wc -l <<<"$report") occurrence(s) à retirer" >&2
  head -n 40 <<<"$report" | cut -c1-160 >&2
  exit 1
fi
echo "verif-public : rien à signaler"
