#!/usr/bin/env python3
"""End-to-end simulation: three people's agents share one repo through Cairn.

A local bare repo stands in for GitHub. Alice, Bob and Carol each have a clone; several agents race for the
same work at the same time, code moves underneath their findings, and prototypes drift. Run:

    python3 tests/test_cairn.py                         # PASS/FAIL per check
    python3 tests/test_cairn.py --transcript out.txt    # also saves sample CLI output
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import time

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAIRN = os.path.join(KIT, "cli", "cairn.py")
RESULTS, TRANSCRIPT = [], []
os.environ["CAIRN_SESSION_DIR"] = tempfile.mkdtemp(prefix="cairn-sessions-")   # hook state from earlier runs stays out

# Keep the developer's own git setup (hooks, signing, URL rewrites, templates) out of the test repos: every git
# process started from here reads this file instead of ~/.gitconfig, and no system config.
_GITCONFIG = os.path.join(tempfile.mkdtemp(prefix="cairn-gitconfig-"), "gitconfig")
with open(_GITCONFIG, "w") as _f:
    _f.write("[user]\n\tname = tester\n\temail = tester@example.com\n[init]\n\tdefaultBranch = main\n")
os.environ.update(GIT_CONFIG_GLOBAL=_GITCONFIG, GIT_CONFIG_SYSTEM=os.devnull)


def sh(cmd, cwd, env=None, input=None):
    e = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    e.update(env or {})
    return subprocess.run(cmd, cwd=cwd, env=e, input=input, capture_output=True, text=True)


def git(cwd, *args):
    p = sh(["git"] + list(args), cwd)
    if p.returncode:
        raise RuntimeError(f"git {args} failed: {p.stderr}")
    return p.stdout.strip()


class Agent:
    def __init__(self, name, repo, session=None, agent="claude-code/opus-5.5", extra=None):
        self.name, self.repo = name, repo
        self.env = {"CAIRN_USER": name, "CAIRN_AGENT": agent, **(extra or {})}
        if session:
            self.env["CAIRN_SESSION"] = session

    def cmd(self, *args, env=None):
        return [sys.executable, CAIRN, "-C", self.repo] + [str(a) for a in args], dict(self.env, **(env or {}))

    def run(self, *args, env=None, input=None, show=False):
        cmd, e = self.cmd(*args, env=env)
        p = sh(cmd, self.repo, e, input if input is not None else "")
        if show:
            TRANSCRIPT.append(f"$ cairn {' '.join(str(a) for a in args)}    # as {self.name}, exit {p.returncode}\n"
                              f"{p.stdout.rstrip()}{(chr(10) + p.stderr.rstrip()) if p.stderr.strip() else ''}\n")
        return p

    def json(self, *args, env=None, input=None):
        p = self.run(*args, "--json", env=env, input=input)
        try:
            return p.returncode, json.loads(p.stdout)
        except ValueError:
            return p.returncode, {"raw": p.stdout, "err": p.stderr}

    def submit(self, kind, payload, *extra, env=None):
        return self.json("submit", kind, "-", *extra, input=json.dumps(payload), env=env)

    def fresh(self, rid):
        return (self.json("show", rid)[1].get("freshness") or {})

    def popen(self, *args, env=None):
        cmd, e = self.cmd(*args, env=env)
        full = dict(os.environ, GIT_TERMINAL_PROMPT="0", **e)
        return subprocess.Popen(cmd, cwd=self.repo, env=full, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  -- {detail}" if detail and not cond else ""))
    return cond


def later(hours):
    t = dt.datetime.now(dt.timezone.utc).replace(microsecond=0) + dt.timedelta(hours=hours)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def kinds(fr):
    return {s["kind"] for s in fr.get("signals", [])}


SESSION_PY = "\n".join(
    [f"# filler line {i}" for i in range(1, 9)]
    + ["import hmac", "import time", "", "SESSIONS = {}", "", "def new_session(user):",
       "    sid = make_id()", "    SESSIONS[sid] = (user, time.time() + 3600)", "    return sid", ""]
    + [f"# filler line {i}" for i in range(19, 41)]
    + ["def verify_token(token, expected):", "    # compare the presented token with the stored one",
       "    if token is None:", "        return False", "    return token == expected", "", "",
       "def expire(sid):"]
    + ["    SESSIONS.pop(sid, None)"] + [f"# tail {i}" for i in range(50, 61)]) + "\n"

CARGO_LOCK = """[[package]]
name = "serde"
version = "1.0.1"

