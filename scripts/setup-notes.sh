#!/bin/sh
# README "Set up Cairn for a project" in one command: create a project's shared notes and start the daily job.
#
#   ~/cairn/scripts/setup-notes.sh <you>/<notes-repo> [--project DIR] [--profile NAME|DIR] [--url GIT_URL]
#
#   <you>/<notes-repo>  the private GitHub repo for the notes, e.g. alice/widget-notes. Created if missing,
#                       when the GitHub CLI (gh) is installed and signed in.
#   --project DIR       your clone of the project (default: the current directory)
#   --profile NAME|DIR  a profile in this kit, such as example, or a profile directory
#   --url GIT_URL       the notes repo's clone address, if not https://github.com/<you>/<notes-repo>.git
#
# Safe to run again: every step checks whether it is already done.
set -eu
KIT=$(cd "$(dirname "$0")/.." && pwd)
CAIRN="python3 $KIT/cli/cairn.py"
SLUG="" PROJECT=$(pwd) PROFILE="" URL=""
die() { printf '\nerror: %s\n' "$*" >&2; exit 1; }
step() { printf '\n== %s\n' "$*"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --project) PROJECT=$2; shift 2 ;;
    --profile) PROFILE=$2; shift 2 ;;
    --url) URL=$2; shift 2 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    -*) die "unknown option $1" ;;
    *) SLUG=$1; shift ;;
  esac
done
case "$SLUG" in */*) ;; *) sed -n '2,13p' "$0"; exit 2 ;; esac
ON_GITHUB=0; [ -z "$URL" ] && ON_GITHUB=1
URL=${URL:-https://github.com/$SLUG.git}
HAVE_GH=0
if [ $ON_GITHUB = 1 ] && command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then HAVE_GH=1; fi
PROJECT=$(cd "$PROJECT" && pwd)
git -C "$PROJECT" rev-parse --git-dir >/dev/null 2>&1 || die "$PROJECT is not a git clone of the project (use --project)"
case "$PROFILE" in
  "") ;;
  */*) [ -d "$PROFILE" ] || die "no profile directory $PROFILE"; PROFILE=$(cd "$PROFILE" && pwd) ;;
  *) [ -d "$KIT/profiles/$PROFILE" ] || die "no profile '$PROFILE' (this kit has: $(ls "$KIT/profiles" | tr '\n' ' '))"
     PROFILE="$KIT/profiles/$PROFILE" ;;
esac
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT

# The remote that points at the official project: one whose address ends in code_repo, else `upstream`, added.
main_remote() {
  WANT=${CAIRN_UPSTREAM_URL:-https://github.com/$1.git}
  for r in $(git -C "$PROJECT" remote); do
    case "$(git -C "$PROJECT" remote get-url "$r")" in
      *"/$1"|*"/$1.git"|*":$1"|*":$1.git"|"$WANT") echo "$r"; return ;;
    esac
  done
  git -C "$PROJECT" remote add upstream "$WANT" 2>/dev/null || git -C "$PROJECT" remote set-url upstream "$WANT"
  echo upstream
}

# The copied branches bring the project's own GitHub Actions workflows with them, and pushing the branches starts
# them in the notes repo. Stop any that are running and disable every workflow except the daily job. Only ever
# called for a companion repo, never for the project's own.
JOB_PATH=.github/workflows/cairn-daily.yml
project_workflows() {  # ids of the notes repo's active workflows, other than the daily job
  gh workflow list -R "$SLUG" --all --json id,path,state \
    --jq ".[] | select(.path != \"$JOB_PATH\" and .state == \"active\") | .id" 2>/dev/null || true
}
quiet_project_workflows() {  # $1: how many times to look, 5 seconds apart (GitHub lists a workflow a little after the push)
  tries=$1; off=0; refused=" "
  while [ "$tries" -gt 0 ]; do
    tries=$((tries - 1))
    ids=""
    for id in $(project_workflows); do
      case "$refused" in *" $id "*) ;; *) ids="$ids $id" ;; esac
    done
    if [ -n "$ids" ]; then
      for id in $ids; do
        if gh workflow disable "$id" -R "$SLUG" >/dev/null 2>&1; then off=$((off + 1))
        else refused="$refused$id "; echo "warning: couldn't disable workflow $id in $SLUG: disable it in the repo's Actions tab"; fi
      done
      for run in $(gh run list -R "$SLUG" --limit 100 --json databaseId,status,workflowName \
          --jq '.[] | select(.status != "completed" and .workflowName != "cairn-daily") | .databaseId' 2>/dev/null || true); do
        gh run cancel "$run" -R "$SLUG" >/dev/null 2>&1 || true
      done
    elif [ "$off" -gt 0 ] || [ "$refused" != " " ]; then break
    fi
    [ "$tries" -gt 0 ] && sleep 5
  done
  [ "$off" -eq 0 ] || echo "disabled $off of the project's own workflows in $SLUG and cancelled their runs"
}

