#!/bin/sh
# Follows the setup steps in docs/overview.md (a public project with private notes) one by one. Local bare repos stand in
# for GitHub: the kit's repo (<owner>/cairn), the private notes repo (<org>/widget-notes) and the public project
# (example/widget, as in profiles/example). Prints PASS/FAIL per step.
set -u
KIT=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d "${TMPDIR:-/tmp}/cairn-gs-XXXX") && [ -d "$T" ] || { echo "can't create a temporary workspace" >&2; exit 1; }
export HOME="$T/home" XDG_CONFIG_HOME="$T/home/.config" CAIRN_BIN_DIR="$T/home/.local/bin" PATH="$T/home/.local/bin:$PATH" GIT_TERMINAL_PROMPT=0
mkdir -p "$HOME"
export CAIRN_SESSION_DIR="$T/sessions"   # hook state from earlier runs stays out
git config --global user.name maintainer; git config --global user.email m@example.com; git config --global init.defaultBranch master
FAILS=0
ok() { if eval "$2" >/dev/null 2>&1; then echo "PASS  $1"; else echo "FAIL  $1"; FAILS=$((FAILS+1)); fi; }
cd "$T"

# stand-in for the public project: develop + release, default branch release
git init -q --bare upstream.git
git clone -q upstream.git seed && cd seed
mkdir -p server/sync core/types client/signing
printf 'def sync_queue(): pass\n' > server/sync/queue.py
printf 'class Model: pass\n' > core/types/model.py
printf 'def sign(): pass\n' > client/signing/keys.py
printf '[project]\n' > pyproject.toml; printf '# widget\n' > README.md
git add -A && git commit -qm initial && git push -q origin HEAD:develop HEAD:release && cd ..
git --git-dir=upstream.git symbolic-ref HEAD refs/heads/release
# stand-in for github.com/<owner>/cairn: the kit, published
git init -q --bare kitrepo.git
cp -r "$KIT" kitsrc && rm -rf kitsrc/.git
ok "0. set-kit-repo.sh fills in the kit's address" "kitsrc/scripts/set-kit-repo.sh someone/cairn && grep -q \"'someone/cairn'\" kitsrc/ci/cairn-daily.yml && ! grep -q '<owner>/cairn' kitsrc/README.md"
(cd kitsrc && git init -q && git add -A && git commit -qm "Cairn" && git push -q "$T/kitrepo.git" HEAD:main) \
  && git --git-dir=kitrepo.git symbolic-ref HEAD refs/heads/main
ok "0. the daily job doesn't run in the kit's own repo" "! git --git-dir=kitrepo.git cat-file -e main:.github/workflows/cairn-daily.yml"

# Part 1, step 1: install Cairn from GitHub, create the private notes repo
git clone -q kitrepo.git "$HOME/cairn" && "$HOME/cairn/install.sh" >/dev/null
ok "1. cloning the kit and running install.sh puts cairn on PATH" "command -v cairn"
git init -q --bare companion.git
# step 2: add the daily job (one file) to the notes repo, naming where the kit lives
git clone -q companion.git notes 2>/dev/null && cd notes
mkdir -p .github/workflows
cp "$HOME/cairn/ci/cairn-daily.yml" .github/workflows/
git add -A && git commit -qm "Add the Cairn daily job" && git push -q origin HEAD:cairn/job && cd ..
git --git-dir=companion.git symbolic-ref HEAD refs/heads/cairn/job   # GitHub: the first branch pushed becomes the default
ok "2. the notes repo's cairn/job branch holds only the daily job" \
   "test \"\$(git --git-dir=companion.git ls-tree -r --name-only cairn/job)\" = .github/workflows/cairn-daily.yml"
# step 3: mirror develop and release
git clone -q upstream.git project && cd project
git remote add cairn "$T/companion.git"
git push -q cairn refs/remotes/origin/develop:refs/heads/develop refs/remotes/origin/release:refs/heads/release
ok "3. the notes repo has copies of develop and release" "git --git-dir=$T/companion.git rev-parse develop release"
ok "3. cairn/job stays the default branch (scheduled jobs run from it)" "test \"\$(git --git-dir=$T/companion.git symbolic-ref HEAD)\" = refs/heads/cairn/job"
# step 4: the notebook, from the example profile
git config cairn.remote cairn
ok "4. cairn init --profile example" "cairn init --profile $HOME/cairn/profiles/example"
ok "4. map check: every file is in an area" "cairn map check"
ok "4. settings came from the profile (main branch develop)" "cairn settings --json | grep -q '\"main_branch\": \"develop\"'"
cd ..