[[package]]
name = "tokio"
version = "1.2.0"
"""

SERVER_PY = "def handle(req):\n    return route(req)\n\ndef login(req):\n    return 200\n"
REPRO_PY = ("import sys\nsrc = open('src/api/server.py').read()\n"
            "sys.exit(1 if 'def login(req):\\n    return 200' in src else 0)\n")


def make_world(tmp):
    remote = os.path.join(tmp, "github.git")
    git(tmp, "init", "--bare", "-q", "-b", "main", remote)
    seed = os.path.join(tmp, "seed")
    git(tmp, "clone", "-q", remote, seed)
    files = {
        "src/auth/session.py": SESSION_PY,
        "src/api/server.py": SERVER_PY,
        "src/api/jobs.py": "from sync.worker import retry\n\ndef enqueue(job):\n    retry(job)\n",
        "src/sync/worker.py": "def run():\n    while True:\n        job = pull()\n        retry(job)\n",
        "docs/guide.md": "# Guide\n\nRotate tokens monthly.\n" + "".join(f"Section {i}: unchanged prose line.\n" for i in range(20)),
        "README.md": "# demo service\n",
        "bench.py": "print('p50_ms 12.0')\n",
        "Cargo.lock": CARGO_LOCK,
        "repro_login.py": REPRO_PY,
    }
    for path, text in files.items():
        full = os.path.join(seed, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(text)
    ident = ["-c", "user.name=seed", "-c", "user.email=seed@example.com"]
    git(seed, *ident, "add", "-A")
    git(seed, *ident, "commit", "-q", "-m", "initial")
    git(seed, "push", "-q", "origin", "main", "main:stable")
    clones = {}
    for who in ("alice", "bob", "carol"):
        d = os.path.join(tmp, who)
        git(tmp, "clone", "-q", remote, d)
        git(d, "config", "user.name", who)
        git(d, "config", "user.email", f"{who}@example.com")
        clones[who] = d
    return remote, clones


def code_change(repo, edits, msg):
    """Carol-style development on main: apply edits {path: fn(text)->text | None to delete}, commit, push."""
    git(repo, "checkout", "-q", "main")
    git(repo, "pull", "-q", "--ff-only")
    for path, fn in edits.items():
        full = os.path.join(repo, path)
        if fn is None:
            git(repo, "rm", "-q", path)
        elif fn == "mv":
            continue
        else:
            old = open(full).read() if os.path.exists(full) else ""
            os.makedirs(os.path.dirname(full) or ".", exist_ok=True)
            open(full, "w").write(fn(old))
            git(repo, "add", path)
    git(repo, "commit", "-q", "-m", msg)
    git(repo, "push", "-q", "origin", "main")
    return git(repo, "rev-parse", "HEAD")


def finding(title, claim, path, lines, symbol, area_lens=("auth", "security"), severity="medium", **kw):
    loc = {"path": path}
    if lines:
        loc["lines"] = lines
    if symbol:
        loc["symbol"] = symbol
    d = {"title": title, "claim": claim, "lens": area_lens[1], "areas": [area_lens[0]], "locs": [loc],
         "severity": severity, "confidence": "high",
         "evidence": [{"type": "test", "detail": f"tests/{area_lens[0]}/test_{symbol or 'x'}.py reproduces it"}]}
    d.update(kw)
    return d


def main():
    tmp = tempfile.mkdtemp(prefix="cairn-sim-")
    remote, c = make_world(tmp)
    alice, bob, carol = Agent("alice", c["alice"]), Agent("bob", c["bob"], agent="codex"), Agent("carol", c["carol"])
    print(f"world: {tmp}\n")

    # 1. bootstrap with the default (generic) vocabulary -----------------------
    p = alice.run("init", show=True)
    check("init creates the ledger branch", p.returncode == 0, p.stderr)
    p = bob.run("init")
    check("second init is refused (exit 2)", p.returncode == 2, p.stdout + p.stderr)
    for section, value in [
        ("area", '{"id":"auth","globs":["src/auth/**"],"description":"Login, sessions, tokens"}'),
        ("area", '{"id":"api","globs":["src/api/**"],"description":"HTTP handlers"}'),
        ("area", '{"id":"sync","globs":["src/sync/**"],"description":"Background sync worker"}'),
        ("term", '{"session token":"Opaque bearer token stored in the sid cookie"}'),
        ("benchmarks", '{"id":"http-latency","command":"python3 bench.py","area":"api",'
                       '"metrics":[{"name":"p50_ms","unit":"ms","better":"lower"}]}'),
    ]:
        p = alice.run("map", "add", section, value)
        check(f"map add {section}", p.returncode == 0, p.stderr)
    check("map rm area src", alice.run("map", "rm", "area", "src").returncode == 0)
    code, w = alice.json("where", "src/auth/session.py", "README.md", "docs/guide.md")
    check("where maps paths to the most specific area",
          w.get("src/auth/session.py", [None])[0] == "auth" and w.get("README.md") == ["root"]
          and w.get("docs/guide.md") == ["docs"], w)
    check("map check: every file is in an area", alice.run("map", "check").returncode == 0)
    for key, val in [("release_branch", '"stable"'), ("repro_runners", '["python3 {path}"]')]:
        p = alice.run("settings", "set", key, val)
        check(f"settings set {key} (stored in the ledger)", p.returncode == 0, p.stderr)
    old_guide = alice.run("guide").stdout
    p = alice.run("guide", "set", "-", input=old_guide.rstrip() + "\n\n## Severity\nRate by effect.\n")
    check("guide set replaces the project guide for everyone", p.returncode == 0
          and "## Severity" in bob.run("guide").stdout and old_guide.strip() in bob.run("guide").stdout, p.stdout + p.stderr)
    check("an empty guide is refused", alice.run("guide", "set", "-", input=" \n").returncode != 0
          and "## Severity" in alice.run("guide").stdout)
    code, st = bob.json("settings")
    check("other clones pick settings up from the ledger", st.get("effective", {}).get("release_branch") == "stable", st)
    alice.run("map", show=True)

    # 2. claim races: 4 agents, same key, same instant -------------------------
    keys = [f"audit/{a}/{l}" for a in ("auth", "api", "sync", "docs") for l in ("correctness", "performance")]
    racers = [alice, bob, carol, Agent("alice", c["alice"], session="s-second-agent")]
    exact_one = True
    for key in keys:
        procs = [r.popen("claim", key, "--intent", f"race for {key}") for r in racers]
        codes = [pr.wait() for pr in procs]
        if sorted(codes) != [0, 2, 2, 2]:
            exact_one = False
            print("   codes", key, codes, [pr.stderr.read() for pr in procs])
    check(f"claim race: exactly one winner of 4 for each of {len(keys)} keys", exact_one)
    code, cl = alice.json("claims")
    check("all raced leases are recorded", len(cl.get("active", [])) == len(keys), cl)
    p = bob.run("sweep", env={"CAIRN_NOW": later(3)})
    check("sweep (3h later) removes expired leases", p.returncode == 0 and str(len(keys)) in p.stdout, p.stdout)

    # 3. concurrent submits all land -----------------------------------------
    payloads = [
        ("alice", finding("Login handler returns 200 for bad passwords", "login() ignores the password check.",
                          "src/api/server.py", "4-5", "login", ("api", "security"), "high",
                          repro={"run": "python3 repro_login.py", "expect": "fail"})),
        ("bob", finding("Sync worker loops forever without backoff", "run() retries in a hot loop with no sleep.",
                        "src/sync/worker.py", "1-4", "run", ("sync", "reliability"))),
        ("carol", finding("Guide recommends monthly rotation but code never rotates", "Docs promise rotation "
                          "that is not implemented.", "docs/guide.md", "3", None, ("docs", "docs"), "low")),
        ("alice", finding("route() result is not validated", "handle() returns route() output unchecked.",
                          "src/api/server.py", "1-2", "handle", ("api", "correctness"), "low")),
        ("bob", finding("retry() swallows job failures", "Failures inside retry are dropped silently.",
                        "src/sync/worker.py", "4", "retry", ("sync", "correctness"))),
        ("carol", finding("README lacks setup steps", "There are no install instructions.", "README.md", "1",
                          None, ("root", "docs"), "info")),
    ]
    agents = {"alice": alice, "bob": bob, "carol": carol}
    procs = []
    for who, pl in payloads:
        path = os.path.join(tmp, f"{who}-{len(procs)}.json")
        with open(path, "w") as f:
            json.dump(pl, f)
        procs.append(agents[who].popen("submit", "finding", path))
    outs = [pr.communicate() for pr in procs]
    codes = [pr.returncode for pr in procs]
    check("6 concurrent submits from 3 people all land", codes == [0] * 6, [(cd, o[1]) for cd, o in zip(codes, outs)])
    ids = {}
    for (who, pl), (out, _) in zip(payloads, outs):
        m = re.search(r"SUBMITTED (F-\w+)", out)
        ids[pl["title"]] = m.group(1) if m else None
    F_LOGIN, F_README, F_DOCROT = (ids["Login handler returns 200 for bad passwords"], ids["README lacks setup steps"],
                                   ids["Guide recommends monthly rotation but code never rotates"])
    code, st = alice.json("status", "--kind", "finding")
    check("ledger holds all 6 findings", len(st.get("records", [])) == 6, len(st.get("records", [])))
    shard_ok = all(re.match(r"records/finding/\w\w/F-\w{8}\.md$", git(c["alice"], "ls-tree", "-r", "--name-only",
                   "refs/remotes/origin/cairn/ledger", "records/finding").splitlines()[0]) for _ in [0])
    check("records are sharded into 2-character folders", shard_ok)
    check("agents fetch only the ledger tip (shallow)", git(c["alice"], "rev-parse", "--is-shallow-repository") == "true")

    # 4. leases steer agents away from each other ------------------------------
    p = alice.run("claim", "audit/auth/security", "--intent", "review token handling", show=True)
    check("alice claims audit/auth/security", p.returncode == 0, p.stderr)
    p = bob.run("check", "review session token comparison", "--area", "auth", "--lens", "security", show=True)
    check("bob's check says HELD (exit 2)", p.returncode == 2 and p.stdout.startswith("HELD"), p.stdout)
    check("bob cannot take alice's lease", bob.run("claim", "audit/auth/security").returncode == 2)
    check("bob cannot extend alice's lease", bob.run("heartbeat", "audit/auth/security").returncode == 2)
    a = finding("Session token compared with == (timing leak)",
                "verify_token compares secrets with ==, leaking a prefix through timing.",
                "src/auth/session.py", "41-45", "verify_token",
                assumptions=["no caller compares tokens before verify_token"])
    code, out = alice.submit("finding", a, "--release", "audit/auth/security")
    check("alice submits the timing finding and releases her lease in one commit", code == 0, out)
    A = out.get("id")

    # 5. duplicates become confirmations --------------------------------------
    b = finding("Non-constant-time comparison of session tokens in verify_token",
                "Token comparison in verify_token uses == instead of hmac.compare_digest, enabling timing attacks.",
                "src/auth/session.py", "43-45", "verify_token")
    p = bob.run("submit", "finding", "-", input=json.dumps(b), show=True)
    check("bob's differently-worded duplicate is refused (exit 2)", p.returncode == 2 and A in p.stdout, p.stdout)
    check("the refusal says how alike the wording is", "wording 0." in p.stdout and "same code" in p.stdout, p.stdout)
    other = finding("verify_token allocates a new buffer for every call",
                    "Each call to verify_token copies the stored token into a fresh bytes object before comparing.",
                    "src/auth/session.py", "43-45", "verify_token", ("auth", "security"), "low")
    code, out = bob.submit("finding", other, "--dry-run")
    check("a different claim about the same lines passes a dry run, which writes nothing",
          code == 0 and out.get("dry_run") and "related" in (out.get("note") or "")
          and bob.json("search", "allocates a new buffer")[1].get("records", []) == [], out)
    code, out = alice.submit("finding", dict(a, title="Session token compared with == (timing leak), again"))
    check("an exact copy of the session's own finding is still refused", code == 2, out)
    code, out = alice.submit("finding", dict(a, claim="x" * 700), "--dry-run")
    check("a field over its limit is named with its length", code == 1 and "700 characters" in json.dumps(out)
          and "600" in json.dumps(out), out)
    p = alice.run("review", A, "confirms", "--method", "reproduced", "--note", "re-ran my own test")
    check("confirming a finding you filed in this session is refused", p.returncode == 2
          and "in this session" in p.stderr + p.stdout and alice.json("show", A)[1].get("status") == "open", p.stdout + p.stderr)
    p = alice.run("review", A, "confirms", "--method", "reproduced", "--note", "second agent, own node",
                  env={"CAIRN_SESSION": "s-other-agent"})
    check("another agent of the same person may confirm it, without making it confirmed",
          p.returncode == 0 and alice.json("show", A)[1].get("status") == "open", p.stdout + p.stderr)
    bob.run("review", A, "confirms", "--method", "reproduced-blind", "--note", "found independently", show=True)
    check("independent confirmation from another person -> confirmed", bob.json("show", A)[1].get("status") == "confirmed")
    check("the same review twice is refused", bob.run("review", A, "confirms", "--method", "reproduced-blind").returncode == 2)
    unrelated = finding("Session expiry uses local wall-clock time", "new_session sets expiry from time.time() "
                        "without a monotonic clock.", "src/auth/session.py", "14-17", "new_session",
                        ("auth", "correctness"), "low")
    code, out = carol.submit("finding", unrelated)
    check("a different bug in the same file is accepted", code == 0, out)
    C_EXPIRY = out.get("id")
    code, out = carol.submit("finding", b, "--not-duplicate", "different attack: remote timing over HTTP/2")
    check("--not-duplicate overrides and is recorded", code == 0 and out["record"].get("dedupe_override"), out)

    # 6. freshness: line-level, re-verification, fixes, release ----------------
    code_change(c["carol"], {"src/auth/session.py": lambda t: t + "# refactor at the end of the file\n"}, "tail edit")
    fr = alice.fresh(A)
    check("an edit elsewhere in the file only puts the finding on WATCH", fr.get("state") == "watch"
          and "file" in kinds(fr), fr)
    git(c["bob"], "pull", "-q", "--ff-only")
    bob.run("review", A, "confirms", "--method", "reproduced", "--note", "still reproduces after refactor")
    check("re-confirming at the new commit makes it fresh", alice.fresh(A).get("state") == "fresh", alice.fresh(A))
    fix_sha = code_change(c["carol"], {"src/auth/session.py": lambda t: t.replace(
        "return token == expected", "return hmac.compare_digest(token, expected)")},
        f"use compare_digest\n\nCairn-Fixes: {A}")
    fr = alice.fresh(A)
    check("an edit inside the located lines makes it STALE", fr.get("state") == "stale" and "lines" in kinds(fr), fr)
    check("release status: the buggy code is still in the stable branch",
          alice.json("show", A)[1].get("in_release") == "present", alice.json("show", A)[1].get("in_release"))
    bot = Agent("github-actions[bot]", c["carol"], agent="ci")
    p = bot.run("fixes", "--since", "1", show=True)
    check("`cairn fixes` turns the Cairn-Fixes trailer into a fixed review", p.returncode == 0
          and alice.json("show", A)[1].get("status") == "fixed", p.stdout + p.stderr)
    p = bot.run("fixes", "--since", "1")
    check("running `fixes` again changes nothing", "nothing new" in p.stdout, p.stdout)

    # 7. freshness: dependencies, packages, callers, renames, deletions, age ----
    git(c["alice"], "pull", "-q", "--ff-only")
    dep = finding("retry() is not idempotent: a job can run twice", "If pull() returns a job twice, retry runs it "
                  "twice; there is no dedupe key.", "src/sync/worker.py", "3-4", "retry", ("sync", "concurrency"),
                  depends_on=[{"path": "src/api/server.py"}, {"package": "serde"}],
                  assumptions=["pull() can return the same job twice"])
    code, out = alice.submit("finding", dep)
    F_DEP = out.get("id")
    check("a finding can declare assumptions and dependencies", code == 0, out)
    check("it starts fresh", alice.fresh(F_DEP).get("state") == "fresh", alice.fresh(F_DEP))
    code_change(c["carol"], {"Cargo.lock": lambda t: t.replace('version = "1.2.0"', 'version = "1.3.0"')}, "bump tokio")
    check("bumping an unrelated package leaves it fresh", alice.fresh(F_DEP).get("state") == "fresh", alice.fresh(F_DEP))
    code_change(c["carol"], {"Cargo.lock": lambda t: t.replace('version = "1.0.1"', 'version = "1.0.9"')}, "bump serde")
    fr = alice.fresh(F_DEP)
    check("bumping a declared package puts it on WATCH", fr.get("state") == "watch" and "package" in kinds(fr), fr)
    code_change(c["carol"], {"src/api/jobs.py": lambda t: t + "\ndef enqueue_twice(job):\n    retry(job)\n"}, "caller")
    check("a changed caller of the symbol is a signal", "callers" in kinds(alice.fresh(F_DEP)), alice.fresh(F_DEP))
    code_change(c["carol"], {"src/api/server.py": lambda t: t + "\ndef health(req):\n    return 200\n"}, "health")
    check("a changed declared dependency is a signal", "depends" in kinds(alice.fresh(F_DEP)), alice.fresh(F_DEP))
    git(c["carol"], "pull", "-q", "--ff-only")
    git(c["carol"], "mv", "docs/guide.md", "docs/handbook.md")
    git(c["carol"], "commit", "-q", "-m", "rename guide")
    git(c["carol"], "push", "-q", "origin", "main")
    fr = alice.fresh(F_DOCROT)
    check("a pure rename is followed, not flagged", fr.get("state") == "fresh" and "moved" in kinds(fr), fr)
    code_change(c["carol"], {"docs/handbook.md": lambda t: t.replace("Rotate tokens monthly.", "Rotate tokens weekly.")},
                "edit handbook")
    fr = alice.fresh(F_DOCROT)
    check("an edit to the located line after a rename makes it STALE", fr.get("state") == "stale", fr)
    code_change(c["carol"], {"bench.py": None}, "drop bench script")
    code, out = carol.submit("finding", finding("bench.py prints a fixed number", "The benchmark is a stub.",
                                                "bench.py", "1", None, ("root", "tests"), "low"),
                             "--commit", git(c["carol"], "rev-parse", "HEAD~1"))
    fr = alice.fresh(out.get("id"))
    check("a deleted file makes it GONE", fr.get("state") == "gone", fr)
    fr = alice.json("show", F_README, env={"CAIRN_NOW": later(24 * 40)})[1].get("freshness", {})
    check("an unchanged finding still EXPIRES after 30 days", fr.get("state") == "expired" and "age" in kinds(fr), fr)

    # 8. coverage, including the clean result ----------------------------------
    for d in (c["alice"], c["bob"]):
        git(d, "pull", "-q", "--ff-only")
    report = {"title": "auth security audit after the compare_digest fix", "lens": "security", "areas": ["auth"],
              "coverage": [{"area": "auth", "lens": "security", "depth": "deep", "result": "issues"},
                           {"area": "api", "lens": "performance", "depth": "standard", "result": "clean"}],
              "links": {"cites": [A]}, "body": "Read every function in src/auth. Only the timing issue, now fixed."}
    code, out = alice.submit("report", report)
    check("standard or deep coverage that does not say what was skipped is refused",
          code == 1 and "not_examined" in json.dumps(out), out)
    skim = dict(report, coverage=[{"area": "docs", "lens": "docs", "depth": "skim", "result": "clean"}])
    check("skim coverage needs no such statement", alice.submit("report", skim)[0] == 0)
    report["coverage"][0]["not_examined"] = "the OAuth callback; nothing behind the SSO flag"
    report["coverage"][1]["not_examined"] = "nothing: the area is one file and I read all of it"
    check("alice records coverage, including a clean result", alice.submit("report", report)[0] == 0)
    code, out = bob.submit("report", report)
    check("bob's identical coverage at the same commit is refused", code == 2, out)
    old = re.search(r"R-[0-9A-Z]{8}", json.dumps(out)).group(0)
    code, out = alice.submit("report", dict(report, body="Read every function in src/auth, and src/api/jobs.py.",
                                            links={"cites": [A], "supersedes": old}))
    check("a report that supersedes the one covering the cell is accepted",
          code == 0 and alice.json("show", old)[1].get("status") == "superseded", out)
    mixed = dict(report, links={"cites": [A]}, coverage=[
        dict(report["coverage"][0]),
        {"area": "sync", "lens": "performance", "depth": "skim", "result": "clean"}])
    code, out = bob.submit("report", mixed)
    kept = [(x["area"], x["lens"]) for x in (out.get("record") or {}).get("coverage", [])]
    check("a report with one covered cell keeps its other cells and says which was dropped",
          code == 0 and kept == [("sync", "performance")] and "auth × security" in (out.get("note") or ""), out)
    code, out = bob.submit("report", dict(mixed, coverage=[dict(report["coverage"][0], depth="skim")]),
                           "--not-duplicate", "only looked at the cookie flags")
    cov = {(x["area"], x["lens"]): x for x in bob.json("coverage")[1].get("cells", [])} if code == 0 else {}
    check("a later skim of a cell does not downgrade a deeper report at the same commit",
          code == 0 and cov.get(("auth", "security"), {}).get("depth") == "deep", cov or out)
    p = bob.run("check", "security audit of auth", "--area", "auth", "--lens", "security", show=True)
    check("check says COVERED for a freshly audited cell", p.returncode == 2 and p.stdout.startswith("COVERED"), p.stdout)
    check("check shows what the audit did not examine", "not examined: the OAuth callback" in p.stdout, p.stdout)
    p = bob.run("check", "look for races in the worker", "--area", "sync", "--lens", "concurrency", show=True)
    check("check says PROCEED for an unexamined cell", p.returncode == 0 and "audit/sync/concurrency" in p.stdout, p.stdout)

    # 9. tasks: post, pull concurrently, resolve --------------------------------
    for title, area, lens, pri in [("Is the retry loop in sync/worker.py idempotent?", "sync", "concurrency", "p1"),
                                   ("Does the rate limiter cover the login endpoint?", "api", "security", "p0"),
                                   ("Are the token rotation docs current?", "docs", "docs", "p2")]:
        check(f"carol posts {pri} task", carol.run("task", title, "--area", area, "--lens", lens, "--priority", pri).returncode == 0)
    check("a near-identical task is refused",
          carol.run("task", "Is the sync retry loop idempotent?", "--area", "sync", "--lens", "concurrency").returncode == 2)
    pa, pb = alice.popen("next", "--json"), bob.popen("next", "--json")
    ra, rb = json.loads(pa.communicate()[0]), json.loads(pb.communicate()[0])
    ta, tb = (ra.get("task") or {}).get("id"), (rb.get("task") or {}).get("id")
    check("two concurrent `next` calls get different tasks", ta and tb and ta != tb, (ra, rb))
    check("they get the two highest priorities", sorted([ra["task"]["priority"], rb["task"]["priority"]]) == ["p0", "p1"])
    code, rc = carol.json("next")
    check("third agent gets the p2 task", code == 0 and rc["task"]["priority"] == "p2", rc)
    check("no tasks left: exit 2", bob.run("next").returncode == 2)
    sync_task = ta if ra["task"]["priority"] == "p1" else tb
    sync_agent = alice if sync_task == ta else bob
    code, out = sync_agent.json("review", F_DEP, "confirms", "--method", "reproduced", "--resolves", sync_task,
                                "--release", f"task/{sync_task.lower()}")
    check("a review can answer a task and release its lease", code == 0, out)
    check("task is done", carol.json("show", sync_task)[1].get("status") == "done")

    # 10. benchmarks merge instead of duplicating ------------------------------
    code, env = alice.json("env")
    bench = {"title": "http-latency on alice's laptop", "bench": "http-latency", "areas": ["api"], "env": env,
             "metrics": [{"name": "p50_ms", "unit": "ms", "better": "lower", "samples": [12.1, 11.8, 12.4]}]}
    check("alice records a benchmark", alice.submit("benchmark", bench)[0] == 0)
    bench2 = dict(bench, title="http-latency rerun", metrics=[dict(bench["metrics"][0], samples=[12.0, 12.2])])
    code, out = bob.submit("benchmark", bench2, "--commit", git(c["alice"], "rev-parse", "HEAD"))
    check("bob's run at the same commit/env merges", code == 0 and "merged" in (out.get("note") or ""), out)
    check("bench shows 5 pooled samples", any(s["n"] == 5 for s in alice.json("bench", "http-latency")[1].get("series", [])))
    alice.run("bench", "http-latency", show=True)
    check("unregistered benchmark is rejected", alice.submit("benchmark", dict(bench, bench="nope"))[0] == 1)

    # 11. leases expire, and writes clean them up --------------------------------
    alice.run("claim", "audit/api/performance", "--intent", "profile handlers", "--ttl", "1h")
    p = bob.run("claim", "audit/api/performance", "--intent", "take over", env={"CAIRN_NOW": later(2)})
    check("an expired lease can be taken over", p.returncode == 0, p.stdout + p.stderr)
    alice.run("claim", "topic/root/sweep-me", "--intent", "abandoned work", "--ttl", "1h")
    bob.run("map", "add", "term", '{"lease":"a claim with an expiry"}', env={"CAIRN_NOW": later(3)})
    tree = git(c["bob"], "ls-tree", "-r", "--name-only", "refs/remotes/origin/cairn/ledger", "claims")
    check("an unrelated write deletes leases that expired (no cleanup job needed)", "sweep-me" not in tree, tree)

    # 12. the recorded commit is one other people can resolve -------------------
    open(os.path.join(c["alice"], "LOCAL.md"), "w").write("not pushed\n")
    git(c["alice"], "add", "LOCAL.md")
    git(c["alice"], "commit", "-q", "-m", "local only")
    code, out = alice.submit("finding", finding("Example on an unpushed commit", "Claim made on local work.",
                                                "src/api/jobs.py", "1", None, ("api", "tests"), "info"))
    check("an info finding is told that small fixes belong in the code", "small fix" in (out.get("note") or ""), out)
    origin_main = git(c["alice"], "rev-parse", "origin/main")
    check("an unpushed HEAD is replaced by its merge-base with main", code == 0
          and out["record"]["commit"] == origin_main and "merge-base" in (out.get("note") or ""), out)
    git(c["alice"], "reset", "-q", "--hard", "origin/main")

    # 13. executable repros ------------------------------------------------------
    code, out = bob.submit("finding", finding("Example with a disallowed repro", "x", "src/api/jobs.py", "1", None,
                                              ("api", "tests"), "info", repro={"run": "bash -c 'echo hi'", "expect": "fail"}))
    check("a repro command outside the ledger's allowed runners is rejected", code == 1
          and "allowed runner" in out.get("error", ""), out)
    p = bob.run("recheck", F_LOGIN, "--run", "--file", show=True)
    check("recheck runs an allowed repro; it still fails, so a confirmation is filed", p.returncode == 0
          and "STILL REPRODUCES" in p.stdout and bob.json("show", F_LOGIN)[1].get("status") == "confirmed", p.stdout + p.stderr)

    # 14. code for findings: patch-first changes ---------------------------------
    p = carol.run("work", "start", F_LOGIN, show=True)
    check("work start reserves the fix and makes a local branch", p.returncode == 0
          and git(c["carol"], "rev-parse", "--abbrev-ref", "HEAD").startswith("cairn/work/"), p.stdout + p.stderr)
    check("a second agent can't start the same fix", bob.run("work", "start", F_LOGIN, "--no-branch").returncode == 2)
    srv = open(os.path.join(c["carol"], "src/api/server.py")).read()
    open(os.path.join(c["carol"], "src/api/server.py"), "w").write(
        srv.replace("def login(req):\n    return 200", "def login(req):\n    return 200 if check(req) else 401"))
    git(c["carol"], "commit", "-qam", "login: check the password")
    p = carol.run("work", "submit", "--addresses", F_LOGIN, "--title", "Check the password in login()",
                  "--test", "python3 repro_login.py=pass", show=True)
    m = re.search(r"SUBMITTED (C-\w+)", p.stdout)
    CH = m.group(1) if m else None
    check("work submit stores the change as a patch next to its record", p.returncode == 0 and CH
          and "patch stored" in p.stdout, p.stdout + p.stderr)
    remote_branches = git(c["carol"], "ls-remote", "--heads", "origin", "cairn/work/*")
    check("no work branch was pushed for a small change", remote_branches == "", remote_branches)
    check("the fix lease was released by the submit", "fix/" not in json.dumps(alice.json("claims")[1].get("active")))
    code, sf = alice.json("show", F_LOGIN)
    check("the finding lists the change that addresses it", CH in sf.get("linked_from", []), sf)
    p = bob.run("work", "checkout", CH, show=True)
    check("another person rebuilds the change from the patch (no branch needed)", p.returncode == 0
          and git(c["bob"], "show", f"cairn/work/{CH.lower()}:src/api/server.py").count("401") == 1, p.stdout + p.stderr)
    bob.run("review", CH, "confirms", "--method", "reproduced", "--note", "applied; repro passes")
    check("the change is confirmed by someone else", alice.json("show", CH)[1].get("status") == "confirmed")
    check("a fresh change applies cleanly", alice.fresh(CH).get("state") == "fresh", alice.fresh(CH))
    git(c["carol"], "checkout", "-q", "main")
    code_change(c["carol"], {"src/api/server.py": lambda t: "# api server\n" + t}, "header comment")
    fr = alice.fresh(CH)
    check("main touching the change's file without conflict -> WATCH (drift)", fr.get("state") == "watch"
          and "drift" in kinds(fr), fr)
    p = bob.run("work", "refresh", "--all", show=True)
    m = re.search(r"refreshed (C-\w+) -> (C-\w+)", p.stdout)
    CH2 = m.group(2) if m else None
    check("refresh re-applies a clean change on the new tip and supersedes the old one", p.returncode == 0 and CH2
          and alice.json("show", CH)[1].get("status") == "superseded" and alice.fresh(CH2).get("state") == "fresh",
          p.stdout + p.stderr)
    code_change(c["carol"], {"src/api/server.py": lambda t: t.replace("    return 200\n", "    return 204\n", 1)
                             .replace("def login(req):\n    return 204", "def login(req):\n    return 418")}, "conflict")
    fr = alice.fresh(CH2)
    check("a conflicting edit on main makes the change STALE with the conflicting file", fr.get("state") == "stale"
          and "conflict" in kinds(fr) and "server.py" in json.dumps(fr), fr)
    p = bob.run("work", "refresh", CH2, "--post-task", show=True)
    check("refresh of a conflicting change posts a rebase task instead", p.returncode == 2 and "posted rebase task" in p.stdout,
          p.stdout + p.stderr)
    git(c["bob"], "pull", "-q", "--ff-only")
    p = bob.run("recheck", F_LOGIN, "--run")
    check("after main changes login(), the repro no longer fails: recheck says so (exit 2)", p.returncode == 2
          and "NO LONGER REPRODUCES" in p.stdout, p.stdout + p.stderr)

    # 15. re-check queue and map drift -------------------------------------------
    p = bob.run("recheck", "--post-tasks", "2", show=True)
    first = set(re.findall(r"T-\w{8}", p.stdout.split("posted")[-1]))
    check("the re-check queue posts tasks for the most stale findings", p.returncode == 0 and len(first) == 2, p.stdout)
    p = bob.run("recheck", "--post-tasks", "2")
    second = set(re.findall(r"T-\w{8}", p.stdout.split("posted")[-1]))
    check("posting again never re-posts the same findings", not (first & second), (first, second))
    code_change(c["carol"], {"tools/lint.sh": lambda t: "#!/bin/sh\n"}, "add tools")
    p = bob.run("map", "check", "--post-task")
    check("map drift: an unmapped folder becomes a task", p.returncode == 2 and "posted task" in p.stdout, p.stdout)
    check("and only once", "already exists" in bob.run("map", "check", "--post-task").stdout)

    # 15b. notes, budgeted context, harness hooks, metrics, MCP ------------------
    note = {"title": "How the sync worker processes jobs", "areas": ["sync"],
            "locs": [{"path": "src/sync/worker.py", "lines": "1-4", "symbol": "run"}],
            "summary": "run() loops forever pulling jobs and calling retry(job); there is no backoff and no dedupe key, "
                       "so a job pulled twice runs twice. retry() is also called from src/api/jobs.py.",
            "body": "## Entry points\nrun() in src/sync/worker.py.\n## Invariants\nNone enforced.\n## How to test\npython3 -m pytest tests/sync"}
    code, out = alice.submit("note", note)
    NOTE = out.get("id")
    check("an area note records how a part of the code works", code == 0, out)
    code, out = bob.submit("note", dict(note, title="Sync worker job processing overview"))
    check("a second note on the same thing is steered to improve the first", code == 2 and NOTE in json.dumps(out), out)
    code, out = bob.submit("note", dict(note, summary=note["summary"] + " pull() blocks when the queue is empty.",
                                        links={"supersedes": NOTE}))
    NOTE2 = out.get("id")
    check("improving a note supersedes the old one", code == 0 and alice.json("show", NOTE)[1].get("status") == "superseded", out)
    code, cx = alice.json("context", "--paths", "src/sync/worker.py", "--budget", "300")
    check("context for a file returns its area's note and live findings within budget", code == 0
          and NOTE2 in cx.get("text", "") and cx.get("tokens", 999) <= 330, cx)
    code, cx = alice.json("context")
    check("context with no scope is a short orientation", code == 0 and "cairn check" in cx.get("text", "")
          and cx.get("tokens", 9999) <= 950, cx.get("tokens"))
    check("the orientation says findings shown are background", "only if it bears on your task" in cx.get("text", ""), cx)
    code, cx = alice.json("context", "--area", "root")
    check("an area's context counts low-severity findings instead of listing them",
          code == 0 and F_README not in cx.get("text", "") and "lower-severity finding(s) not listed" in cx.get("text", ""), cx)
    alice.run("settings", "set", "context_min_severity", '"info"')
    code, cx = alice.json("context", "--area", "root")
    check("the severity floor is a project setting", code == 0 and F_README in cx.get("text", ""), cx)
    alice.run("settings", "set", "context_min_severity", '"medium"')
    check("an unknown severity floor is refused", alice.run("settings", "set", "context_min_severity", '"huge"').returncode != 0)
    hook_env = {"CAIRN_SESSION_ID": ""}
    start_in = json.dumps({"session_id": "sess-1", "cwd": c["alice"], "hook_event_name": "SessionStart"})
    p = alice.run("hook", "session-start", input=start_in, env=hook_env)
    out = json.loads(p.stdout or "{}")
    check("session-start hook injects orientation as hook JSON (Claude Code / Codex)",
          "[cairn]" in out.get("hookSpecificOutput", {}).get("additionalContext", ""), p.stdout[:300] + p.stderr)
    read_in = json.dumps({"session_id": "sess-1", "cwd": c["alice"], "tool_name": "Read",
                          "tool_input": {"file_path": os.path.join(c["alice"], "src/sync/worker.py")}})
    p = alice.run("hook", "tool", input=read_in)
    ctx_text = json.loads(p.stdout or "{}").get("hookSpecificOutput", {}).get("additionalContext", "")
    check("the first read in an area injects its note and findings", NOTE2 in ctx_text, p.stdout[:400] + p.stderr)
    bullets = [ln for ln in ctx_text.splitlines() if ln.startswith(f"- {F_DEP} ")]
    check("a finding in both the file and its area is injected once, not twice", F_DEP in ctx_text and len(bullets) == 1,
          ctx_text)
    p = alice.run("hook", "tool", input=read_in)
    check("the same area is not injected twice in a session", p.stdout.strip() == "", p.stdout)
    resume_in = json.dumps({"session_id": "sess-1", "cwd": c["alice"], "hook_event_name": "SessionStart", "source": "resume"})
    p = alice.run("hook", "session-start", input=resume_in, env=hook_env)
    again = alice.run("hook", "tool", input=read_in)
    check("a resumed session is given nothing twice (Codex resumes on every turn)",
          p.returncode == 0 and p.stdout.strip() == "" and again.stdout.strip() == "", p.stdout[:200] + again.stdout[:200])
    bash_in = json.dumps({"session_id": "sess-1", "cwd": c["alice"], "tool_name": "Bash",
                          "tool_input": {"command": "sed -n '1,40p' src/auth/session.py | head"}})
    p = alice.run("hook", "tool", input=bash_in)
    check("file paths inside shell commands are recognised (how Codex reads files)",
          "auth" in json.loads(p.stdout or "{}").get("hookSpecificOutput", {}).get("additionalContext", ""), p.stdout[:300])
    p = alice.run("hook", "tool", "--format", "text", "--session", "sess-2", "--path", "src/api/server.py", input="")
    check("text format for adapters without hook JSON (OpenCode, Pi): silent without a session", p.stdout.strip() == "")
    alice.run("hook", "session-start", "--format", "text", "--session", "sess-2", input=json.dumps({"cwd": c["alice"]}))
    p = alice.run("hook", "tool", "--format", "text", "--session", "sess-2", "--path", "src/api/server.py", input="")
    check("text format returns plain context", p.stdout.startswith("[cairn]"), p.stdout[:200])
    compact_in = json.dumps({"session_id": "sess-2", "cwd": c["alice"], "source": "compact"})
    p = alice.run("hook", "session-start", "--format", "text", "--session", "sess-2", input=compact_in)
    again = alice.run("hook", "tool", "--format", "text", "--session", "sess-2", "--path", "src/api/server.py", input="")
    check("after a compaction the session starts over", p.stdout.startswith("[cairn]") and again.stdout.startswith("[cairn]"),
          p.stdout[:200] + again.stdout[:200])
    transcript = os.path.join(tmp, "t.jsonl")
    with open(transcript, "w") as f:
        f.write(json.dumps({"type": "assistant", "message": {"id": "m1", "usage": {"input_tokens": 1000, "output_tokens": 200}}}) + "\n")
        f.write(json.dumps({"type": "assistant", "message": {"id": "m2", "usage": {"input_tokens": 1500, "output_tokens": 300,
                                                                                  "cache_read_input_tokens": 900}}}) + "\n")
    alice.run("hook", "session-end", input=json.dumps({"session_id": "sess-1", "cwd": c["alice"], "transcript_path": transcript}))
    code, m = alice.json("metrics")
    ses = m.get("mine", {}).get("sessions", {}).get("context", {})
    check("session-end records token use from the transcript", ses.get("sessions") == 1 and ses.get("median_tokens") == 3900, m)
    bm = bob.json("metrics")[1]
    check("metrics count duplicate work avoided by `check` (bob's HELD and COVERED)",
          {"HELD", "COVERED"} <= set(bm.get("mine", {}).get("checks", {})), bm)
    check("metrics can be published to the ledger", alice.run("metrics", "--publish").returncode == 0)
    alice.run("settings", "set", "context_holdout", "1.0")
    p = alice.run("hook", "session-start", input=json.dumps({"session_id": "sess-3", "cwd": c["alice"]}))
    check("holdout sessions get no injected context (for A/B measurement)", p.stdout.strip() == "", p.stdout[:200])
    alice.run("settings", "set", "context_holdout", "0.0")
    reqs = "\n".join(json.dumps(x) for x in [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "cairn_context",
                                                                       "arguments": {"paths": ["src/sync/worker.py"]}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "cairn_status",
                                                                       "arguments": {"kind": "note", "area": "sync"}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "cairn_map", "arguments": {}}}]) + "\n"
    p = sh([sys.executable, CAIRN, "-C", c["alice"], "mcp"], c["alice"], {"CAIRN_USER": "alice"}, reqs)
    resp = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
    tools = [t["name"] for t in (resp[1]["result"]["tools"] if len(resp) > 1 else [])]
    check("MCP server answers initialize and lists tools", len(resp) == 5 and "cairn_check" in tools
          and {"cairn_map", "cairn_status", "cairn_whoami"} <= set(tools), p.stdout[:300] + p.stderr)
    check("MCP tool call returns context", len(resp) == 5 and NOTE2 in resp[2]["result"]["content"][0]["text"], resp[2:3])
    check("MCP tools can list records and print the map", len(resp) == 5 and NOTE2 in resp[3]["result"]["content"][0]["text"]
          and "Lenses" in resp[4]["result"]["content"][0]["text"], resp[3:])
    events = [json.loads(l) for l in open(os.path.join(c["alice"], ".git", "cairn-events.jsonl"))]
    check("the local event log says why a call was refused",
          any(e.get("exit") == 2 and "LIKELY DUPLICATE" in e.get("why", "") for e in events)
          and any(e.get("exit") == 1 and "700 characters" in e.get("why", "") for e in events), events[-3:])

    alice.run("settings", "set", "latest_version", '"99.0.0"')
    p = alice.run("hook", "session-start", input=json.dumps({"session_id": "sess-4", "cwd": c["alice"]}))
    told = json.loads(p.stdout or "{}").get("hookSpecificOutput", {}).get("additionalContext", "")
    check("a session on an older Cairn than the ledger's is told to update (and so is `brief`)",
          "99.0.0" in told and "update Cairn" in told and "99.0.0" in alice.run("brief").stdout, told[-300:])
    alice.run("settings", "set", "latest_version", "null")
    p = alice.run("hook", "session-start", input=json.dumps({"session_id": "sess-5", "cwd": c["alice"]}))
    check("a session on a current Cairn is told nothing about updating",
          "[cairn]" in p.stdout and "update Cairn" not in p.stdout, p.stdout[-300:])

    # 16. the core has no project vocabulary; profiles supply it ------------------
    core = open(CAIRN).read() + open(os.path.join(KIT, "skill/SKILL.md")).read()
    ex_dir = os.path.join(KIT, "profiles/example")
    ex_map, ex_cfg = json.load(open(os.path.join(ex_dir, "map.json"))), json.load(open(os.path.join(ex_dir, "ledger.json")))
    general = {l["id"] for l in alice.json("map")[1]["lenses"]}
    terms = ([ex_cfg["code_repo"], ex_cfg["code_repo"].split("/")[1]] + [b["id"] for b in ex_map["benchmarks"]]
             + [l["id"] for l in ex_map["lenses"] if l["id"] not in general])
    leaks = [w for w in terms if w in core.lower()]
    check("CLI and skill contain no profile's terms", len(terms) >= 4 and not leaks, leaks)
    same = all(open(os.path.join(KIT, x)).read() == open(os.path.join(KIT, y)).read() for x, y in [
        ("cli/cairn.py", "adapters/claude-code/bin/cairn"), ("skill/SKILL.md", "adapters/claude-code/skills/cairn/SKILL.md"),
        ("skill/reference.md", "adapters/claude-code/skills/cairn/reference.md")])
    check("the Claude Code plugin carries the same CLI and skill as every other harness", same)
    toy = Agent("alice", c["alice"], extra={"CAIRN_BRANCH": "cairn/toy"})
    toy_bob = Agent("bob", c["bob"], extra={"CAIRN_BRANCH": "cairn/toy"})
    p = toy.run("init", "--profile", os.path.join(KIT, "tests/fixtures/toy-profile"), show=True)
    check("a second project vocabulary initialises from a profile", p.returncode == 0, p.stdout + p.stderr)
    check("its guide is served to agents", "Toy project notes" in toy_bob.run("guide").stdout)
    code, out = toy_bob.submit("finding", finding("Example toy finding", "uses a lens this ledger lacks",
                                                  "src/api/server.py", "1", None, ("core", "security")))
    check("a lens from another vocabulary is unknown here", code == 1 and "unknown lens" in out.get("error", ""), out)
    code, out = toy_bob.submit("finding", finding("Example toy facet", "bad facet value", "src/api/server.py", "1",
                                                  None, ("core", "correctness"), facets={"platform": ["windows"]}))
    check("facet values are validated against the ledger's map", code == 1 and "facet" in out.get("error", ""), out)
    code, out = toy_bob.submit("finding", finding("Unbounded queue in jobs", "enqueue grows without bound",
                                                  "src/api/jobs.py", "1-4", "enqueue", ("core", "dos"), "critical"))
    check("a public ledger refuses severe findings in a lens the project marked sensitive", code == 1
          and "sensitive" in out.get("error", ""), out)
    code, out = toy_bob.submit("finding", finding("Queue length is not logged", "enqueue gives no visibility",
                                                  "src/api/jobs.py", "1-4", "enqueue", ("core", "dos"), "low",
                                                  facets={"platform": ["linux"]}))
    check("low-severity findings in that lens are fine", code == 0, out)
    code, st = toy_bob.json("status", "--facet", "platform=linux")
    check("records can be filtered by facet", len(st.get("records", [])) == 1, st)
    ex = Agent("alice", c["alice"], extra={"CAIRN_BRANCH": "cairn/example"})
    p = ex.run("init", "--profile", ex_dir)
    check("the example profile loads through the same generic code", p.returncode == 0, p.stdout + p.stderr)
    ex_ledger_map, default_map = ex.json("map")[1], alice.json("map")[1]
    check("its lenses stay in its own ledger", "data-loss" in [l["id"] for l in ex_ledger_map["lenses"]]
          and "data-loss" not in [l["id"] for l in default_map["lenses"]])

    # 17. validation and audit ----------------------------------------------------
    check("whole ledger validates", alice.run("validate", show=True).returncode == 0)
    sh(["git", "fetch", "-q", "origin", "+refs/heads/cairn/ledger:refs/remotes/origin/cairn/ledger"], c["bob"])
    before = git(c["bob"], "rev-parse", "refs/remotes/origin/cairn/ledger")
    code, out = bob.submit("finding", finding("server.handle has no timeout", "handle() can block forever on "
                                              "route().", "src/api/server.py", "1-2", "handle", ("api", "reliability")))
    after = git(c["bob"], "rev-parse", "refs/remotes/origin/cairn/ledger")
    check("bob's push landed", code == 0 and before != after, out)
    check("per-push mode: a normal push validates for its pusher",
          bob.run("validate", "--base", before, "--head", after, "--actor", "bob").returncode == 0)
    p = bob.run("validate", "--base", before, "--head", after, "--actor", "alice")
    check("per-push mode: a record pushed by someone other than its author fails", p.returncode == 1
          and "not the writer" in p.stdout, p.stdout)
    p = alice.run("validate", "--since", "1", show=True)
    check("daily audit mode checks every recent commit", p.returncode == 0 and "commit(s) checked" in p.stdout, p.stdout)
    rec_path = git(c["bob"], "ls-tree", "-r", "--name-only", after, "records/finding").splitlines()[0]
    blob = git(c["bob"], "show", f"{after}:{rec_path}").replace('"severity": "', '"severity": "x')
    env = {"GIT_INDEX_FILE": os.path.join(tmp, "tamper.idx")}
    sh(["git", "read-tree", after], c["bob"], env)
    sha = sh(["git", "hash-object", "-w", "--stdin"], c["bob"], input=blob).stdout.strip()
    sh(["git", "update-index", "--cacheinfo", f"100644,{sha},{rec_path}"], c["bob"], env)
    tree = sh(["git", "write-tree"], c["bob"], env).stdout.strip()
    tampered = sh(["git", "commit-tree", tree, "-p", after, "-m", "tamper"], c["bob"],
                  {"GIT_AUTHOR_NAME": "mallory", "GIT_COMMITTER_NAME": "mallory"}).stdout.strip()
    sh(["git", "push", "-q", "origin", f"{tampered}:refs/heads/cairn/ledger"], c["bob"])
    p = alice.run("validate", "--since", "1")
    check("the daily audit catches an edited record pushed around the CLI", p.returncode == 1
          and "append-only" in p.stdout, p.stdout)

    # 18. read paths, dashboard, PR context, cache ---------------------------------
    t0 = time.time()
    check("brief works", carol.run("brief", show=True).returncode == 0)
    dt_brief = time.time() - t0
    carol.run("brief", "--area", "auth", show=True)
    carol.run("coverage", show=True)
    carol.run("show", F_DEP, show=True)
    carol.run("show", CH2, show=True)
    p = carol.run("render", "--commit")
    check("render --commit writes the dashboard", p.returncode == 0 and "RENDERED" in p.stdout, p.stdout + p.stderr)
    this_version = re.search(r'^VERSION = "([^"]+)"', open(CAIRN).read(), re.M).group(1)
    check("render --commit records the Cairn version it ran with, for the update notice",
          carol.json("settings")[1].get("ledger", {}).get("latest_version") == this_version, carol.json("settings")[1].get("ledger"))
    readme = git(c["carol"], "show", "refs/remotes/origin/cairn/ledger:README.md")
    check("dashboard has coverage, live findings and release status", "## Coverage" in readme
          and "## Live findings" in readme and "in stable" in readme)
    sync_row = next((ln for ln in readme.splitlines() if ln.startswith("| sync |")), "")
    api_row = next((ln for ln in readme.splitlines() if ln.startswith("| api |")), "")
    check("dashboard shows each area's note, and a dot where there is none", "| area | note |" in readme
          and f"[`{NOTE2}`](" in sync_row and NOTE not in sync_row and api_row.startswith("| api | · |"), (sync_row, api_row))
    git(c["carol"], "checkout", "-qb", "feature", "origin/main")
    open(os.path.join(c["carol"], "src/auth/session.py"), "a").write("# PR change\n")
    git(c["carol"], "commit", "-qam", "pr change")
    p = carol.run("touching", "--diff", "origin/main...HEAD", "--markdown", show=True)
    check("PR context lists live findings on changed files (not fixed ones)", C_EXPIRY in p.stdout and A not in p.stdout,
          p.stdout)
    git(c["carol"], "checkout", "-q", "main")
    cache = os.path.join(c["alice"], ".git", "cairn-cache.pickle")
    open(cache, "wb").write(b"garbage")
    check("a corrupt cache is ignored", alice.run("brief").returncode == 0)
    clean = all(git(d, "status", "--porcelain") == "" for d in (c["alice"], c["bob"]))
    check("ledger writes never touch the agent's working tree", clean)

    # 18a. the author's agent is self-reported as harness/model; a wrong one is accepted with a reminder -----
    named = Agent("carol", c["carol"], agent="write-sync-note")
    p = named.run("claim", "topic/docs/agent-name-check", "--intent", "check the reminder")
    q = carol.run("claim", "topic/docs/agent-name-check-2", "--intent", "check there is no reminder")
    check("a write under a task-like agent name still lands, with a reminder of the harness/model format",
          p.returncode == 0 and "harness" in p.stderr and "write-sync-note" in p.stderr
          and q.returncode == 0 and "harness" not in q.stderr, (p.stderr, q.stderr))
    named.run("release", "topic/docs/agent-name-check")
    carol.run("release", "topic/docs/agent-name-check-2")

    # 18. old notes are brought up to date, not dropped (40 days on) ---------------
    aged = {"CAIRN_NOW": later(24 * 40)}
    p = bob.run("recheck", "--post-tasks", "1", env=aged, show=True)
    posted = re.findall(r"T-\w{8}", p.stdout.split("posted")[-1])
    note_task = next((t for t in posted if NOTE2 in json.dumps(bob.json("show", t)[1])), None)
    check("an old note gets a re-check task of its own, alongside the findings'", p.returncode == 0 and len(posted) == 2
          and note_task is not None, p.stdout)
    code, cx = alice.json("context", "--paths", "src/sync/worker.py", "--budget", "600", env=aged)
    check("an agent opening that area is asked to re-verify the note, and given the task to close",
          "checked-evidence" in cx.get("text", "") and str(note_task) in cx.get("text", ""), cx.get("text"))
    before = alice.json("show", NOTE2, env=aged)[1].get("freshness", {}).get("state")
    p = carol.run("review", NOTE2, "confirms", "--method", "checked-evidence", "--note", "read against worker.py",
                  "--resolves", str(note_task), env=aged, show=True)
    state = alice.json("show", NOTE2, env=aged)[1].get("freshness", {}).get("state")
    check("checking the note against the code makes it fresh again and closes the task", p.returncode == 0
          and before == "expired" and state in ("fresh", "watch")
          and bob.json("show", str(note_task), env=aged)[1].get("status") == "done", (p.stdout + p.stderr, before, state))
    p = bob.run("recheck", "--post-tasks", "1", env=aged)
    again = [t for t in re.findall(r"T-\w{8}", p.stdout.split("posted")[-1]) if NOTE2 in json.dumps(bob.json("show", t)[1])]
    check("a note that was just re-verified is not queued again", not again, p.stdout)

    n_commits = git(c["alice"], "rev-list", "--count", "refs/remotes/origin/cairn/ledger")
    code, st = carol.json("status", "--kind", "finding", "--kind", "report", "--kind", "task", "--kind",
                          "benchmark", "--kind", "review", "--kind", "change", "--limit", "500")
    print(f"\nledger: {n_commits} commits fetched (shallow), {len(st.get('records', []))} records; brief took {dt_brief:.2f}s")
    passed = sum(ok for _, ok in RESULTS)
    print(f"\n{passed}/{len(RESULTS)} checks passed")
    if "--transcript" in sys.argv:
        with open(sys.argv[sys.argv.index("--transcript") + 1], "w") as f:
            f.write("\n".join(TRANSCRIPT))
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