step "1/7 Install Cairn"
"$KIT/install.sh"

step "2/7 The private notes repo, $SLUG"
if git ls-remote "$URL" >/dev/null 2>&1; then
  echo "found $URL"
elif [ $HAVE_GH = 1 ]; then
  gh repo create "$SLUG" --private --description "Shared notes for coding agents (Cairn)"
else
  die "can't reach $URL. Create it as a private repo first, or install and sign in to the GitHub CLI (gh)."
fi

# The daily job gets a branch of its own, cairn/job, which holds that one file and is the notes repo's default
# branch (GitHub runs scheduled jobs only from the default branch). A name of its own leaves every other name,
# `main` included, free for the copies of the project's branches.
JOB_BRANCH=cairn/job
step "3/7 The daily job, on the notes repo's $JOB_BRANCH branch"
KITREPO=$(sed -n "s/.*vars\.CAIRN_KIT || '\([^']*\)'.*/\1/p" "$KIT/ci/cairn-daily.yml" | head -1)
if [ "$KITREPO" = "OWNER/cairn" ]; then
  KITREPO=$(git -C "$KIT" remote get-url origin 2>/dev/null | sed -E 's#^(https://github\.com/|git@github\.com:)##; s#\.git$##' || true)
  echo "$KITREPO" | grep -Eq '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$' \
    || die "the job doesn't know where the kit lives on GitHub. In the kit, run: scripts/set-kit-repo.sh <owner>/<repo>"
fi
WF=.github/workflows/cairn-daily.yml
git clone -q "$URL" "$TMP/notes" 2>/dev/null || die "can't clone $URL (check your access)"
default_branch() { git ls-remote --symref "$URL" HEAD 2>/dev/null | sed -n 's#^ref: refs/heads/\(.*\)[[:space:]]HEAD$#\1#p' | head -1; }
DEFAULT=$(default_branch)
if git -C "$TMP/notes" cat-file -e "origin/$JOB_BRANCH:$WF" 2>/dev/null; then
  echo "already there"
elif [ -n "$DEFAULT" ] && git -C "$TMP/notes" cat-file -e "origin/$DEFAULT:$WF" 2>/dev/null; then
  JOB_BRANCH=$DEFAULT   # a notes repo set up before the job had a branch of its own
  echo "already there (on $JOB_BRANCH)"
else
  (
    cd "$TMP/notes"
    if git rev-parse -q --verify "origin/$JOB_BRANCH" >/dev/null; then
      git checkout -q -B "$JOB_BRANCH" "origin/$JOB_BRANCH"
    else  # a new branch with no history and no files, whatever the clone checked out
      git symbolic-ref HEAD "refs/heads/$JOB_BRANCH"
      git read-tree --empty
      git clean -fdxq
    fi
    mkdir -p .github/workflows
    sed "s#vars\.CAIRN_KIT || '[^']*'#vars.CAIRN_KIT || '$KITREPO'#" "$KIT/ci/cairn-daily.yml" > "$WF"
    git add "$WF" && git commit -q -m "Add the Cairn daily job" && git push -q origin "$JOB_BRANCH"
  )
  echo "added $WF (downloads Cairn from $KITREPO)"
fi
if [ "$(default_branch)" != "$JOB_BRANCH" ]; then
  if [ $HAVE_GH = 1 ]; then gh repo edit "$SLUG" --default-branch "$JOB_BRANCH" && echo "made $JOB_BRANCH the default branch"
  else echo "warning: make $JOB_BRANCH the notes repo's default branch: GitHub runs daily jobs only from the default branch"; fi
fi

step "4/7 The notebook"
git -C "$PROJECT" remote get-url cairn >/dev/null 2>&1 && git -C "$PROJECT" remote set-url cairn "$URL" \
  || git -C "$PROJECT" remote add cairn "$URL"
git -C "$PROJECT" config cairn.remote cairn
if git ls-remote --exit-code "$URL" refs/heads/cairn/ledger >/dev/null 2>&1; then
  echo "already created"
elif [ -n "$PROFILE" ]; then
  (cd "$PROJECT" && $CAIRN init --profile "$PROFILE")
else
  (cd "$PROJECT" && $CAIRN init)
fi
(cd "$PROJECT" && $CAIRN map check) || true