# step 6: the daily job, run the way GitHub runs it: the workflow file read from the notes repo's default branch,
# each step in a fresh shell, job env evaluated, and $GITHUB_ENV carried between steps
cat > runjob.py <<'PY'
import os, re, subprocess, sys, tempfile, yaml
wf = yaml.safe_load(sys.stdin.read())
ws, job = sys.argv[1], wf["jobs"]["maintain"]
env = dict(os.environ, GITHUB_WORKSPACE=ws, RUNNER_TEMP=tempfile.mkdtemp(dir=ws))
for k, v in job["env"].items():  # ${{ vars.X || 'default' }} -> default (no repository variables set)
    m = re.search(r"\|\|\s*'([^']*)'", str(v))
    env[k] = m.group(1) if "${{" in str(v) and m else str(v)
genv = os.path.join(ws, "github_env")
for step in job["steps"]:
    open(genv, "w").close()
    script = step["run"].replace("${{ github.token }}", "x").replace("${{ github.repository }}", "org/widget-notes")
    p = subprocess.run(["sh", "-e", "-c", script], cwd=ws, env=dict(env, GITHUB_ENV=genv), capture_output=True, text=True)
    print(f"--- {step['name']}: exit {p.returncode}\n{p.stdout[-800:]}{p.stderr[-800:]}")
    if p.returncode:
        sys.exit(p.returncode)
    for line in open(genv).read().splitlines():
        if "=" in line:
            k, v = line.split("=", 1); env[k] = v
PY
mkdir ws1 ws2
git --git-dir=companion.git show cairn/job:.github/workflows/cairn-daily.yml | \
  CAIRN_REPO_URL="$T/companion.git" CAIRN_UPSTREAM_URL="$T/upstream.git" CAIRN_KIT_URL="$T/kitrepo.git" \
  python3 runjob.py "$T/ws1" > job1.log 2>&1
ok "6. the daily job downloads Cairn from the kit's repo and runs every step" "grep -q 'using Cairn from' job1.log && grep -q 'Maintain the ledger: exit 0' job1.log"
tail -2 job1.log | sed 's/^/      /'
ok "6. it wrote the dashboard on the ledger branch" "git --git-dir=$T/companion.git cat-file -e cairn/ledger:README.md"
sed "s#vars\.CAIRN_KIT || '[^']*'#vars.CAIRN_KIT || 'OWNER/cairn'#" "$KIT/ci/cairn-daily.yml" | \
  CAIRN_REPO_URL="$T/companion.git" python3 runjob.py "$T/ws2" > job2.log 2>&1
ok "6. a job file that doesn't name the kit stops with a clear message" "grep -q 'Set CAIRN_KIT' job2.log"

# Part 2: each person
git clone -q kitrepo.git person-kit && person-kit/install.sh >/dev/null
ok "P1. the kit clones from its own repo and installs" "command -v cairn && test -f person-kit/install.sh"
git clone -q upstream.git person-proj && cd person-proj && git checkout -q develop
git remote add cairn "$T/companion.git" && git config cairn.remote cairn
ok "P2. whoami" "cairn whoami --user dana --agent codex/gpt"
cd ..
if command -v claude >/dev/null; then
  ok "P3. install.sh claude-code installs the Claude Code plugin" "person-kit/install.sh claude-code | grep -q 'installed the Claude Code plugin' && claude plugin list | grep -q 'cairn@cairn'"
  ok "P3. running it again (how people update) still succeeds" "person-kit/install.sh claude-code | grep -q 'installed the Claude Code plugin'"
else
  ok "P3. install.sh claude-code prints the plugin commands" "person-kit/install.sh claude-code | grep -q 'claude plugin install cairn@cairn'"
