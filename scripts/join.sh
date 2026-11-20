#!/bin/sh
# README "Join a project that uses Cairn" in one command: connect your clone of the project to the shared notes.
#
#   ~/cairn/scripts/join.sh <you>/<notes-repo> [--project DIR] [--user LOGIN] [--url GIT_URL] [ASSISTANT ...]
#
#   ASSISTANT       any of claude-code, codex, opencode, pi: also adds Cairn to those assistants
#   --project DIR   your clone of the project (default: the current directory)
#   --user LOGIN    your GitHub login (default: from the GitHub CLI, gh, if you are signed in)
#   --url GIT_URL   the notes repo's clone address, if not https://github.com/<you>/<notes-repo>.git
#
# Safe to run again.
set -eu
KIT=$(cd "$(dirname "$0")/.." && pwd)
CAIRN="python3 $KIT/cli/cairn.py"
SLUG="" PROJECT=$(pwd) LOGIN="" URL="" HARNESSES=""
die() { printf '\nerror: %s\n' "$*" >&2; exit 1; }
step() { printf '\n== %s\n' "$*"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --project) PROJECT=$2; shift 2 ;;
    --user) LOGIN=$2; shift 2 ;;
    --url) URL=$2; shift 2 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    -*) die "unknown option $1" ;;
    */*) SLUG=$1; shift ;;
    *) HARNESSES="$HARNESSES $1"; shift ;;
  esac
done
[ -n "$SLUG" ] || { sed -n '2,11p' "$0"; exit 2; }
URL=${URL:-https://github.com/$SLUG.git}
PROJECT=$(cd "$PROJECT" && pwd)
git -C "$PROJECT" rev-parse --git-dir >/dev/null 2>&1 || die "$PROJECT is not a git clone of the project (use --project)"

step "1/4 Install Cairn"
# shellcheck disable=SC2086
"$KIT/install.sh" $HARNESSES

step "2/4 Connect $PROJECT to the notes"
git ls-remote --exit-code "$URL" refs/heads/cairn/ledger >/dev/null 2>&1 \
  || die "found no notes at $URL. Check that you have access, and that setup-notes.sh has been run for it."
git -C "$PROJECT" remote get-url cairn >/dev/null 2>&1 && git -C "$PROJECT" remote set-url cairn "$URL" \
  || git -C "$PROJECT" remote add cairn "$URL"
git -C "$PROJECT" config cairn.remote cairn
echo "connected"

step "3/4 Who you are"
if [ -z "$LOGIN" ] && command -v gh >/dev/null 2>&1; then LOGIN=$(gh api user --jq .login 2>/dev/null || true); fi
[ -n "$LOGIN" ] || LOGIN=$(git -C "$PROJECT" config cairn.user || true)
[ -n "$LOGIN" ] || die "tell Cairn your GitHub login: run this again with --user <your-github-login>"
(cd "$PROJECT" && $CAIRN whoami --user "$LOGIN" >/dev/null)
echo "you are $LOGIN"

step "4/4 The official project"
SETTINGS=$(cd "$PROJECT" && $CAIRN settings --json)
get() { echo "$SETTINGS" | python3 -c "import json,sys; print(json.load(sys.stdin)['effective'].get('$1') or '')"; }
CODE_REPO=$(get code_repo)
if [ -n "$CODE_REPO" ] && [ "$CODE_REPO" != "$SLUG" ]; then
  WANT=${CAIRN_UPSTREAM_URL:-https://github.com/$CODE_REPO.git}
  MR=""
  for r in $(git -C "$PROJECT" remote); do
    case "$(git -C "$PROJECT" remote get-url "$r")" in
      *"/$CODE_REPO"|*"/$CODE_REPO.git"|*":$CODE_REPO"|*":$CODE_REPO.git"|"$WANT") MR=$r; break ;;
    esac
  done
  if [ -z "$MR" ]; then
    MR=upstream
    git -C "$PROJECT" remote add upstream "$WANT" 2>/dev/null || git -C "$PROJECT" remote set-url upstream "$WANT"
    echo "added remote upstream for $CODE_REPO"
  fi
  # Compare with the ledger's own setting, not the effective one: that already includes this clone's cairn.mainRemote.
  SHARED_MR=$(echo "$SETTINGS" | python3 -c "import json,sys; print(json.load(sys.stdin)['ledger'].get('main_remote') or 'origin')")
  if [ "$MR" != "$SHARED_MR" ]; then git -C "$PROJECT" config cairn.mainRemote "$MR"; else git -C "$PROJECT" config --unset cairn.mainRemote 2>/dev/null || true; fi
  git -C "$PROJECT" fetch -q "$MR" "+refs/heads/$(get main_branch):refs/remotes/$MR/$(get main_branch)"
  echo "comparing notes against $CODE_REPO ($MR/$(get main_branch))"
else
  echo "the notes live beside the code itself"
fi

printf '\n'
(cd "$PROJECT" && $CAIRN brief) | head -2
printf '\nDone. Start your assistant in %s; its first context should include a [cairn] orientation.\n' "$PROJECT"
[ -n "$HARNESSES" ] || printf 'To add Cairn to your assistant, run this again with its name: claude-code, codex, opencode or pi.\n'
case "$HARNESSES" in *claude*) command -v claude >/dev/null 2>&1 || printf 'Claude Code: run the two claude plugin commands printed in step 1.\n' ;; esac