step "5/7 Copies of the project's branches, for the daily job"
SETTINGS=$(cd "$PROJECT" && $CAIRN settings --json)
get() { echo "$SETTINGS" | python3 -c "import json,sys; print(json.load(sys.stdin)['effective'].get('$1') or '')"; }
CODE_REPO=$(get code_repo)
if [ -z "$CODE_REPO" ]; then
  echo "skipped: no code_repo in the settings, so the notes are kept beside the code itself"
elif [ "$CODE_REPO" = "$SLUG" ]; then
  echo "skipped: the notes live in the project's own repo"
else
  for b in $(get main_branch) $(get release_branch); do
    [ "$b" != "$JOB_BRANCH" ] || die "this notes repo keeps its daily job on \`$b\`, which is also the name of one of $CODE_REPO's branches, so the copy of that branch has nowhere to go. Rename the notes repo's \`$b\` branch to cairn/job (on GitHub: the repo's Branches page, or \`gh api -X POST repos/$SLUG/branches/$b/rename -f new_name=cairn/job\`), then run this again."
  done
  MR=$(main_remote "$CODE_REPO")
  [ "$MR" = "$(get main_remote)" ] || git -C "$PROJECT" config cairn.mainRemote "$MR"
  for b in $(get main_branch) $(get release_branch); do
    git -C "$PROJECT" fetch -q "$MR" "+refs/heads/$b:refs/remotes/$MR/$b"
    if git -C "$PROJECT" push -q cairn "refs/remotes/$MR/$b:refs/heads/$b" 2>/dev/null; then echo "copied $b from $CODE_REPO"
    else echo "warning: couldn't update $b in the notes repo (the daily job keeps it in step from now on)"; fi
  done
  HAS_WF=0
  for b in $(get main_branch) $(get release_branch); do
    [ -z "$(git -C "$PROJECT" ls-tree -r --name-only "refs/remotes/$MR/$b" .github/workflows 2>/dev/null)" ] || HAS_WF=1
  done
  if [ $HAS_WF = 1 ] && [ $HAVE_GH = 1 ]; then
    quiet_project_workflows 6
  elif [ $HAS_WF = 1 ]; then
    echo "warning: $CODE_REPO has its own GitHub Actions workflows, and the copied branches start them in the notes"
    echo "repo. In its Actions tab, cancel their runs and disable every workflow except cairn-daily."
  fi
fi

step "6/7 Protect the notes from being erased"
RULESET='{"name":"cairn ledger","target":"branch","enforcement":"active","conditions":{"ref_name":{"include":["refs/heads/cairn/ledger"],"exclude":[]}},"rules":[{"type":"deletion"},{"type":"non_fast_forward"}]}'
if [ $HAVE_GH = 0 ]; then
  echo "do it by hand, on a paid GitHub plan: Settings > Rules > Rulesets, a branch ruleset for cairn/ledger"
  echo "that restricts deletions and blocks force pushes"
elif gh api "repos/$SLUG/rulesets" --jq '.[].name' 2>/dev/null | grep -qx "cairn ledger"; then
  echo "already protected"
elif echo "$RULESET" | gh api -X POST "repos/$SLUG/rulesets" --input - >/dev/null 2>&1; then
  echo "protected cairn/ledger: no deletion, no force pushes"
else
  echo "skipped: GitHub offers this for private repos on paid plans only. The daily job still reports"
  echo "edited or deleted notes, and every clone holds a copy to restore from."
fi

step "7/7 First run of the daily job"
if [ $HAVE_GH = 1 ]; then
  STARTED=0
  for i in 1 2 3 4 5 6; do
    if gh workflow run cairn-daily.yml -R "$SLUG" >/dev/null 2>&1; then STARTED=1; break; fi
    sleep 5
  done
  if [ $STARTED = 1 ]; then echo "started; follow it with: gh run watch -R $SLUG"
  else echo "couldn't start it yet: open the repo's Actions tab, choose cairn-daily, then Run workflow"; fi
else
  echo "open the notes repo's Actions tab, choose cairn-daily, then Run workflow"
fi

# A second look: a workflow GitHub listed only after step 5 is caught here.
if [ $HAVE_GH = 1 ] && [ -n "$CODE_REPO" ] && [ "$CODE_REPO" != "$SLUG" ]; then quiet_project_workflows 1; fi

KITURL=$(git -C "$KIT" remote get-url origin 2>/dev/null || echo "https://github.com/$KITREPO.git")
printf '\nDone. Each teammate now runs (naming the assistants they use):\n'
printf '  git clone %s ~/cairn && ~/cairn/scripts/join.sh %s claude-code\n' "$KITURL" "$SLUG"