fi
ok "P3. install.sh codex writes hooks.json and the AGENTS.md section" "person-kit/install.sh codex && test -f $HOME/.codex/hooks.json && grep -q cairn:begin $HOME/.codex/AGENTS.md"
ok "P3. install.sh codex is idempotent" "person-kit/install.sh codex | grep -q 'already has'"
ok "P3. install.sh pi and opencode copy their adapters" "person-kit/install.sh pi opencode && test -f $HOME/.pi/agent/extensions/cairn.ts && test -f $HOME/.config/opencode/plugins/cairn.ts"
cd person-proj
ok "P4. cairn brief" "cairn brief"
ok "P4. cairn guide prints the project's rules" "cairn guide | grep -q security@example.com"
printf '{"title":"Sync queue retries forever on one peer","claim":"sync_queue never drops a peer that keeps failing.","lens":"data-loss","locs":[{"path":"server/sync/queue.py","lines":"1"}],"severity":"low","confidence":"low","evidence":[{"type":"code-reading","detail":"illustrative"}],"facets":{"platform":["linux"]}}' > "$T/gs-f.json"
ok "P4. submit a finding with the profile's lenses and facets" "cairn submit finding $T/gs-f.json"
ok "P4. a session starts with a [cairn] orientation" "echo '{\"session_id\":\"gs\",\"cwd\":\"'\$PWD'\"}' | cairn hook session-start | grep -q '\[cairn\]'"
ok "P4. opening a file in sync injects its findings" "echo '{\"session_id\":\"gs\",\"cwd\":\"'\$PWD'\",\"tool_input\":{\"file_path\":\"'\$PWD'/server/sync/queue.py\"}}' | cairn hook tool | grep -q 'Sync queue retries'"
cd ..
# The same two parts with the scripts: setup-notes.sh (part 1) and join.sh (part 2)
# A new, empty GitHub repo: its default branch becomes the first branch pushed to it.
new_github_repo() {
  git init -q --bare -b main "$1"
  printf '#!/bin/sh\ngit rev-parse -q --verify HEAD >/dev/null 2>&1 && exit 0\nread old new ref && git symbolic-ref HEAD "$ref"\n' > "$1/hooks/post-receive"
  chmod +x "$1/hooks/post-receive"
}
new_github_repo notes2.git
git clone -q upstream.git proj2
CAIRN_UPSTREAM_URL="$T/upstream.git" "$HOME/cairn/scripts/setup-notes.sh" someone/notes2 --url "$T/notes2.git" \
  --project proj2 --profile example > setup2.log 2>&1
ok "S1. setup-notes.sh runs every step" "grep -q '^Done' setup2.log"
ok "S1. the job file names the kit and is alone on cairn/job" \
   "test \"\$(git --git-dir=notes2.git ls-tree -r --name-only cairn/job)\" = .github/workflows/cairn-daily.yml && git --git-dir=notes2.git show cairn/job:.github/workflows/cairn-daily.yml | grep -q \"'someone/cairn'\""
ok "S1. cairn/job is the default branch; develop and release copied; notebook created" \
   "test \"\$(git --git-dir=notes2.git symbolic-ref HEAD)\" = refs/heads/cairn/job && git --git-dir=notes2.git rev-parse develop release cairn/ledger"
CAIRN_UPSTREAM_URL="$T/upstream.git" "$HOME/cairn/scripts/setup-notes.sh" someone/notes2 --url "$T/notes2.git" \
  --project proj2 --profile example > setup3.log 2>&1
ok "S1. running it again changes nothing" "grep -q '^Done' setup3.log && grep -q 'already there' setup3.log && grep -q 'already created' setup3.log && test \$(git --git-dir=notes2.git rev-list --count cairn/job) = 1"
mkdir ws3 && git --git-dir=notes2.git show cairn/job:.github/workflows/cairn-daily.yml | \
  CAIRN_REPO_URL="$T/notes2.git" CAIRN_UPSTREAM_URL="$T/upstream.git" CAIRN_KIT_URL="$T/kitrepo.git" python3 runjob.py "$T/ws3" > job3.log 2>&1
ok "S1. the daily job runs on the scripted setup" "grep -q 'Maintain the ledger: exit 0' job3.log"
mkdir ws3b && git --git-dir=notes2.git show cairn/job:.github/workflows/cairn-daily.yml | \
  CAIRN_REPO_URL="$T/notes2.git" CAIRN_UPSTREAM_URL="$T/upstream.git" CAIRN_KIT_URL="$T/kitrepo.git" python3 runjob.py "$T/ws3b" > job3b.log 2>&1
ok "S1. the job records the Cairn version it ran with, and the next day's run accepts that" \
   "git --git-dir=notes2.git show cairn/ledger:ledger.json | grep -q '\"latest_version\"' && grep -q 'Maintain the ledger: exit 0' job3b.log"
git clone -q upstream.git proj3 && git -C proj3 remote rename origin myfork
CAIRN_UPSTREAM_URL="$T/upstream.git" "$HOME/cairn/scripts/join.sh" someone/notes2 --url "$T/notes2.git" --project proj3 \
  --user erin codex > join.log 2>&1
ok "S2. join.sh connects a clone, sets who you are, installs Codex's hooks" \
   "grep -q '^Done' join.log && test \"\$(git -C proj3 config cairn.user)\" = erin && test -f $HOME/.codex/hooks.json"
ok "S2. a clone whose official remote isn't called origin is found" "test \"\$(git -C proj3 config cairn.mainRemote)\" = myfork"
CAIRN_UPSTREAM_URL="$T/upstream.git" "$HOME/cairn/scripts/join.sh" someone/notes2 --url "$T/notes2.git" --project proj3 \
  --user erin > join2.log 2>&1
ok "S2. joining again keeps that remote" "grep -q '^Done' join2.log && test \"\$(git -C proj3 config cairn.mainRemote)\" = myfork"
ok "S2. the joined clone gets context" "(cd proj3 && echo '{\"session_id\":\"j\",\"cwd\":\"'\$PWD'\"}' | cairn hook session-start | grep -q '\\[cairn\\]')"
ok "S2. join.sh refuses a notes repo that isn't set up" "! $HOME/cairn/scripts/join.sh someone/none --url $T/nothing.git --project proj3 --user erin"

# A project whose own branches are main and release: the job's branch must not get in the way of the copy of main.
git init -q --bare upstream-m.git
git clone -q upstream.git seed-m 2>/dev/null && (cd seed-m && git push -q "$T/upstream-m.git" origin/develop:refs/heads/main origin/release:refs/heads/release)
git --git-dir=upstream-m.git symbolic-ref HEAD refs/heads/main
cp -r "$HOME/cairn/profiles/example" profile-m
sed -i.bak 's/"main_branch": "develop"/"main_branch": "main"/' profile-m/ledger.json && rm profile-m/ledger.json.bak
new_github_repo notes-m.git
git clone -q upstream-m.git proj-m
CAIRN_UPSTREAM_URL="$T/upstream-m.git" "$HOME/cairn/scripts/setup-notes.sh" someone/notes-m --url "$T/notes-m.git" \
  --project proj-m --profile "$T/profile-m" > setup-m.log 2>&1
ok "M1. setup-notes.sh runs every step for a project whose branch is main" "grep -q '^Done' setup-m.log && ! grep -q warning setup-m.log"
ok "M1. the notes repo's main is the project's main, and the job is alone on cairn/job" \
   "test \"\$(git --git-dir=notes-m.git rev-parse main)\" = \"\$(git --git-dir=upstream-m.git rev-parse main)\" && test \"\$(git --git-dir=notes-m.git ls-tree -r --name-only cairn/job)\" = .github/workflows/cairn-daily.yml"
ok "M1. cairn/job is the default branch" "test \"\$(git --git-dir=notes-m.git symbolic-ref HEAD)\" = refs/heads/cairn/job"
mkdir ws-m && git --git-dir=notes-m.git show cairn/job:.github/workflows/cairn-daily.yml | \
  CAIRN_REPO_URL="$T/notes-m.git" CAIRN_UPSTREAM_URL="$T/upstream-m.git" CAIRN_KIT_URL="$T/kitrepo.git" python3 runjob.py "$T/ws-m" > job-m.log 2>&1
ok "M1. the daily job runs there and leaves the project's main alone" \
   "grep -q 'Maintain the ledger: exit 0' job-m.log && test \"\$(git --git-dir=notes-m.git rev-parse main)\" = \"\$(git --git-dir=upstream-m.git rev-parse main)\""

# A notes repo set up before cairn/job existed keeps its daily job on main. It still works for a project with other
# branch names, and is refused with a clear message for a project whose own branch is main.
git init -q --bare -b main notes-old.git
git clone -q notes-old.git old-seed 2>/dev/null && (cd old-seed && mkdir -p .github/workflows && cp "$HOME/cairn/ci/cairn-daily.yml" .github/workflows/ \
  && git add -A && git commit -qm "Add the Cairn daily job" && git push -q origin HEAD:main)
git clone -q upstream.git proj-old
CAIRN_UPSTREAM_URL="$T/upstream.git" "$HOME/cairn/scripts/setup-notes.sh" someone/notes-old --url "$T/notes-old.git" \
  --project proj-old --profile example > setup-old.log 2>&1
ok "O1. a notes repo with the job on main still sets up" \
   "grep -q '^Done' setup-old.log && grep -q 'already there' setup-old.log && ! git --git-dir=notes-old.git rev-parse -q --verify cairn/job"
git init -q --bare -b main notes-old2.git
(cd old-seed && git push -q "$T/notes-old2.git" HEAD:main)
git clone -q upstream-m.git proj-old2
CAIRN_UPSTREAM_URL="$T/upstream-m.git" "$HOME/cairn/scripts/setup-notes.sh" someone/notes-old2 --url "$T/notes-old2.git" \
  --project proj-old2 --profile "$T/profile-m" > setup-old2.log 2>&1
ok "O1. the job on main plus a project branch called main is refused, with what to do" \
   "! grep -q '^Done' setup-old2.log && grep -q 'cairn/job' setup-old2.log && test \"\$(git --git-dir=notes-old2.git ls-tree -r --name-only main)\" = .github/workflows/cairn-daily.yml"

if command -v claude >/dev/null; then ok "Claude Code accepts the kit's repo as a marketplace, and the plugin" "claude plugin validate person-kit && claude plugin validate person-kit/adapters/claude-code"; fi
echo; echo "$FAILS failure(s); workspace $T"
exit $FAILS
