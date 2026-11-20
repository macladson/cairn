#!/usr/bin/env python3
"""Cairn: a shared, git-native ledger of agent findings, reports, benchmarks and tasks.

One file, Python 3.9+, standard library only. Records live on an orphan branch
(default `cairn/ledger`), either in the repo they describe or in a private companion
repo. Every write is built off-tree on the current ledger tip and pushed without
--force, so git's atomic ref update acts as compare-and-swap: concurrent agents never
clobber each other, and claims (work leases) are mutually exclusive.

Nothing project-specific is built into this file. Each ledger carries its own
vocabulary (map.json), settings (ledger.json) and agent notes (GUIDE.md); projects
start from a profile with `cairn init --profile <dir>`.

Agents: read .claude/skills/cairn/SKILL.md, then run `cairn guide`.
Exit codes: 0 ok, 1 error, 2 blocked (held claim, likely duplicate, not PROCEED).
"""
from __future__ import annotations

import argparse
import atexit
import datetime as dt
import functools
import getpass
import hashlib
import json
import math
import os
import pickle
import platform
import random
import re
import secrets
import shlex
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict

VERSION = "0.1.0"

KIND_PREFIX = {"finding": "F", "report": "R", "benchmark": "B", "review": "V", "task": "T", "change": "C", "note": "N"}
DEFAULT_LENSES = [
    {"id": "correctness", "description": "Wrong results, broken invariants, logic errors"},
    {"id": "security", "description": "Vulnerabilities, unsafe input handling, secrets, authn/authz", "sensitive": True},
    {"id": "performance", "description": "Latency, throughput, memory, algorithmic cost"},
    {"id": "reliability", "description": "Error handling, retries, timeouts, failure modes"},
    {"id": "concurrency", "description": "Races, deadlocks, ordering, idempotency"},
    {"id": "tests", "description": "Missing, flaky or misleading tests"},
    {"id": "api", "description": "Interface design, compatibility, contracts"},
    {"id": "dependencies", "description": "Third-party packages: versions, licences, CVEs"},
    {"id": "build", "description": "Build, packaging, CI, tooling"},
    {"id": "docs", "description": "Documentation that is missing, wrong or stale"},
]
SEVERITIES = ["info", "low", "medium", "high", "critical"]
CONFIDENCE = ["low", "medium", "high"]
EVIDENCE_TYPES = ["repro", "test", "trace", "log", "benchmark", "code-reading"]
VERDICTS = ["confirms", "refutes", "duplicate", "fixed", "stale", "retract"]
METHODS = ["reproduced-blind", "reproduced", "checked-evidence", "opinion"]
COUNTING_METHODS = {"reproduced-blind", "reproduced"}
DEPTHS = ["skim", "standard", "deep"]
RESULTS = ["clean", "issues"]
PRIORITIES = ["p0", "p1", "p2", "p3"]
LIVE_STATES = {"open", "current", "proposed", "confirmed", "disputed"}
GONE_STATES = {"retracted", "duplicate", "superseded"}
FRESH_RANK = {"unknown": 0, "fresh": 1, "watch": 2, "expired": 3, "stale": 4, "gone": 5}

MAP_FILE, SETTINGS_FILE, GUIDE_FILE = "map.json", "ledger.json", "GUIDE.md"
DEFAULT_SETTINGS = {
    "main_remote": "origin",        # remote that hosts the code
    "main_branch": "main",          # branch findings are checked against
    "release_branch": None,         # optional: branch of the latest release, for "is it in the release?"
    "code_repo": None,              # optional: owner/repo of the code when the ledger lives in a companion repo
    "public": False,                # true if the ledger is publicly readable
    "bots": ["github-actions[bot]", "cairn-bot"],
    "freshness": {"max_days": 30, "max_area_commits": 50, "covered_max_commits": 5, "covered_max_days": 30},
    "lockfiles": ["Cargo.lock", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "uv.lock",
                  "go.sum", "Gemfile.lock", "composer.lock"],
    "repro_runners": [],            # allowed repro command patterns, e.g. "pytest {path}::{test}"
    "repro_timeout": 1800,
    "work_remote": None,            # where `cairn work` pushes code branches (default: the ledger remote)
    "patch_max_bytes": 200000,      # changes smaller than this also store a .patch next to the record
    "context_budget": {"session": 900, "area": 450},  # tokens of context injected by hooks
    "context_min_severity": "medium",   # an area's injected context lists findings at or above this; the rest
                                    # are counted, and still shown from the files they are located in
    "context_holdout": 0.0,         # share of sessions that get no injected context (to measure the effect)
    "shallow_fetch": True,          # agents fetch only the ledger tip, never its history
    "latest_version": None,         # newest Cairn the ledger has been maintained with (the daily job records it);
                                    # sessions running an older one are told to update
}

DUP_THRESHOLD = 0.55        # submit refuses at or above this similarity...
DUP_TEXT_FLOOR = 0.45       # ...if the title and claim themselves are at least this alike. Two findings in one
                            # function share its names and lines without making the same claim.
DUP_OWN_SESSION = 0.90      # a session knows what it has just filed: only a near-copy of its own record is refused
RELATED_THRESHOLD = 0.30    # shown as related at or above this
DEFAULT_TTL = "2h"
BENCH_ENOUGH_SAMPLES = 5
SWEEP_GRACE = dt.timedelta(minutes=10)
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
CACHE_VERSION = 2

EXIT_OK, EXIT_ERROR, EXIT_BLOCKED = 0, 1, 2

ID_RE = re.compile(r"^[FRBVTCN]-[0-9A-HJKMNP-TV-Z]{8}$")
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)+$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
LINES_RE = re.compile(r"^\d+(-\d+)?$")
TS_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
PLACEHOLDER = r"[A-Za-z0-9_:./=+@-]+"

SECRET_PATTERNS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS access key"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"), "GitHub token"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{40,}"), "GitHub fine-grained token"),
    (re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----"), "private key"),
    (re.compile(r"xox[abposr]-[A-Za-z0-9-]{10,}"), "Slack token"),
    (re.compile(r"sk-(?:ant-|proj-)?[A-Za-z0-9_-]{32,}"), "API secret key"),
    (re.compile(r"AIza[0-9A-Za-z_-]{35}"), "Google API key"),
]

STOPWORDS = set("""a an the of to in on for and or is are was were be been by with this that these those it its as at
from not no can may might when if into via than then there their our we you they should would could does do did has have
had using use used uses also only just so such per any all each other more most less very""".split())


class CairnError(Exception):
    """Fatal error (exit 1)."""


class DryRun(Exception):
    """Raised inside a write to stop before anything is pushed (submit --dry-run)."""


class Blocked(Exception):
    """The ledger says no: held claim, likely duplicate, already reviewed (exit 2)."""

    def __init__(self, message, data=None):
        super().__init__(message)
        self.data = data or {}


# ----------------------------------------------------------------------------- time

def now():
    forced = os.environ.get("CAIRN_NOW")
    if forced:
        return parse_ts(forced)
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso(t):
    return t.strftime(TS_FMT)


def parse_ts(s):
    return dt.datetime.strptime(s, TS_FMT).replace(tzinfo=dt.timezone.utc)


def parse_ttl(s):
    m = re.fullmatch(r"(\d+)\s*([smhd])", s.strip().lower())
    if not m:
        raise CairnError(f"bad TTL {s!r}: use e.g. 45m, 2h, 1d")
    secs = int(m.group(1)) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]
    if not 60 <= secs <= 7 * 86400:
        raise CairnError("TTL must be between 1 minute and 7 days")
    return dt.timedelta(seconds=secs)


def human_delta(seconds):
    seconds = int(abs(seconds))
    if seconds < 90:
        return f"{seconds}s"
    if seconds < 5400:
        return f"{seconds // 60}m"
    if seconds < 172800:
        return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"
    return f"{seconds // 86400}d"


def ago(ts, t=None):
    try:
        return human_delta(((t or now()) - parse_ts(ts)).total_seconds()) + " ago"
    except Exception:
        return "?"


# ------------------------------------------------------------------------------ git

def run_git(args, input=None, env=None, check=True, binary=False):
    e = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    if env:
        e.update(env)
    kw = {} if binary else {"text": True, "encoding": "utf-8", "errors": "replace"}
    p = subprocess.run(["git"] + list(args), input=input, capture_output=True, env=e, **kw)
    if check and p.returncode != 0:
        err = p.stderr if not binary else p.stderr.decode("utf-8", "replace")
        raise CairnError(f"git {' '.join(str(a) for a in args[:3])} failed: {err.strip()}")
    return p


def git(*args, **kw):
    return run_git(args, **kw).stdout


def git_ok(*args):
    return run_git(args, check=False).returncode == 0


def rev_parse(ref):
    if not ref:
        return None
    p = run_git(["rev-parse", "--verify", "--quiet", ref + "^{commit}"], check=False)
    return p.stdout.strip() if p.returncode == 0 else None


def git_config(key):
    p = run_git(["config", "--get", key], check=False)
    return p.stdout.strip() or None


def version_tuple(v):
    """'0.2.10' -> (0, 2, 10); anything that isn't dotted numbers -> (), which never counts as newer."""
    try:
        return tuple(int(x) for x in str(v).strip().lstrip("v").split("."))
    except ValueError:
        return ()


def update_notice(settings):
    """One line for a session whose Cairn is older than the one the ledger is maintained with, else ''."""
    latest = settings.get("latest_version")
    if not latest or version_tuple(latest) <= version_tuple(VERSION):
        return ""
    return (f"[cairn] This machine runs Cairn {VERSION}; the shared ledger is maintained with {latest}. Tell your user "
            f"to update Cairn: `git pull` in their clone of the Cairn kit, then `./install.sh` there with their "
            f"assistants' names (in Claude Code, also update the cairn plugin).")


AGENT_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.:@\[\]-]*$")
AGENT_HOWTO = ("`cairn whoami --agent <harness>/<model>`: the tool you run in and the model you are, as your own "
               "system reports them (e.g. `claude-code/claude-opus-5-5`); not a task, role or person's name")
WRITE_CMDS = {"submit", "note", "task", "review", "claim", "next", "work"}


def agent_hint(ident, cfg):
    """A reminder, never a refusal: records are most useful when `author.agent` says which harness and model
    wrote them. Agents report this themselves, so a wrong value is corrected by telling them the format."""
    if AGENT_RE.match(ident.agent or "") or ident.agent in ("ci", "mcp") or ident.user in cfg.settings.get("bots", []):
        return ""
    how = AGENT_HOWTO if not os.environ.get("CAIRN_AGENT") else \
        AGENT_HOWTO.replace("`cairn whoami --agent <harness>/<model>`", "set CAIRN_AGENT to <harness>/<model>")
    return f"note: this was recorded with agent `{ident.agent}`. For your next write, say who you are with {how}."


def sign_args():
    """`git commit-tree` ignores commit.gpgsign, so pass -S for people who sign their commits: a pre-push hook or
    a branch rule that requires signatures would otherwise reject every write."""
    p = run_git(["config", "--type=bool", "--get", "commit.gpgsign"], check=False)
    return ["-S"] if p.stdout.strip() == "true" else []


def blob_text(rev, path):
    p = run_git(["cat-file", "-p", f"{rev}:{path}"], check=False)
    return p.stdout if p.returncode == 0 else None


_commit_cache = {}


def commit_exists(sha):
    if sha not in _commit_cache:
        _commit_cache[sha] = git_ok("cat-file", "-e", sha + "^{commit}")
    return _commit_cache[sha]


class Config:
    """Where the ledger lives (client side) plus the ledger's own settings (stored in ledger.json)."""

    def __init__(self, offline=False):
        p = run_git(["rev-parse", "--show-toplevel"], check=False)
        if p.returncode != 0:
            raise CairnError("not inside a git repository")
        self.root = p.stdout.strip()
        self.git_dir = git("rev-parse", "--absolute-git-dir").strip()
        self.client = {}
        path = os.path.join(self.root, ".cairn.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                self.client = json.load(f)
        self.remote = (os.environ.get("CAIRN_REMOTE") or git_config("cairn.remote") or self.client.get("remote")
                       or "origin")
        self.branch = (os.environ.get("CAIRN_BRANCH") or git_config("cairn.branch") or self.client.get("branch")
                       or "cairn/ledger")
        self.offline = offline
        self.use_ledger_settings({})

    def use_ledger_settings(self, data):
        s = json.loads(json.dumps(DEFAULT_SETTINGS))
        for src in (data or {}, self.client):
            for k, v in src.items():
                if k in DEFAULT_SETTINGS:
                    s[k] = {**s[k], **v} if isinstance(v, dict) and isinstance(s.get(k), dict) else v
        local_main = git_config("cairn.mainremote")   # e.g. when your origin is a fork: git config cairn.mainRemote upstream
        if local_main:
            s["main_remote"] = local_main
        self.settings = s

    def __getattr__(self, name):
        settings = self.__dict__.get("settings", {})
        if name in settings:
            return settings[name]
        raise AttributeError(name)

    @property
    def bot_logins(self):
        return [b.lower() for b in self.settings["bots"]]

    @property
    def ledger_ref(self):
        return f"refs/remotes/{self.remote}/{self.branch}"

    @property
    def main_ref(self):
        return f"refs/remotes/{self.main_remote}/{self.main_branch}"

    @property
    def release_ref(self):
        rb = self.settings.get("release_branch")
        return f"refs/remotes/{self.main_remote}/{rb}" if rb else None

    def main_sha(self):
        return rev_parse(self.main_ref) or rev_parse("HEAD")


class Identity:
    """Who is writing: a GitHub login (the accountable human), an agent name, a session."""

    def __init__(self, cfg):
        self.user = (os.environ.get("CAIRN_USER") or git_config("cairn.user") or git_config("github.user")
                     or git_config("user.name") or getpass.getuser())
        self.path = os.path.join(cfg.git_dir, "cairn-session.json")
        self.data = {}
        if os.path.exists(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    self.data = json.load(f)
            except (OSError, ValueError):
                self.data = {}
        if "session" not in self.data:
            self.data["session"] = "s-" + secrets.token_hex(4)
            self.save()
        self.session = os.environ.get("CAIRN_SESSION") or self.data["session"]
        self.agent = os.environ.get("CAIRN_AGENT") or self.data.get("agent") or "unknown"

    def save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f)

    def author(self):
        return {"github": self.user, "agent": self.agent, "session": self.session}

    def holds(self, claim):
        h = claim.get("holder") or {}
        return h.get("github", "").lower() == self.user.lower() and h.get("session") == self.session


LOCAL_REF_RACE = ("cannot lock ref", "unable to update local ref", "but expected", "is at ", "shallow.lock",
                  "shallow file has changed", "another git process", "index.lock")


def fetch(cfg, full=False, attempts=6):
    """Update remote-tracking refs. Agents sharing a clone queue on a lock file (where the OS has flock), and
    retry if another git process in the clone still gets in the way."""
    lock = _clone_lock(cfg)
    try:
        for attempt in range(attempts):
            try:
                return _fetch_once(cfg, full)
            except CairnError as e:
                if attempt == attempts - 1 or not any(s in str(e) for s in LOCAL_REF_RACE):
                    raise
                time.sleep(0.05 + random.random() * 0.2 * (attempt + 1))
        return None
    finally:
        if lock is not None:
            lock.close()


def _clone_lock(cfg, timeout=60.0):
    try:
        import fcntl
    except ImportError:
        return None
    try:
        f = open(os.path.join(cfg.git_dir, "cairn-fetch.lock"), "a")
    except OSError:
        return None
    end = time.time() + timeout
    while True:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return f
        except OSError:
            if time.time() > end:
                f.close()
                return None
            time.sleep(0.02)


def _fetch_once(cfg, full):
    if not cfg.offline:
        depth = ["--depth=2147483647"] if full else (["--depth=1"] if cfg.shallow_fetch else [])
        p = run_git(["fetch", "--quiet", "--no-tags", *depth, cfg.remote, f"+refs/heads/{cfg.branch}:{cfg.ledger_ref}"],
                    check=False)
        if p.returncode != 0:
            err = p.stderr.lower()
            if "couldn't find remote ref" in err or "could not find remote ref" in err:
                if rev_parse(cfg.ledger_ref):
                    run_git(["update-ref", "-d", cfg.ledger_ref], check=False)
            else:
                raise CairnError(f"git fetch {cfg.remote} failed: {p.stderr.strip()}")
    tip = rev_parse(cfg.ledger_ref)
    settings = {}
    if tip:
        text = blob_text(tip, SETTINGS_FILE)
        if text:
            try:
                settings = json.loads(text)
            except ValueError:
                settings = {}
    cfg.use_ledger_settings(settings)
    if not cfg.offline:
        specs = [f"+refs/heads/{cfg.main_branch}:{cfg.main_ref}"]
        if cfg.release_ref:
            specs.append(f"+refs/heads/{cfg.release_branch}:{cfg.release_ref}")
        run_git(["fetch", "--quiet", "--no-tags", cfg.main_remote, *specs], check=False)
    return tip


def build_commit(base, changes, message, ident):
    """Create a commit on top of `base` with `changes` ({path: text or None}) without touching the worktree.

    The commit author name is the writer's login, so the daily audit can check records against it.
    """
    with tempfile.TemporaryDirectory() as td:
        env = {"GIT_INDEX_FILE": os.path.join(td, "index")}
        git("read-tree", base, env=env) if base else git("read-tree", "--empty", env=env)
        lines = []
        for path, content in sorted(changes.items()):
            if content is None:
                lines.append(f"0 {'0' * 40}\t{path}")
            else:
                sha = git("hash-object", "-w", "--stdin", input=content).strip()
                lines.append(f"100644 {sha}\t{path}")
        git("update-index", "--index-info", input="\n".join(lines) + "\n", env=env)
        tree = git("write-tree", env=env).strip()
        mail = git_config("user.email") or f"{ident.user}@users.noreply.github.com"
        stamp = f"@{int(now().timestamp())} +0000"
        cenv = dict(env, GIT_AUTHOR_NAME=ident.user, GIT_COMMITTER_NAME=ident.user, GIT_AUTHOR_EMAIL=mail,
                    GIT_COMMITTER_EMAIL=mail, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)
        args = ["commit-tree", tree, "-m", message] + (["-p", base] if base else []) + sign_args()
        return git(*args, env=cenv).strip()


RETRYABLE = ("non-fast-forward", "fetch first", "[rejected]", "stale info", "cannot lock ref",
             "failed to update ref", "incorrect old value", "reference already exists", "remote rejected")


def write(cfg, ident, mutate, message, attempts=15):
    """Optimistic concurrency: fetch tip, apply `mutate(ledger)`, push; on rejection redo on the new tip.

    `mutate` returns {path: text|None} (or ({...}, result)) and may raise Blocked. Claims that expired more
    than SWEEP_GRACE ago are deleted in the same commit, so no separate clean-up job is needed.
    """
    if cfg.offline:
        raise CairnError("writes need the network; drop --offline")
    for attempt in range(attempts):
        base = fetch(cfg)
        ledger = Ledger(base)
        out = mutate(ledger)
        changes, result = out if isinstance(out, tuple) else (out, None)
        if not changes:
            return None, ledger, result
        cutoff = now() - SWEEP_GRACE
        swept = 0
        for key, c in sorted(ledger.claims.items()):
            p = claim_path(key)
            if p not in changes and claim_expired(c, cutoff) and swept < 25:
                changes[p], swept = None, swept + 1
        commit = build_commit(base, changes, message, ident)
        p = run_git(["push", "--porcelain", cfg.remote, f"{commit}:refs/heads/{cfg.branch}"], check=False)
        if p.returncode == 0:
            run_git(["update-ref", cfg.ledger_ref, commit], check=False)
            return commit, ledger, result
        err = (p.stdout + p.stderr).lower()
        if not any(s in err for s in RETRYABLE):
            raise CairnError(f"push to {cfg.remote}/{cfg.branch} failed: {(p.stderr or p.stdout).strip()}")
        WRITE_STATS["retries"] += 1
        time.sleep(min(2.0, 0.05 * (2 ** attempt)) * random.random() + 0.02)
    raise CairnError(f"gave up after {attempts} attempts: the ledger is very busy, try again")


# --------------------------------------------------------------------------- ledger

def new_id(kind):
    n = secrets.randbits(40)
    return f"{KIND_PREFIX[kind]}-" + "".join(CROCKFORD[(n >> (5 * i)) & 31] for i in range(8))


def parse_record(text):
    if not text.startswith("---\n"):
        raise ValueError("missing front matter")
    end = text.find("\n---\n", 4)
    if end < 0:
        if text.rstrip().endswith("\n---"):
            end = text.rstrip().rfind("\n---")
        else:
            raise ValueError("unterminated front matter")
    meta = json.loads(text[4:end])
    if not isinstance(meta, dict):
        raise ValueError("front matter is not an object")
    meta["body"] = text[end + 5:].strip("\n")
    return meta


FIELD_ORDER = ["id", "kind", "title", "created_at", "author", "commit", "areas", "lens", "locs", "claim", "severity",
               "confidence", "evidence", "summary", "assumptions", "depends_on", "repro", "code", "tests", "facets", "coverage", "bench", "env",
               "metrics", "target", "verdict", "method", "priority", "links", "tags"]


def serialize_record(rec):
    meta = {k: rec[k] for k in FIELD_ORDER if k in rec}
    meta.update({k: v for k, v in rec.items() if k not in meta and k != "body"})
    body = (rec.get("body") or "").strip("\n")
    return "---\n" + json.dumps(meta, indent=2, ensure_ascii=False) + "\n---\n" + (body + "\n" if body else "")


def record_path(rec):
    """records/<kind>/<2-char shard>/<id>.md: 1,024 shards per kind keep git tree objects small."""
    return f"records/{rec['kind']}/{rec['id'][2:4]}/{rec['id']}.md"


def claim_path(key):
    return "claims/" + key.replace("/", "--") + ".json"


def dump_json(obj):
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def default_map(top_dirs):
    areas = [{"id": re.sub(r"[^a-z0-9_-]+", "-", d.lower()).strip("-") or "dir", "globs": [f"{d}/**"],
              "description": "", "owners": []} for d in top_dirs]
    areas.append({"id": "root", "globs": ["*"], "description": "Files at the repository root", "owners": []})
    return {"version": 1, "areas": areas, "lenses": json.loads(json.dumps(DEFAULT_LENSES)), "facets": {},
            "benchmarks": [], "glossary": {}}


_glob_cache = {}


def glob_re(g):
    if g not in _glob_cache:
        i, out = 0, ""
        while i < len(g):
            if g.startswith("**/", i):
                out, i = out + "(?:.*/)?", i + 3
            elif g.startswith("**", i):
                out, i = out + ".*", i + 2
            elif g[i] == "*":
                out, i = out + "[^/]*", i + 1
            elif g[i] == "?":
                out, i = out + "[^/]", i + 1
            else:
                out, i = out + re.escape(g[i]), i + 1
        _glob_cache[g] = re.compile(out + r"\Z")
    return _glob_cache[g]


def glob_specificity(g):
    return len(re.split(r"[*?]", g, maxsplit=1)[0])


def as_list(v):
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


CACHE_DIR = None          # set to the git dir by Ctx; parsed blobs are cached there
_BLOB_MEMO = {}           # in-process copy of the cache: blob sha -> ("rec"|"txt"|"err", value)


def _load_blob_cache():
    if _BLOB_MEMO or not CACHE_DIR:
        return
    try:
        with open(os.path.join(CACHE_DIR, "cairn-cache.pickle"), "rb") as f:
            data = pickle.load(f)
        if isinstance(data, dict) and data.get("v") == CACHE_VERSION:
            _BLOB_MEMO.update(data["blobs"])
    except Exception:  # noqa: BLE001 - a bad cache is just a cold cache
        pass


def _save_blob_cache(keep):
    if not CACHE_DIR:
        return
    data = {"v": CACHE_VERSION, "blobs": {k: v for k, v in _BLOB_MEMO.items() if k in keep}}
    try:
        fd, tmp = tempfile.mkstemp(dir=CACHE_DIR, prefix="cairn-cache-")
        with os.fdopen(fd, "wb") as f:
            pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, os.path.join(CACHE_DIR, "cairn-cache.pickle"))
    except OSError:
        pass


def record_shape_problem(rec):
    """The fields every reader relies on. A record without them is reported and left out, never indexed."""
    rid, kind = rec.get("id"), rec.get("kind")
    if not isinstance(rid, str) or not ID_RE.match(rid):
        return f"bad or missing id {rid!r}"
    if kind not in KIND_PREFIX or KIND_PREFIX[kind] != rid[0]:
        return f"bad or missing kind {kind!r} for {rid}"
    if not isinstance(rec.get("author"), dict) or not isinstance(rec["author"].get("github"), str):
        return f"{rid} has no author.github"
    if not isinstance(rec.get("created_at"), str):
        return f"{rid} has no created_at"
    return None


class Ledger:
    """A snapshot of the ledger branch at one commit. Blobs are parsed once and cached by content hash."""

    def __init__(self, commit):
        self.commit = commit
        self.blobs, self.texts = {}, {}
        self.records, self.paths, self.errors, self.claims = {}, {}, [], {}
        if commit:
            self._load()
        self.map_error = None
        try:
            self.map = json.loads(self.texts[MAP_FILE]) if MAP_FILE in self.texts else default_map([])
        except ValueError as e:
            self.map, self.map_error = default_map([]), f"{MAP_FILE}: {e}"
        try:
            self.settings = json.loads(self.texts.get(SETTINGS_FILE) or "{}")
        except ValueError:
            self.settings = {}
        self.guide = self.texts.get(GUIDE_FILE)

    def _load(self):
        out = run_git(["ls-tree", "-r", "-z", self.commit], binary=True).stdout
        entries = []
        for item in out.split(b"\0"):
            if item:
                meta, path = item.split(b"\t", 1)
                _mode, typ, sha = meta.split()
                if typ == b"blob":
                    entries.append((path.decode("utf-8", "replace"), sha.decode()))
        _load_blob_cache()
        missing = [(p, s) for p, s in entries if s not in _BLOB_MEMO]
        if missing:
            data = run_git(["cat-file", "--batch"], input="".join(s + "\n" for _, s in missing).encode(),
                           binary=True).stdout
            pos = 0
            for path, sha in missing:
                nl = data.index(b"\n", pos)
                size = int(data[pos:nl].split()[2])
                text = data[nl + 1:nl + 1 + size].decode("utf-8", "replace")
                pos = nl + 1 + size + 1
                if path.startswith("records/") and path.endswith(".md"):
                    try:
                        _BLOB_MEMO[sha] = ("rec", parse_record(text))
                    except Exception as e:  # noqa: BLE001 - report, don't crash readers
                        _BLOB_MEMO[sha] = ("err", str(e))
                else:
                    _BLOB_MEMO[sha] = ("txt", text)
            _save_blob_cache({s for _, s in entries})
        for path, sha in entries:
            self.blobs[path] = sha
            kind, val = _BLOB_MEMO[sha]
            if kind == "rec":
                rec = dict(val)
                problem = record_shape_problem(rec)
                if problem:
                    self.errors.append(f"{path}: {problem}; ignored")
                    continue
                if rec.get("id") in self.records:
                    self.errors.append(f"{path}: duplicate id {rec.get('id')}")
                self.records[rec.get("id")] = rec
                self.paths[rec.get("id")] = path
            elif kind == "err":
                self.errors.append(f"{path}: {val}")
            else:
                self.texts[path] = val
                if path.startswith("claims/") and path.endswith(".json"):
                    try:
                        c = json.loads(val)
                        self.claims[c["key"]] = c
                    except Exception as e:  # noqa: BLE001
                        self.errors.append(f"{path}: {e}")

    def text(self, path):
        if path in self.texts:
            return self.texts[path]
        return blob_text(self.commit, path) if self.commit else None

    # vocabulary -------------------------------------------------------------
    def areas(self):
        return self.map.get("areas", [])

    def area(self, area_id):
        return next((a for a in self.areas() if a.get("id") == area_id), None)

    def area_ids(self):
        return [a.get("id") for a in self.areas()]

    def lenses(self):
        return self.map.get("lenses", [])

    def lens_ids(self):
        return [l.get("id") for l in self.lenses()]

    def lens(self, lens_id):
        return next((l for l in self.lenses() if l.get("id") == lens_id), None)

    def facets(self):
        return self.map.get("facets", {}) or {}

    def bench(self, bench_id):
        return next((b for b in self.map.get("benchmarks", []) if b.get("id") == bench_id), None)

    def where(self, path):
        """Area ids whose globs match `path`, most specific first."""
        hits = []
        for a in self.areas():
            best = max((glob_specificity(g) for g in a.get("globs", []) if glob_re(g).match(path)), default=-1)
            if best >= 0:
                hits.append((best, a["id"]))
        return [a for _, a in sorted(hits, key=lambda x: -x[0])]

    def of_kind(self, *kinds):
        return [r for r in self.records.values() if r.get("kind") in kinds]


# ------------------------------------------------------------------ similarity

_STEMS = {}
_SUFFIXES = ("ations", "ation", "ings", "ing", "ers", "er", "ed", "es", "ly", "s")


def _stem(w):
    st = _STEMS.get(w)
    if st is None:
        st = w
        if len(w) >= 2 and w not in STOPWORDS:
            for suf in _SUFFIXES:
                if w.endswith(suf) and len(w) - len(suf) >= 4:
                    st = w[: -len(suf)]
                    break
        else:
            st = ""
        _STEMS[w] = st
    return st


@functools.lru_cache(maxsize=300000)
def _tokens(text):
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return tuple(st for st in map(_stem, re.findall(r"[A-Za-z0-9]+", text.lower())) if st)


def tokens(text):
    return list(_tokens(text or ""))


@functools.lru_cache(maxsize=300000)
def _vector(text):
    words = Counter(re.findall(r"[a-z0-9]+", re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text).lower()))
    c = Counter()
    for w, n in words.items():
        st = _STEMS.get(w)
        if st is None:
            st = _stem(w)
        if st:
            c[st] += n
    return c, math.sqrt(sum(v * v for v in c.values()))


def text_cosine(query, text):
    """cosine() of two texts. Each text's vector is built once per process, and a text that contains none of
    the query's stems scores 0 without being tokenized (a stem is a prefix of its word, so this is exact)."""
    cq, nq = _vector(query or "")
    if not nq or not text:
        return 0.0
    low = text.lower()
    if not any(t in low for t in cq):
        return 0.0
    ct, nt = _vector(text)
    if not nt:
        return 0.0
    small, big = (cq, ct) if len(cq) <= len(ct) else (ct, cq)
    return sum(v * big[t] for t, v in small.items() if t in big) / (nq * nt)


def cosine(a, b):
    ca, cb = Counter(a), Counter(b)
    if not ca or not cb:
        return 0.0
    dot = sum(ca[t] * cb[t] for t in ca)
    return dot / (math.sqrt(sum(v * v for v in ca.values())) * math.sqrt(sum(v * v for v in cb.values())))


def record_text(rec, full=False):
    """Title and claim (what dedupe compares); with full=True also tags, symbols, paths and body (for search)."""
    parts = [rec.get("title", ""), rec.get("claim", "")]
    if full:
        parts += [" ".join(as_list(rec.get("tags"))), rec.get("body", "")[:2000]]
        parts += [f"{l.get('path', '')} {l.get('symbol', '')}" for l in rec.get("locs") or []]
    return " ".join(p for p in parts if p)


def parse_lines(s):
    if not s or not LINES_RE.match(str(s)):
        return None
    a, _, b = str(s).partition("-")
    return int(a), int(b or a)


def loc_score(la, lb):
    best = 0.0
    for x in la or []:
        for y in lb or []:
            if not x.get("path") or x.get("path") != y.get("path"):
                continue
            ra, rb = parse_lines(x.get("lines")), parse_lines(y.get("lines"))
            if ra and rb:  # overlapping lines (5-line slack) beat everything else
                s = 1.0 if ra[0] <= rb[1] + 5 and rb[0] <= ra[1] + 5 else 0.4
            elif x.get("symbol") and x.get("symbol") == y.get("symbol"):
                s = 0.8
            else:
                s = 0.6
            best = max(best, s)
    return best


def similarity(a, b):
    """0..1. Weights: title+claim text 0.6, located code 0.25, same area and lens 0.15.

    A different lens on the same area scores 0 for the last part: same code, different concern.
    """
    t = text_cosine(record_text(a), record_text(b))
    p = loc_score(a.get("locs"), b.get("locs"))
    shared = set(as_list(a.get("areas"))) & set(as_list(b.get("areas")))
    la, lb = a.get("lens"), b.get("lens")
    al = 0.0
    if shared:
        al = 1.0 if la and la == lb else (0.5 if not la or not lb else 0.0)
    return round(0.6 * t + 0.25 * p + 0.15 * al, 3)


# --------------------------------------------------------------- derived view

class Derived:
    """Persistent cache of facts derived from git (diffs, counts, greps), valid for one main commit."""

    def __init__(self, cfg, main):
        self.path = os.path.join(cfg.git_dir, "cairn-derived.pickle")
        self.main, self.data, self.dirty = main, {}, False
        try:
            with open(self.path, "rb") as f:
                saved = pickle.load(f)
            if isinstance(saved, dict) and saved.get("main") == main and saved.get("v") == CACHE_VERSION:
                self.data = saved["data"]
        except Exception:  # noqa: BLE001
            pass
        atexit.register(self.save)

    def get(self, key, fn):
        if key not in self.data:
            self.data[key] = fn()
            self.dirty = True
        return self.data[key]

    def save(self):
        if not self.dirty:
            return
        try:
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), prefix="cairn-derived-")
            with os.fdopen(fd, "wb") as f:
                pickle.dump({"v": CACHE_VERSION, "main": self.main, "data": self.data}, f)
            os.replace(tmp, self.path)
            self.dirty = False
        except OSError:
            pass


def norm_lines(lines):
    return "\n".join(l.strip() for l in lines if l.strip())


class Index:
    """Statuses, freshness and coverage derived from an immutable ledger snapshot and the code's history."""

    def __init__(self, ledger, cfg, t=None):
        self.L, self.cfg, self.now = ledger, cfg, t or now()
        self.main = cfg.main_sha()
        self.release = rev_parse(cfg.release_ref) if cfg.release_ref else None
        self.D = Derived(cfg, self.main)
        self.reviews = defaultdict(list)
        self.superseded_by, self.dup_of, self.resolved_by = {}, {}, {}
        for r in sorted(ledger.records.values(), key=lambda r: r.get("created_at", "")):
            links = r.get("links") or {}
            if r.get("kind") == "review":
                self.reviews[r.get("target")].append(r)
            for t_ in as_list(links.get("supersedes")):
                self.superseded_by[t_] = r["id"]
            for t_ in as_list(links.get("resolves")):
                self.resolved_by.setdefault(t_, r["id"])
            if links.get("duplicate_of"):
                self.dup_of[r["id"]] = links["duplicate_of"]
        self.active = {k: c for k, c in ledger.claims.items() if not claim_expired(c, self.now)}
        self._status, self._fresh = {}, {}

    # status ------------------------------------------------------------------
    def status(self, rid):
        if rid in self._status:
            return self._status[rid]
        rec = self.L.records.get(rid)
        if rec is None:
            return "missing"
        author = rec.get("author", {}).get("github", "").lower()
        revs = self.reviews.get(rid, [])
        mine = [v for v in revs if v.get("author", {}).get("github", "").lower() == author]
        others = [v for v in revs if v.get("author", {}).get("github", "").lower() != author]
        if any(v.get("verdict") == "retract" for v in mine):
            st = "retracted"
        elif rec.get("kind") == "task":
            resolver = self.resolved_by.get(rid)
            if resolver and self.status(resolver) not in GONE_STATES:
                st = "done"
            elif f"task/{rid.lower()}" in self.active:
                st = "claimed"
            else:
                st = "open"
        elif any(v.get("verdict") == "fixed" for v in revs):
            st = "fixed"
        elif rid in self.dup_of or any(v.get("verdict") == "duplicate" for v in revs):
            st = "duplicate"
        elif rid in self.superseded_by:
            st = "superseded"
        else:
            conf = {v["author"]["github"].lower() for v in others
                    if v.get("verdict") == "confirms" and v.get("method") in COUNTING_METHODS}
            refu = {v["author"]["github"].lower() for v in others
                    if v.get("verdict") == "refutes" and v.get("method") != "opinion"}
            st = "disputed" if refu and len(refu) >= len(conf) else ("confirmed" if conf else "open")
            if st == "open" and rec.get("kind") != "finding":
                st = "proposed" if rec.get("kind") == "change" else "current"
        self._status[rid] = st
        return st

    # freshness ---------------------------------------------------------------
    def ref_commit(self, rec):
        """The commit and time a record was last verified at: its own, or a later reproducing confirmation."""
        c, t = rec.get("commit"), rec.get("created_at", "")
        # A finding is re-verified by reproducing it; a note describes how code works, so reading the code
        # against it (checked-evidence) is the reproduction.
        methods = COUNTING_METHODS | {"checked-evidence"} if rec.get("kind") == "note" else COUNTING_METHODS
        for v in self.reviews.get(rec["id"], []):
            if not (v.get("verdict") == "confirms" and v.get("method") in methods and v.get("commit")):
                continue
            vt = v.get("created_at", "")
            # Timestamps have one-second resolution and reviews come in no fixed order, so on a tie
            # the commit that is later in history wins.
            if vt > t or (vt == t and (not c or (v["commit"] != c
                                                 and git_ok("merge-base", "--is-ancestor", c, v["commit"])))):
                c, t = v["commit"], vt
        return c, t

    def changes(self, ref):
        """{path at ref: (status, path at main)} for everything that differs, with renames followed."""
        def fn():
            out = run_git(["diff", "--name-status", "-M", "-z", ref, self.main], check=False).stdout.split("\0")
            res, i = {}, 0
            while i < len(out) and out[i]:
                st = out[i]
                if st[0] in "RC":
                    old, new, i = out[i + 1], out[i + 2], i + 3
                else:
                    old = new = out[i + 1]
                    i += 2
                res[old] = (st, new)
            return res
        return self.D.get(("changes", ref), fn)

    def lines_touched(self, ref, path, newpath, rng):
        def fn():
            out = git("diff", "-U0", "-M", ref, self.main, "--", *sorted({path, newpath}))
            hunks = []
            for line in out.splitlines():
                m = re.match(r"@@ -(\d+)(?:,(\d+))? \+", line)
                if m:
                    hunks.append((int(m.group(1)), int(m.group(2)) if m.group(2) is not None else 1))
            return hunks
        for a, b in self.D.get(("hunks", ref, path, newpath), fn):
            if b == 0 and rng[0] <= a < rng[1]:
                return True
            if b and a <= rng[1] and a + b - 1 >= rng[0]:
                return True
        return False

    def callers(self, symbol):
        """Files at main that mention `symbol` as a word, or None if it is too short or too common to track."""
        sym = re.split(r"::|\.", symbol)[-1]
        if len(sym) < 4:
            return None

        def fn():
            p = run_git(["grep", "-l", "-w", "-F", "-e", sym, self.main, "--"], check=False)
            files = [l.split(":", 1)[1] for l in p.stdout.splitlines() if ":" in l]
            return files if len(files) <= 60 else None
        return self.D.get(("callers", sym), fn)

    def package_changed(self, ref, package, changed):
        names = set(self.cfg.lockfiles)
        rx = re.compile(r"(?<![\w-])" + re.escape(package) + r"(?![\w-])")
        for lf in [p for p in changed if os.path.basename(p) in names]:
            diff = self.D.get(("lockdiff", ref, lf), lambda lf=lf: git("diff", "-U3", ref, self.main, "--", lf))
            if any(rx.search(h) for h in diff.split("\n@@")[1:]):
                return True
        return False

    def commits_since(self, ref):
        return self.D.get(("count", ref), lambda: int(git("rev-list", "--count", f"{ref}..{self.main}").strip() or 0))

    def area_commits(self, ref, area_id):
        area = self.L.area(area_id)
        if not area or not ref or not self.main or not commit_exists(ref):
            return None
        specs = [f":(glob){g}" for g in area.get("globs", [])]
        return self.D.get(("area", ref, area_id), lambda: int(
            git("rev-list", "--count", f"{ref}..{self.main}", "--", *specs).strip() or 0))

    def change_freshness(self, rec):
        """For code: does the change still apply to the main branch? Uses git merge-tree, no checkout."""
        code = rec.get("code") or {}
        res = {"verified_at": code.get("base"), "verified_on": rec.get("created_at", ""), "state": "unknown",
               "signals": []}
        base = code.get("base")
        if not self.main or not base or not commit_exists(base):
            res["signals"].append({"kind": "unknown", "detail": "base commit or main branch not available locally"})
            return res
        sig = res["signals"]
        touched = {l.get("path") for l in rec.get("locs") or []}
        ch = self.changes(base)
        moved_under = sorted(p for p in touched if p in ch)
        if moved_under:
            head = change_commit(self, rec)
            if not head:
                sig.append({"kind": "unknown", "detail": "the change's code is not available (no branch, no patch)"})
            else:
                clean, info = self.D.get(("merge", base, head), lambda: merge_check(base, self.main, head))
                if clean is None:
                    sig.append({"kind": "unknown", "detail": info})
                elif clean:
                    sig.append({"kind": "drift", "detail": f"{self.cfg.main_branch} changed {len(moved_under)} touched "
                                f"file(s) but the change still applies cleanly: `cairn work refresh {rec['id']}`"})
                else:
                    sig.append({"kind": "conflict", "detail": "conflicts with " + self.cfg.main_branch + " in "
                                + ", ".join(info[:5]) + (" …" if len(info) > 5 else "")})
        try:
            days = (self.now - parse_ts(rec.get("created_at", ""))).days
        except ValueError:
            days = 0
        if days > self.cfg.freshness.get("max_days", 30):
            sig.append({"kind": "age", "detail": f"made {days} days ago"})
        res["commits"], res["days"] = self.commits_since(base), days
        kinds = {x["kind"] for x in sig}
        res["state"] = ("unknown" if kinds == {"unknown"} else "stale" if "conflict" in kinds else
                        "expired" if "age" in kinds else "watch" if "drift" in kinds else "fresh")
        return res

    def freshness(self, rec):
        """How much to trust a record now. state: fresh, watch, expired, stale, gone or unknown, with signals."""
        rid = rec["id"]
        if rid in self._fresh:
            return self._fresh[rid]
        if rec.get("kind") == "change":
            self._fresh[rid] = self.change_freshness(rec)
            return self._fresh[rid]
        ref, ts = self.ref_commit(rec)
        res = {"verified_at": ref, "verified_on": ts, "state": "unknown", "signals": []}
        self._fresh[rid] = res
        if not ref or not self.main:
            res["signals"].append({"kind": "unknown", "detail": "no commit or no main branch to compare with"})
            return res
        if not commit_exists(ref):
            res["signals"].append({"kind": "unknown", "detail": f"commit {ref[:7]} is not in local history"})
            return res
        sig = res["signals"]
        ch = self.changes(ref)
        changed = set(ch) | {new for _, new in ch.values()}
        located = set()
        for loc in rec.get("locs") or []:
            p = loc.get("path")
            if not p:
                continue
            located.add(p)
            if p not in ch:
                continue
            st, newp = ch[p]
            located.add(newp)
            if st == "D":
                sig.append({"kind": "gone", "detail": f"{p} was deleted"})
                continue
            if st[0] == "R":
                sig.append({"kind": "moved", "detail": f"{p} moved to {newp}"})
                if st == "R100":
                    continue
            rng = parse_lines(loc.get("lines"))
            if rng and self.lines_touched(ref, p, newp, rng):
                sig.append({"kind": "lines", "detail": f"{p}:{loc['lines']} changed"})
            elif rng:
                sig.append({"kind": "file", "detail": f"{p} changed outside lines {loc['lines']}"})
            else:
                sig.append({"kind": "file", "detail": f"{p} changed"})
        syms = {l.get("symbol") for l in rec.get("locs") or [] if l.get("symbol")}
        for d in rec.get("depends_on") or []:
            if d.get("path"):
                rx = glob_re(d["path"])
                if any(rx.match(c) for c in changed):
                    sig.append({"kind": "depends", "detail": f"dependency {d['path']} changed"})
            if d.get("package") and self.package_changed(ref, d["package"], changed):
                sig.append({"kind": "package", "detail": f"package {d['package']} changed in a lockfile"})
            if d.get("symbol"):
                syms.add(d["symbol"])
        for s in sorted(syms):
            files = self.callers(s)
            touched = [f for f in (files or []) if f in changed and f not in located]
            if touched:
                sig.append({"kind": "callers", "detail": f"{len(touched)} file(s) using {s} changed"})
        fr = self.cfg.freshness
        try:
            days = (self.now - parse_ts(ts)).days
        except ValueError:
            days = 0
        if days > fr.get("max_days", 30):
            sig.append({"kind": "age", "detail": f"verified {days} days ago"})
        for a in as_list(rec.get("areas")):
            n = self.area_commits(ref, a)
            limit = (self.L.area(a) or {}).get("max_commits", fr.get("max_area_commits", 50))
            if n is not None and n > limit:
                sig.append({"kind": "churn", "detail": f"{n} commits in {a} since"})
                break
        res["commits"], res["days"] = self.commits_since(ref), days
        kinds = {s["kind"] for s in sig}
        res["state"] = ("gone" if "gone" in kinds else "stale" if "lines" in kinds else
                        "expired" if kinds & {"age", "churn"} else
                        "watch" if kinds & {"file", "depends", "package", "callers"} else "fresh")
        return res

    def release_presence(self, rec):
        """Is the located code in the latest release? present, absent, changed or unknown (None if no release)."""
        if not self.release:
            return None
        ref, _ = self.ref_commit(rec)

        def fn():
            results = []
            for loc in rec.get("locs") or []:
                p = loc.get("path")
                a = blob_text(ref, p) if p and ref else None
                if a is None:
                    continue
                b = blob_text(self.release, p)
                if b is None:
                    results.append("absent")
                    continue
                rng = parse_lines(loc.get("lines"))
                if rng:
                    snippet = norm_lines(a.splitlines()[rng[0] - 1:rng[1]])
                    results.append("present" if snippet and snippet in norm_lines(b.splitlines()) else "changed")
                else:
                    results.append("present" if a == b else "changed")
            if not results:
                return "unknown"
            return "present" if "present" in results else ("absent" if set(results) == {"absent"} else "changed")
        return self.D.get(("release", self.release, rec["id"], ref), fn)

    # coverage ----------------------------------------------------------------
    def coverage(self, areas=None):
        cells = {}
        recs = sorted(self.L.records.values(), key=lambda r: r.get("created_at", ""))
        for r in recs:
            if r.get("kind") != "report" or self.status(r["id"]) in GONE_STATES:
                continue
            for c in r.get("coverage") or []:
                prev = cells.get((c.get("area"), c.get("lens")))
                if prev and prev["commit"] == r.get("commit") and prev["depth"] in DEPTHS and c.get("depth") in DEPTHS \
                        and DEPTHS.index(prev["depth"]) > DEPTHS.index(c["depth"]):
                    continue   # a second, shallower look at the same commit adds a report, not a downgrade
                cells[(c.get("area"), c.get("lens"))] = {
                    "area": c.get("area"), "lens": c.get("lens"), "depth": c.get("depth"), "result": c.get("result"),
                    "commit": r.get("commit"), "by": r.get("author", {}).get("github"), "at": r.get("created_at"),
                    "record": r["id"]}
                if c.get("not_examined"):
                    cells[(c.get("area"), c.get("lens"))]["not_examined"] = c["not_examined"]
        live = Counter()
        for r in recs:
            if r.get("kind") == "finding" and self.status(r["id"]) in LIVE_STATES:
                for a in as_list(r.get("areas")):
                    live[(a, r.get("lens"))] += 1
                    if (a, r.get("lens")) not in cells:
                        cells[(a, r.get("lens"))] = {"area": a, "lens": r.get("lens"), "depth": "spot",
                                                     "result": "issues", "commit": r.get("commit"),
                                                     "by": r.get("author", {}).get("github"),
                                                     "at": r.get("created_at"), "record": r["id"]}
        out = {}
        for key, cell in cells.items():
            if areas and key[0] not in areas:
                continue
            cell["live_findings"] = live[key]
            cell["commits_since"] = self.area_commits(cell["commit"], cell["area"])
            out[key] = cell
        return out


def claim_expired(c, t=None):
    try:
        return parse_ts(c["expires_at"]) <= (t or now())
    except (KeyError, ValueError):
        return True


def claim_areas(c):
    parts = c.get("key", "").split("/")
    areas, lens = set(as_list(c.get("areas"))), c.get("lens")
    if parts[0] == "audit" and len(parts) >= 3:
        areas.add(parts[1])
        lens = lens or parts[2]
    return areas, lens


# ------------------------------------------------------------------- validation

def _str(v, n=500):
    return isinstance(v, str) and 0 < len(v.strip()) and len(v) <= n


def _need(rec, field, n, what):
    """None if rec[field] is a non-empty string of at most n characters, else a message that says which rule broke."""
    v = rec.get(field)
    if not isinstance(v, str) or not v.strip():
        return f"{what} is missing: add \"{field}\" (up to {n} characters)"
    if len(v) > n:
        return f"{what} is {len(v)} characters; the limit for \"{field}\" is {n}"
    return None


def runner_regex(pattern):
    parts = re.split(r"(\{[a-z_]+\})", " ".join(pattern.split()))
    return re.compile("".join(PLACEHOLDER if re.fullmatch(r"\{[a-z_]+\}", p) else re.escape(p) for p in parts) + r"\Z")


def repro_allowed(cmd, runners):
    cmd = " ".join(str(cmd).split())
    if re.search(r"[;|&$<>`\\\n]", cmd):
        return False
    return any(runner_regex(p).match(cmd) for p in runners)


def validate_record(rec, ledger=None, runners=None):
    """Problems with a record. With `ledger`, check vocabulary against its map; with `runners`, check `repro`."""
    e = []
    rid, kind = rec.get("id"), rec.get("kind")
    if not (isinstance(rid, str) and ID_RE.match(rid)):
        e.append(f"bad id {rid!r}")
    if kind not in KIND_PREFIX:
        return e + [f"bad kind {kind!r}"]
    if isinstance(rid, str) and rid[:1] != KIND_PREFIX[kind]:
        e.append(f"id prefix does not match kind {kind}")
    e += [m for m in [_need(rec, "title", 200, "title")] if m]
    if not (isinstance(rec.get("created_at"), str) and TS_RE.match(rec["created_at"])):
        e.append("created_at must be UTC like 2026-11-20T14:02:11Z")
    a = rec.get("author")
    if not (isinstance(a, dict) and _str(a.get("github"), 100)):
        e.append("author.github is required")
    if rec.get("commit") is not None and not (isinstance(rec["commit"], str) and SHA_RE.match(rec["commit"])):
        e.append("commit must be a hex sha")
    if kind != "task" and not rec.get("commit"):
        e.append("commit is required: the code sha this work examined")
    areas = rec.get("areas", [])
    if not isinstance(areas, list) or not all(isinstance(x, str) for x in areas):
        e.append("areas must be a list of area ids")
        areas = []
    lens = rec.get("lens")
    for loc in rec.get("locs") or []:
        if not (isinstance(loc, dict) and _str(loc.get("path"))) or loc["path"].startswith("/"):
            e.append(f"bad loc {loc!r}: needs a repo-relative path")
        elif loc.get("lines") is not None and not LINES_RE.match(str(loc["lines"])):
            e.append(f"bad lines {loc.get('lines')!r}: use '41' or '41-48'")
    if not isinstance(rec.get("links", {}), dict):
        e.append("links must be an object")
    for k in ("resolves", "supersedes", "duplicate_of", "addresses", "cites"):
        for t in as_list((rec.get("links") or {}).get(k)):
            if not (isinstance(t, str) and ID_RE.match(t)):
                e.append(f"links.{k} has bad id {t!r}")
    if "assumptions" in rec and not (isinstance(rec["assumptions"], list)
                                     and all(_str(x, 300) for x in rec["assumptions"])):
        over = [len(x) for x in rec["assumptions"] if isinstance(x, str) and len(x) > 300] \
            if isinstance(rec["assumptions"], list) else []
        e.append("assumptions must be a list of short statements (max 300 chars each)"
                 + (f"; yours has one of {max(over)} characters" if over else ""))
    for d in rec.get("depends_on") or []:
        if not isinstance(d, dict) or not any(_str(d.get(k), 300) for k in ("path", "symbol", "package")):
            e.append(f"depends_on entries need a path, symbol or package: {d!r}")
    facets = rec.get("facets", {})
    if not isinstance(facets, dict) or not all(isinstance(v, list) and all(isinstance(x, str) for x in v)
                                              for v in facets.values()):
        e.append("facets must map a facet id to a list of values")
        facets = {}
    repro = rec.get("repro")
    if repro is not None:
        if not (isinstance(repro, dict) and _str(repro.get("run"), 300) and repro.get("expect") in ("fail", "pass")):
            e.append('repro must look like {"run": "<command>", "expect": "fail" or "pass"}')
        elif runners and not repro_allowed(repro["run"], runners):
            e.append(f"repro command is not an allowed runner for this ledger; allowed: {runners}")

    if kind == "finding":
        if not areas:
            e.append("finding needs areas (or locs that map to an area)")
        if not lens:
            e.append("finding needs a lens")
        e += [m for m in [_need(rec, "claim", 600, "the finding's one-sentence claim")] if m]
        if rec.get("severity") not in SEVERITIES:
            e.append(f"severity must be one of {SEVERITIES}")
        if rec.get("confidence") not in CONFIDENCE:
            e.append(f"confidence must be one of {CONFIDENCE}")
        ev = rec.get("evidence")
        if not isinstance(ev, list) or not ev:
            e.append("finding needs at least one evidence item")
        else:
            for item in ev:
                if not isinstance(item, dict) or item.get("type") not in EVIDENCE_TYPES:
                    e.append(f"evidence items need type in {EVIDENCE_TYPES} and a detail")
                else:
                    e += [m for m in [_need(item, "detail", 2000, f"evidence ({item['type']}) detail")] if m]
    elif kind == "report":
        cov = rec.get("coverage", [])
        if not isinstance(cov, list) or (not cov and not (rec.get("links") or {}).get("cites")):
            e.append("report needs coverage entries (or links.cites)")
        for c in cov if isinstance(cov, list) else []:
            if not isinstance(c, dict) or c.get("depth") not in DEPTHS or c.get("result") not in RESULTS \
                    or not c.get("area") or not c.get("lens"):
                e.append(f"coverage entries need area, lens, depth in {DEPTHS}, result in {RESULTS}")
    elif kind == "benchmark":
        if not _str(rec.get("bench"), 100):
            e.append("benchmark needs bench (a registered id)")
        env = rec.get("env")
        if not (isinstance(env, dict) and _str(env.get("class"), 100)):
            e.append("benchmark needs env.class (see `cairn env`)")
        mets = rec.get("metrics")
        if not isinstance(mets, list) or not mets:
            e.append("benchmark needs metrics")
        for m in mets if isinstance(mets, list) else []:
            ok = isinstance(m, dict) and _str(m.get("name"), 100) and m.get("better") in ("lower", "higher") \
                and isinstance(m.get("samples"), list) and m["samples"] \
                and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in m["samples"])
            if not ok:
                e.append("metrics need name, unit, better (lower|higher) and numeric samples")
    elif kind == "review":
        if not (isinstance(rec.get("target"), str) and ID_RE.match(rec["target"])):
            e.append("review needs a target record id")
        if rec.get("verdict") not in VERDICTS:
            e.append(f"verdict must be one of {VERDICTS}")
        if rec.get("verdict") in ("confirms", "refutes") and rec.get("method") not in METHODS:
            e.append(f"confirms/refutes need method in {METHODS}")
    elif kind == "task":
        if rec.get("priority") not in PRIORITIES:
            e.append(f"priority must be one of {PRIORITIES}")
    elif kind == "note":
        if not areas:
            e.append("note needs areas: the part of the repo it explains")
        e += [m for m in [_need(rec, "summary", 800, "the note's summary (one paragraph agents read first)"),
                          _need(rec, "body", 8000, "the note's body (entry points, data flow, invariants, gotchas, "
                                                   "how to test)")] if m]
    elif kind == "change":
        code = rec.get("code")
        if not (isinstance(code, dict) and isinstance(code.get("commit"), str) and SHA_RE.match(code["commit"])
                and isinstance(code.get("base"), str) and SHA_RE.match(code["base"])
                and (code.get("patch") is True or _str(code.get("branch"), 200))):
            e.append("change needs code {commit, base} and a stored patch or a branch (use `cairn work submit`)")
        if not as_list((rec.get("links") or {}).get("addresses")):
            e.append("change needs links.addresses: the finding or task ids it addresses")
        for t in rec.get("tests") or []:
            if not (isinstance(t, dict) and _str(t.get("run"), 300) and t.get("result") in ("pass", "fail")):
                e.append('tests entries look like {"run": "<command>", "result": "pass" or "fail"}')

    if ledger is not None:
        known = set(ledger.area_ids())
        for x in areas:
            if x not in known:
                e.append(f"unknown area {x!r}; see `cairn map` or propose it with `cairn map add area`")
        lenses = set(ledger.lens_ids())
        if lens and lens not in lenses:
            e.append(f"unknown lens {lens!r}; this ledger's lenses: {', '.join(sorted(lenses))}")
        for c in rec.get("coverage") or []:
            if isinstance(c, dict) and (c.get("area") not in known or c.get("lens") not in lenses):
                e.append(f"coverage uses unknown area/lens {c.get('area')}/{c.get('lens')}")
        defined = ledger.facets()
        for f, vals in facets.items():
            if f not in defined:
                e.append(f"unknown facet {f!r}; this ledger's facets: {', '.join(sorted(defined)) or 'none'}")
            elif defined[f].get("values"):
                bad = [v for v in vals if v not in defined[f]["values"]]
                if bad:
                    e.append(f"facet {f} has unknown values {bad}; allowed: {defined[f]['values']}")
        if kind == "benchmark" and rec.get("bench") and not ledger.bench(rec["bench"]):
            e.append(f"unknown benchmark {rec['bench']!r}; register it with `cairn map add benchmarks`")
        if kind == "review" and rec.get("target") and rec["target"] not in ledger.records:
            e.append(f"review target {rec['target']} does not exist")
        for t in as_list((rec.get("links") or {}).get("addresses")):
            if t not in ledger.records:
                e.append(f"links.addresses: {t} does not exist")
    return e


def scan_secrets(text):
    return sorted({label for rx, label in SECRET_PATTERNS if rx.search(text)})


def validate_claim(c, path):
    e = []
    if not isinstance(c, dict) or not isinstance(c.get("key"), str) or not KEY_RE.match(c["key"]):
        return [f"{path}: bad claim key"]
    if claim_path(c["key"]) != path:
        e.append(f"{path}: file name does not match key {c['key']}")
    if not _str((c.get("holder") or {}).get("github"), 100):
        e.append(f"{path}: holder.github missing")
    for k in ("acquired_at", "expires_at"):
        if not (isinstance(c.get(k), str) and TS_RE.match(c[k])):
            e.append(f"{path}: {k} missing or malformed")
    return e


def validate_map(m):
    e = []
    if not isinstance(m, dict):
        return ["map.json must be an object"]
    ids = set()
    for a in m.get("areas", []):
        if not (isinstance(a, dict) and isinstance(a.get("id"), str) and SLUG_RE.match(a["id"])):
            e.append(f"area needs a lowercase id: {a!r}")
            continue
        if a["id"] in ids:
            e.append(f"duplicate area {a['id']}")
        ids.add(a["id"])
        if not a.get("globs") or not all(isinstance(g, str) and g for g in a["globs"]):
            e.append(f"area {a['id']} needs globs")
        if "max_commits" in a and not isinstance(a["max_commits"], int):
            e.append(f"area {a['id']}: max_commits must be an integer")
    lens_ids = set()
    for l in m.get("lenses", []):
        if not (isinstance(l, dict) and isinstance(l.get("id"), str) and SLUG_RE.match(l["id"])):
            e.append(f"lens needs a lowercase id: {l!r}")
            continue
        if l["id"] in lens_ids:
            e.append(f"duplicate lens {l['id']}")
        lens_ids.add(l["id"])
        if "sensitive" in l and not isinstance(l["sensitive"], bool):
            e.append(f"lens {l['id']}: sensitive must be true or false")
    facets = m.get("facets", {})
    if not isinstance(facets, dict):
        e.append("facets must be an object of facet id: {description, values}")
    else:
        for fid, f in facets.items():
            if not SLUG_RE.match(fid) or not isinstance(f, dict):
                e.append(f"facet {fid!r} needs a lowercase id and an object")
            elif "values" in f and not (isinstance(f["values"], list) and all(isinstance(v, str) for v in f["values"])):
                e.append(f"facet {fid}: values must be a list of strings")
    for b in m.get("benchmarks", []):
        if not (isinstance(b, dict) and isinstance(b.get("id"), str) and SLUG_RE.match(b["id"]) and b.get("command")
                and isinstance(b.get("metrics"), list)):
            e.append(f"benchmark needs id, command and metrics: {b!r}")
    if not isinstance(m.get("glossary", {}), dict):
        e.append("glossary must be an object of term: definition")
    return e


def validate_settings(s):
    e = []
    if not isinstance(s, dict):
        return ["ledger.json must be an object"]
    types = {"main_remote": str, "main_branch": str, "release_branch": (str, type(None)), "code_repo": (str, type(None)),
             "public": bool, "bots": list, "freshness": dict, "lockfiles": list, "repro_runners": list,
             "repro_timeout": int, "shallow_fetch": bool, "work_remote": (str, type(None)), "patch_max_bytes": int,
             "context_budget": dict, "context_min_severity": str, "context_holdout": (int, float), "latest_version": (str, type(None))}
    for k, v in s.items():
        if k not in types:
            e.append(f"unknown setting {k!r}; known: {', '.join(sorted(types))}")
        elif not isinstance(v, types[k]):
            e.append(f"setting {k} has the wrong type")
    if isinstance(s.get("context_min_severity"), str) and s["context_min_severity"] not in SEVERITIES:
        e.append(f"context_min_severity must be one of {SEVERITIES}")
    for k, v in (s.get("freshness") or {}).items():
        if k not in DEFAULT_SETTINGS["freshness"] or not isinstance(v, int):
            e.append(f"freshness.{k} must be one of {sorted(DEFAULT_SETTINGS['freshness'])} with an integer value")
    return e


# ------------------------------------------------------------------ formatting

def sev_rank(r):
    """Severity as a number; records pushed around the CLI may carry junk, so never raise."""
    sev = r.get("severity", "info")
    return SEVERITIES.index(sev) if sev in SEVERITIES else -1


def short(sha):
    return (sha or "-------")[:7]


def loc_str(rec):
    locs = rec.get("locs") or []
    if not locs:
        return ""
    l0 = locs[0]
    s = l0.get("path", "") + (f":{l0['lines']}" if l0.get("lines") else "")
    return s + (f" (+{len(locs) - 1})" if len(locs) > 1 else "")


def fresh_str(fr):
    if not fr or fr["state"] == "fresh":
        return ""
    if fr["state"] == "unknown":
        return "FRESHNESS?"
    strongest = {"gone": ["gone"], "stale": ["lines", "conflict"], "expired": ["churn", "age"],
                 "watch": ["file", "depends", "package", "callers", "drift"]}[fr["state"]]
    detail = next((s["detail"] for k in strongest for s in fr["signals"] if s["kind"] == k), "")
    return f"{fr['state'].upper()}: {detail}"


def rec_line(ix, r, score=None):
    st = ix.status(r["id"])
    extra = [r.get("severity", "")] if r["kind"] == "finding" else ([r.get("priority", "")] if r["kind"] == "task" else [])
    scope = "/".join(x for x in [",".join(as_list(r.get("areas"))), r.get("lens") or ""] if x)
    flag = fresh_str(ix.freshness(r)) if r["kind"] in ("finding", "report", "change") and st in LIVE_STATES else ""
    head = f"{r['id']}  {st:<10} " + (f"{score:.2f}  " if score is not None else "")
    tail = "  ".join(x for x in [f"[{scope}]" if scope else "", loc_str(r), flag] if x)
    return f"  {head}{' '.join(x for x in extra if x):<8} {r.get('title', '')}  {tail}".rstrip()


def claim_line(c, t):
    h = c.get("holder", {})
    left = human_delta((parse_ts(c["expires_at"]) - t).total_seconds())
    return f"  {c['key']:<34} {h.get('github')} ({h.get('agent', '?')})  \"{c.get('intent', '')}\"  expires in {left}"


def rec_summary(ix, r, fresh=True):
    d = {k: r.get(k) for k in ("id", "kind", "title", "areas", "lens", "severity", "priority", "facets", "created_at")
         if r.get(k) is not None}
    d["author"] = r.get("author", {}).get("github")
    d["status"] = ix.status(r["id"])
    if r.get("locs"):
        d["locs"] = r["locs"]
    if fresh and r["kind"] in ("finding", "report", "change"):
        d["freshness"] = ix.freshness(r)
        rel = ix.release_presence(r)
        if rel:
            d["in_release"] = rel
    return d


# --------------------------------------------------------------------- commands

class Ctx:
    def __init__(self, args):
        global CACHE_DIR
        self.args = args
        self.cfg = Config(offline=getattr(args, "offline", False))
        CACHE_DIR = self.cfg.git_dir
        self.ident = Identity(self.cfg)
        self._ledger = None

    def ledger(self, full=False):
        if self._ledger is None:
            self._ledger = Ledger(fetch(self.cfg, full=full))
        return self._ledger

    def require_ledger(self, full=False):
        L = self.ledger(full)
        if not L.commit:
            raise CairnError(f"no ledger at {self.cfg.remote}/{self.cfg.branch}; run `cairn init` first")
        return L


def scope_areas(L, areas, paths):
    out = list(areas or [])
    for p in paths or []:
        w = L.where(p)
        if w and w[0] not in out:
            out.append(w[0])
    return out


def in_scope(r, areas, lens=None, facets=None):
    if areas and not set(as_list(r.get("areas"))) & set(areas):
        return False
    if lens and r.get("lens") != lens:
        return False
    for f in facets or []:
        k, _, v = f.partition("=")
        if v not in (r.get("facets") or {}).get(k, []):
            return False
    return True


def load_profile(path):
    """A profile is a directory with map.json and optionally ledger.json (settings) and GUIDE.md."""
    files = {}
    try:
        with open(os.path.join(path, MAP_FILE), encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError) as e:
        raise CairnError(f"cannot read {path}/{MAP_FILE}: {e}")
    problems = validate_map(m)
    if problems:
        raise CairnError("profile map rejected: " + "; ".join(problems))
    files[MAP_FILE] = dump_json(m)
    sp = os.path.join(path, SETTINGS_FILE)
    if os.path.exists(sp):
        with open(sp, encoding="utf-8") as f:
            s = json.load(f)
        problems = validate_settings(s)
        if problems:
            raise CairnError("profile settings rejected: " + "; ".join(problems))
        files[SETTINGS_FILE] = dump_json(s)
    gp = os.path.join(path, GUIDE_FILE)
    if os.path.exists(gp):
        with open(gp, encoding="utf-8") as f:
            files[GUIDE_FILE] = f.read()
    return files


def cmd_init(ctx):
    cfg, args = ctx.cfg, ctx.args
    if args.profile:
        files = load_profile(args.profile)
    elif args.map:
        files = load_profile(os.path.dirname(os.path.abspath(args.map))) if os.path.basename(args.map) == MAP_FILE \
            else None
        if files is None:
            with open(args.map, encoding="utf-8") as f:
                m = json.load(f)
            if validate_map(m):
                raise CairnError("map rejected: " + "; ".join(validate_map(m)))
            files = {MAP_FILE: dump_json(m)}
    else:
        tops = []
        if rev_parse("HEAD"):
            tops = [d for d in git("ls-tree", "-d", "--name-only", "HEAD").splitlines() if d and not d.startswith(".")]
        files = {MAP_FILE: dump_json(default_map(tops))}
    files.setdefault(SETTINGS_FILE, dump_json({}))
    files["README.md"] = "# Cairn ledger\n\nRun `cairn render` for the dashboard; the daily job commits it here.\n"

    def mutate(L):
        if L.commit:
            raise Blocked(f"a ledger already exists at {cfg.remote}/{cfg.branch} ({short(L.commit)})")
        return dict(files)

    commit, _, _ = write(cfg, ctx.ident, mutate, "cairn: initialise ledger")
    m = json.loads(files[MAP_FILE])
    text = (f"Created {cfg.remote}/{cfg.branch} at {short(commit)}.\n"
            f"Areas: {', '.join(a['id'] for a in m['areas'])}.\n"
            f"Lenses: {', '.join(l['id'] for l in m['lenses'])}.\n"
            f"Next: check `cairn map check`, adjust `cairn settings`, and give agents the skill.")
    return {"commit": commit, "branch": cfg.branch, "files": sorted(files)}, text, EXIT_OK


def cmd_whoami(ctx):
    ident, args = ctx.ident, ctx.args
    if args.user:
        git("config", "--local", "cairn.user", args.user)
        ident.user = args.user
    if args.agent:
        ident.data["agent"] = args.agent
        ident.agent = args.agent
    if args.new_session:
        ident.data["session"] = "s-" + secrets.token_hex(4)
        ident.session = ident.data["session"]
    if args.agent or args.new_session:
        ident.save()
    d = ident.author()
    text = f"user {d['github']} · agent {d['agent']} · session {d['session']}"
    env_agent = os.environ.get("CAIRN_AGENT")
    if args.agent and env_agent and env_agent != args.agent:
        text += f"\nnote: CAIRN_AGENT is set to `{env_agent}` in this environment and overrides this; change or unset it."
    elif not AGENT_RE.match(d["agent"] or ""):
        text += f"\nnote: set the agent with {AGENT_HOWTO}."
    return d, text, EXIT_OK


GUIDE_MAX = 20000   # characters: agents are asked to read the whole guide, so it has to stay short


def cmd_guide(ctx):
    args = ctx.args
    if args.action == "set":
        if not args.file:
            raise CairnError("usage: cairn guide set <file>   (- reads standard input)")
        try:
            text = sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read()
        except (OSError, UnicodeDecodeError) as e:
            raise CairnError(f"cannot read {args.file}: {e}")
        if not text.strip():
            raise CairnError("the guide is empty; nothing was changed")
        if len(text) > GUIDE_MAX:
            raise CairnError(f"the guide is {len(text)} characters; keep it under {GUIDE_MAX} (every agent reads all of it)")
        text = text.rstrip() + "\n"

        def mutate(L):
            if not L.commit:
                raise CairnError("no ledger yet; run `cairn init`")
            if L.guide == text:
                raise Blocked("the ledger's guide already has this text")
            return {GUIDE_FILE: text}, None

        commit, _, _ = write(ctx.cfg, ctx.ident, mutate, "cairn: guide")
        return {"guide": text, "ledger": commit}, f"UPDATED {GUIDE_FILE} (ledger {short(commit)})", EXIT_OK
    L = ctx.require_ledger()
    if not L.guide:
        return {"guide": None}, "This ledger has no project guide. The skill's general rules apply.", EXIT_OK
    return {"guide": L.guide}, L.guide.rstrip(), EXIT_OK


def cmd_settings(ctx):
    args = ctx.args
    if args.action == "set":
        if not args.key or args.value is None:
            raise CairnError("usage: cairn settings set <key> <json value>, e.g. cairn settings set main_branch '\"dev\"'")
        try:
            value = json.loads(args.value)
        except (TypeError, ValueError):
            value = args.value

        def mutate(L):
            s = dict(L.settings)
            if args.key == "freshness" or args.key.startswith("freshness."):
                sub = args.key.partition(".")[2]
                s["freshness"] = {**s.get("freshness", {}), **({sub: value} if sub else value)}
            else:
                s[args.key] = value
            problems = validate_settings(s)
            if problems:
                raise CairnError("; ".join(problems))
            return {SETTINGS_FILE: dump_json(s)}, s

        commit, _, s = write(ctx.cfg, ctx.ident, mutate, f"cairn: settings {args.key}")
        return s, f"UPDATED {args.key} in {SETTINGS_FILE} (ledger {short(commit)})", EXIT_OK
    L = ctx.require_ledger()
    eff = ctx.cfg.settings
    lines = [f"{k}: {json.dumps(eff[k])}" + ("" if k in L.settings else "  (default)") for k in sorted(eff)]
    return {"ledger": L.settings, "effective": eff}, "\n".join(lines), EXIT_OK


def cmd_brief(ctx):
    L = ctx.require_ledger()
    ix, args, t = Index(L, ctx.cfg), ctx.args, now()
    areas = scope_areas(L, args.area, args.path)
    lines = [f"Cairn ledger {ctx.cfg.remote}/{ctx.cfg.branch} @ {short(L.commit)} · {len(L.records)} records · "
             f"{len(ix.active)} active claims · {ctx.cfg.main_branch} @ {short(ix.main)}"]
    if L.guide:
        lines.append("Project guide: run `cairn guide` before your first task here.")
    if L.errors:
        lines.append(f"  ! {len(L.errors)} unreadable files; run `cairn validate`")
    if update_notice(ctx.cfg.settings):
        lines.append(f"  ! this is Cairn {VERSION}; the ledger is maintained with {ctx.cfg.settings['latest_version']}. "
                     f"Update: `git pull` in your clone of the Cairn kit, then `./install.sh` with your assistants' names")
    if areas:
        lines.append(f"Scope: area {', '.join(areas)}")
    else:
        lines.append("\nAreas (use these ids)")
        for a in L.areas()[:40]:
            desc = f" - {a['description']}" if a.get("description") else ""
            lines.append(f"  {a['id']:<20} {', '.join(a.get('globs', [])[:3])}{' …' if len(a.get('globs', [])) > 3 else ''}{desc}")
        lines.append("Lenses: " + ", ".join(L.lens_ids()))
        if L.facets():
            lines.append("Facets: " + ", ".join(f"{k} ({', '.join(v.get('values', [])[:6])}{' …' if len(v.get('values', [])) > 6 else ''})"
                                                for k, v in L.facets().items()))
    claims = [c for c in ix.active.values() if not areas or claim_areas(c)[0] & set(areas)]
    lines.append(f"\nActive claims ({len(claims)})")
    lines += [claim_line(c, t) for c in sorted(claims, key=lambda c: c["key"])] or ["  none"]
    tasks = [r for r in L.of_kind("task") if ix.status(r["id"]) in ("open", "claimed") and in_scope(r, areas)]
    tasks.sort(key=lambda r: (r.get("priority", "p9"), r.get("created_at", "")))
    lines.append(f"\nOpen tasks ({len(tasks)})")
    lines += [rec_line(ix, r) for r in tasks[:10]] or ["  none"]
    finds = [r for r in L.of_kind("finding") if in_scope(r, areas)]
    counts = Counter(ix.status(r["id"]) for r in finds)
    live = sorted([r for r in finds if ix.status(r["id"]) in LIVE_STATES],
                  key=lambda r: (-sev_rank(r), r.get("created_at", "")))
    lines.append("\nFindings (" + (" · ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "none") + ")")
    lines += [rec_line(ix, r) for r in live[:15]]
    if len(live) > 15:
        lines.append(f"  … {len(live) - 15} more: cairn status --kind finding" + (f" --area {areas[0]}" if areas else ""))
    cov = ix.coverage(areas or None)
    lines.append(f"\nCoverage ({len(cov)} area × lens cells examined)")
    for (a, l), c in sorted(cov.items()):
        since = "?" if c["commits_since"] is None else c["commits_since"]
        lines.append(f"  {a + ' × ' + str(l):<30} {c['depth']}, {c['result']}, {c['live_findings']} live findings · "
                     f"{c['by']} {ago(c['at'], t)} at {short(c['commit'])} · {since} commits since")
    if not cov:
        lines.append("  none yet")
    hint_area = areas[0] if areas else "<area>"
    lines.append(f"\nBefore starting work: cairn check \"<intent>\" --area {hint_area} --lens <lens>")
    data = {"ledger": L.commit, "main": ix.main, "scope": areas, "has_guide": bool(L.guide),
            "claims": list(claims), "tasks": [rec_summary(ix, r, fresh=False) for r in tasks],
            "finding_counts": dict(counts), "findings": [rec_summary(ix, r) for r in live[:50]],
            "coverage": list(cov.values())}
    return data, "\n".join(lines), EXIT_OK


def cmd_check(ctx):
    L = ctx.require_ledger()
    ix, args, t = Index(L, ctx.cfg), ctx.args, now()
    areas = scope_areas(L, args.area, args.paths)
    if args.lens and args.lens not in L.lens_ids():
        raise CairnError(f"unknown lens {args.lens!r}; this ledger's lenses: {', '.join(L.lens_ids())}")
    for a in areas:
        if a not in L.area_ids():
            raise CairnError(f"unknown area {a!r}; see `cairn map`")
    probe = {"id": "F-00000000", "kind": "finding", "title": args.intent, "areas": areas, "lens": args.lens,
             "locs": [{"path": p} for p in args.paths or []]}
    if len(areas) == 1 and args.lens:
        key = f"audit/{areas[0]}/{args.lens}"
    else:
        slug = ""
        for w in tokens(args.intent):
            if len(slug) + len(w) + 1 > 40:
                break
            slug = f"{slug}-{w}" if slug else w
        slug = slug or "work"
        key = f"topic/{areas[0] if areas else 'repo'}/{slug}"
    held, overlaps = None, []
    for c in ix.active.values():
        reasons = []
        c_areas, c_lens = claim_areas(c)
        if c["key"] == key:
            reasons.append("same claim key")
        if areas and c_areas & set(areas):
            reasons.append("same area" + (" and lens" if args.lens and args.lens == c_lens else ""))
        if args.paths and set(args.paths) & set(as_list(c.get("paths"))):
            reasons.append("overlapping paths")
        if cosine(tokens(args.intent), tokens(c.get("intent", ""))) >= 0.4:
            reasons.append("similar intent")
        if reasons:
            mine = ctx.ident.holds(c)
            overlaps.append({"claim": c, "reasons": reasons, "mine": mine})
            if not mine and ("same claim key" in reasons or "same area and lens" in reasons):
                held = held or c
    similar = []
    for r in L.of_kind("finding", "report", "task"):
        st = ix.status(r["id"])
        if st in GONE_STATES or (r["kind"] == "task" and st == "done"):
            continue
        s = max(similarity(probe, r), text_cosine(args.intent, record_text(r, full=True)) * 0.75)
        if s >= RELATED_THRESHOLD:
            similar.append((s, r))
    similar.sort(key=lambda x: -x[0])
    cell = ix.coverage(areas).get((areas[0], args.lens)) if len(areas) == 1 and args.lens else None
    fr = ctx.cfg.freshness
    covered = False
    if cell and cell["depth"] in ("standard", "deep") and cell["commits_since"] is not None:
        covered = (cell["commits_since"] <= fr.get("covered_max_commits", 5)
                   and (t - parse_ts(cell["at"])).days <= fr.get("covered_max_days", 30))
    join = [r for s, r in similar if s >= DUP_THRESHOLD and r["kind"] == "finding"]
    if held:
        verdict, why = "HELD", f"{held['key']} is held by {held['holder']['github']} until {held['expires_at']}"
    elif join:
        verdict, why = "JOIN", f"{join[0]['id']} looks like the same thing: reproduce it and file a review"
    elif covered:
        verdict, why = "COVERED", (f"{areas[0]} × {args.lens} was examined ({cell['depth']}, {cell['result']}) at "
                                   f"{short(cell['commit'])}, {cell['commits_since']} commits since")
    else:
        verdict, why = "PROCEED", f"claim it: cairn claim {key} --intent \"{args.intent}\""
        close = [r for s, r in similar if s >= 0.45]
        if close:
            why += f"\n  but first read {len(close)} closely related record(s) below: if one is your result, review it instead"
    lines = [f"{verdict}: {why}"]
    if overlaps:
        lines.append("\nOverlapping claims")
        for o in overlaps:
            lines.append(claim_line(o["claim"], t) + f"  [{', '.join(o['reasons'])}{'; yours' if o['mine'] else ''}]")
    if similar:
        lines.append("\nRelated records (score: 0.55+ likely duplicate)")
        lines += [rec_line(ix, r, s) for s, r in similar[:8]]
    if cell:
        since = "?" if cell["commits_since"] is None else cell["commits_since"]
        lines.append(f"\nCoverage {cell['area']} × {cell['lens']}: {cell['depth']}, {cell['result']}, by {cell['by']} "
                     f"{ago(cell['at'], t)} at {short(cell['commit'])}, {since} commits since ({cell['record']})")
        if cell.get("not_examined"):
            lines.append(f"  not examined: {cell['not_examined']}")
    data = {"verdict": verdict, "reason": why, "suggested_key": key, "areas": areas,
            "overlapping_claims": overlaps, "coverage": cell,
            "related": [dict(rec_summary(ix, r), score=s) for s, r in similar[:8]]}
    return data, "\n".join(lines), (EXIT_OK if verdict == "PROCEED" else EXIT_BLOCKED)


def cmd_search(ctx):
    L = ctx.require_ledger()
    ix, args = Index(L, ctx.cfg), ctx.args
    q = tokens(args.query)
    kinds = args.kind or ["finding", "report", "benchmark", "task"]
    hits = []
    for r in L.records.values():
        if r["kind"] not in kinds or not in_scope(r, args.area, args.lens, args.facet):
            continue
        st = ix.status(r["id"])
        if args.state and st not in args.state:
            continue
        if not args.state and st in GONE_STATES and not args.all:
            continue
        if args.path and not any(l.get("path", "").startswith(args.path) for l in r.get("locs") or []):
            continue
        s = cosine(q, tokens(record_text(r, full=True))) if q else 0.5
        if s > 0.05 or not q:
            hits.append((s, r))
    hits.sort(key=lambda x: (-x[0], x[1].get("created_at", "")))
    claims = [c for c in ix.active.values() if q and cosine(q, tokens(c.get("intent", "") + " " + c["key"])) > 0.2]
    lines = [f"{len(hits)} records" + (f", showing {args.limit}" if len(hits) > args.limit else "")]
    lines += [rec_line(ix, r, s) for s, r in hits[: args.limit]]
    if claims:
        lines.append("\nActive claims matching")
        lines += [claim_line(c, now()) for c in claims]
    data = {"results": [dict(rec_summary(ix, r), score=round(s, 3)) for s, r in hits[: args.limit]],
            "claims": claims}
    return data, "\n".join(lines), EXIT_OK


def cmd_claim(ctx):
    args, ident, t = ctx.args, ctx.ident, now()
    key = args.key.lower()
    if not KEY_RE.match(key):
        raise CairnError("claim keys look like audit/<area>/<lens>, task/<id>, bench/<id>/<commit7>/<env>, "
                         "topic/<area>/<slug> (lowercase)")
    ttl = parse_ttl(args.ttl)

    def mutate(L):
        parts = key.split("/")
        if parts[0] == "audit":
            if len(parts) != 3 or parts[1] not in L.area_ids() or parts[2] not in L.lens_ids():
                raise CairnError("audit keys are audit/<area>/<lens> with this ledger's ids; see `cairn map`")
        if parts[0] == "task":
            tid = parts[1].upper() if len(parts) == 2 else ""
            if tid not in L.records:
                raise CairnError(f"no task {tid}")
        cur = L.claims.get(key)
        if cur and not claim_expired(cur, t) and not ident.holds(cur):
            h = cur["holder"]
            raise Blocked(f"HELD: {key} is held by {h['github']} ({h.get('agent')}) until {cur['expires_at']}: "
                          f"\"{cur.get('intent', '')}\"", {"claim": cur})
        c = {"key": key, "intent": args.intent or "", "holder": ident.author(),
             "areas": args.area or ([parts[1]] if parts[0] in ("audit", "topic") and len(parts) > 2 else []),
             "lens": args.lens or (parts[2] if parts[0] == "audit" else None), "paths": args.paths or [],
             "acquired_at": cur["acquired_at"] if cur and ident.holds(cur) else iso(t),
             "heartbeat_at": iso(t), "expires_at": iso(t + ttl)}
        return {claim_path(key): dump_json(c)}, c

    commit, _, c = write(ctx.cfg, ident, mutate, f"cairn: claim {key}")
    return c, f"CLAIMED {key} until {c['expires_at']} (ledger {short(commit)})", EXIT_OK


def _holder_mutation(ctx, key, fn, verb):
    ident, t = ctx.ident, now()

    def mutate(L):
        cur = L.claims.get(key)
        if not cur:
            raise Blocked(f"no claim {key}")
        if not ident.holds(cur) and not claim_expired(cur, t):
            raise Blocked(f"{key} is held by {cur['holder']['github']} (session {cur['holder'].get('session')}), "
                          f"not by you", {"claim": cur})
        return fn(cur, t)

    commit, _, res = write(ctx.cfg, ident, mutate, f"cairn: {verb} {key}")
    return commit, res


def cmd_heartbeat(ctx):
    key, ttl = ctx.args.key.lower(), parse_ttl(ctx.args.ttl)

    def fn(cur, t):
        if not ctx.ident.holds(cur):
            raise Blocked(f"{key} expired and is not yours any more; claim it again")
        c = dict(cur, heartbeat_at=iso(t), expires_at=iso(t + ttl))
        return {claim_path(key): dump_json(c)}, c

    _, c = _holder_mutation(ctx, key, fn, "heartbeat")
    return c, f"EXTENDED {key} until {c['expires_at']}", EXIT_OK


def cmd_release(ctx):
    key = ctx.args.key.lower()
    _holder_mutation(ctx, key, lambda cur, t: ({claim_path(key): None}, cur), "release")
    return {"released": key}, f"RELEASED {key}", EXIT_OK


def cmd_claims(ctx):
    L = ctx.require_ledger()
    t = now()
    rows = sorted(L.claims.values(), key=lambda c: c["key"])
    active = [c for c in rows if not claim_expired(c, t)]
    lines = [f"{len(active)} active, {len(rows) - len(active)} expired"] + [claim_line(c, t) for c in active]
    return {"active": active, "expired": [c for c in rows if claim_expired(c, t)]}, "\n".join(lines), EXIT_OK


def read_payload(src):
    try:
        raw = sys.stdin.read() if src == "-" else open(src, encoding="utf-8").read()
    except OSError as e:
        raise CairnError(f"cannot read {src}: {e}")
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise CairnError(f"{src} is not valid JSON: {e}")
    if not isinstance(data, dict):
        raise CairnError("payload must be a JSON object")
    return data


def default_commit(cfg):
    """HEAD if other people can resolve it (it is on a remote branch); otherwise its merge-base with main."""
    head = rev_parse("HEAD")
    if not head:
        main = rev_parse(cfg.main_ref)
        return (main, f"note: no HEAD here; recorded the tip of {cfg.main_branch} ({short(main)}). Use --commit to override.") \
            if main else (None, None)
    on_remote = run_git(["for-each-ref", "--contains", head, "--count=1", "--format=%(refname)", "refs/remotes"],
                        check=False).stdout.strip()
    if on_remote:
        return head, None
    main = rev_parse(cfg.main_ref)
    base = run_git(["merge-base", head, main], check=False).stdout.strip() if main else ""
    if base:
        return base, (f"note: HEAD {short(head)} is not on any remote branch, so others couldn't resolve it; "
                      f"recorded its merge-base with {cfg.main_branch} ({short(base)}). Use --commit to override.")
    return head, f"warning: HEAD {short(head)} is not on any remote branch; others may not be able to resolve it"


def finalize(kind, payload, ctx, L, commit=None):
    rec = {k: v for k, v in payload.items() if k not in ("id", "kind", "created_at", "author")}
    rec.update(id=new_id(kind), kind=kind, created_at=iso(now()), author=ctx.ident.author())
    note = None
    if commit:
        rec["commit"] = rev_parse(commit) or commit
    elif rec.get("commit"):
        rec["commit"] = rev_parse(str(rec["commit"])) or rec["commit"]
    elif kind != "task":
        rec["commit"], note = default_commit(ctx.cfg)
        if not rec["commit"]:
            rec.pop("commit")
    if not rec.get("areas"):
        rec["areas"] = scope_areas(L, [], [l.get("path", "") for l in rec.get("locs") or [] if isinstance(l, dict)])
    if isinstance(rec.get("facets"), dict):
        rec["facets"] = {k: as_list(v) for k, v in rec["facets"].items()}
    rec.setdefault("links", {})
    return rec, note


def guard(rec, ctx, L):
    problems = validate_record(rec, L, ctx.cfg.repro_runners)
    if problems:
        raise CairnError("record rejected:\n  - " + "\n  - ".join(problems))
    leaks = scan_secrets(serialize_record(rec))
    if leaks:
        raise CairnError(f"record looks like it contains a secret ({', '.join(leaks)}); remove it and resubmit")
    lens = L.lens(rec.get("lens")) or {}
    if ctx.cfg.public and lens.get("sensitive") and rec.get("severity") in ("high", "critical"):
        raise CairnError(f"this ledger is public and {rec['lens']} is a sensitive lens: report high/critical "
                         f"findings through the project's security policy or a private ledger instead")


def release_change(ctx, L, key, t):
    key = key.lower()
    cur = L.claims.get(key)
    if cur and (ctx.ident.holds(cur) or claim_expired(cur, t)):
        return {claim_path(key): None}
    if cur:
        raise Blocked(f"--release {key}: held by {cur['holder']['github']}, not you")
    return {}


def cmd_submit(ctx):
    args, t = ctx.args, now()
    kind = args.kind
    payload = read_payload(args.file)
    if args.not_duplicate:
        payload["dedupe_override"] = args.not_duplicate

    def mutate(L):
        if not L.commit:
            raise CairnError("no ledger yet; run `cairn init`")
        rec, cnote = finalize(kind, payload, ctx, L, args.commit)
        guard(rec, ctx, L)
        ix = Index(L, ctx.cfg, t)
        notes = [cnote] if cnote else []
        if kind in ("finding", "task") and not args.not_duplicate:
            replaced = {str(x).upper() for x in as_list((rec.get("links") or {}).get("supersedes"))}
            pool = [r for r in L.of_kind(kind) if ix.status(r["id"]) not in GONE_STATES | {"done"}
                    and r["id"] not in replaced]   # a better version of a record is not a duplicate of it
            cands = sorted(((similarity(rec, r), r) for r in pool), key=lambda x: -x[0])
            cands = [(s, r) for s, r in cands if s >= RELATED_THRESHOLD][:5]
            mine = rec["author"].get("session")
            for s, top in cands:
                if s < DUP_THRESHOLD:
                    break
                wording = text_cosine(record_text(rec), record_text(top))
                own = bool(mine) and top.get("author", {}).get("session") == mine
                if kind == "finding" and (wording < DUP_TEXT_FLOOR or (own and s < DUP_OWN_SESSION)):
                    continue   # tasks are one line of text, so their score is already all wording
                where = "same code" if loc_score(rec.get("locs"), top.get("locs")) >= 0.8 else "different code"
                raise Blocked(
                    f"LIKELY DUPLICATE of {top['id']} ({s:.2f}: wording {wording:.2f}, {where}): "
                    f"\"{top.get('title')}\" by {top['author']['github']}.\n  If you reproduced it: cairn review "
                    f"{top['id']} confirms --method reproduced-blind --note \"...\"\n  If it is a different claim "
                    f"about the same code: resubmit with --not-duplicate \"<what differs>\"",
                    {"candidates": [dict(rec_summary(ix, r, fresh=False), score=s) for s, r in cands]})
            if cands:
                notes.append("related: " + ", ".join(f"{r['id']} ({s:.2f})" for s, r in cands))
        if kind == "finding" and rec.get("severity") == "info":
            notes.append("note: an info finding is for something the next agent would otherwise have to rediscover. "
                         "A stale comment, typo or other small fix that needs no discussion is better made in the "
                         "code than filed here.")
        if kind == "note" and not args.not_duplicate and not as_list((rec.get("links") or {}).get("supersedes")):
            for r in L.of_kind("note"):
                if ix.status(r["id"]) in GONE_STATES or not set(as_list(r.get("areas"))) & set(rec.get("areas", [])):
                    continue
                if cosine(tokens(r.get("title", "") + " " + r.get("summary", "")),
                          tokens(rec.get("title", "") + " " + rec.get("summary", ""))) >= 0.35:
                    raise Blocked(f"NOTE EXISTS: {r['id']} \"{r.get('title')}\" already explains this. Improve it instead: "
                                  f"submit your version with \"links\": {{\"supersedes\": \"{r['id']}\"}}",
                                  {"record": rec_summary(ix, r, fresh=False)})
        if kind == "report":
            for c in rec.get("coverage") or []:
                if c.get("depth") in ("standard", "deep") and not _str(c.get("not_examined"), 600):
                    raise CairnError(
                        f"coverage of {c.get('area')} × {c.get('lens')} at depth {c.get('depth')} needs "
                        f"\"not_examined\": what you did not read or check, in a sentence or two (up to 600 "
                        f"characters; \"nothing: ...\" with what makes you sure, if that is true). "
                        f"If you cannot say, the depth is skim.")
        if kind == "report" and not args.not_duplicate:
            replaced = {str(x).upper() for x in as_list((rec.get("links") or {}).get("supersedes"))}
            kept, dropped = [], []
            for c in rec.get("coverage") or []:
                by = next((r for r in L.of_kind("report") if r["id"] not in replaced   # a better version of a
                           and r.get("commit") == rec.get("commit")                    # report doesn't duplicate it
                           and ix.status(r["id"]) not in GONE_STATES and any(
                               x.get("area") == c["area"] and x.get("lens") == c["lens"]
                               and DEPTHS.index(x.get("depth", "skim")) >= DEPTHS.index(c["depth"])
                               for x in r.get("coverage") or [])), None)
                (dropped if by else kept).append((c, by))
            if dropped and not kept:
                c, r = dropped[0]
                raise Blocked(f"ALREADY COVERED: {r['id']} covered {c['area']} × {c['lens']} at the same commit to "
                              f"the same depth or deeper (yours: {c['depth']}). To replace that report, add "
                              f"\"links\": {{\"supersedes\": \"{r['id']}\"}}. To add a report on a different part "
                              f"of the same cell: --not-duplicate \"<what yours covers>\"",
                              {"record": rec_summary(ix, r, fresh=False)})
            if dropped:
                rec["coverage"] = [c for c, _ in kept]
                notes.append("not recorded, already covered at this commit to the same depth or deeper: "
                             + ", ".join(f"{c['area']} × {c['lens']} ({r['id']})" for c, r in dropped)
                             + ". The rest of the report was accepted.")
        if kind == "benchmark":
            same = [r for r in L.of_kind("benchmark") if r.get("bench") == rec["bench"]
                    and r.get("commit") == rec.get("commit") and r.get("env", {}).get("class") == rec["env"]["class"]]
            if same:
                n = sum(len(m.get("samples", [])) for r in same for m in r.get("metrics", [])[:1])
                notes.append(f"merged with {len(same)} earlier run(s) at this commit and env ({n} samples before this one)")
        if getattr(args, "dry_run", False):
            raise DryRun(rec, "\n".join(notes) or None)
        changes = {record_path(rec): serialize_record(rec)}
        if args.release:
            changes.update(release_change(ctx, L, args.release, t))
        return changes, (rec, "\n".join(notes) or None)

    try:
        commit, _, (rec, note) = write(ctx.cfg, ctx.ident, mutate, f"cairn: {kind} {payload.get('title', '')[:60]}")
    except DryRun as d:
        rec, note = d.args
        return {"dry_run": True, "record": rec, "note": note}, \
            f"WOULD SUBMIT ({kind}): passes every check; nothing was written" + (f"\n{note}" if note else ""), EXIT_OK
    text = f"SUBMITTED {rec['id']} ({kind}) at ledger {short(commit)}" + (f"\n{note}" if note else "")
    if args.release:
        text += f"\nreleased {args.release}"
    return {"id": rec["id"], "record": rec, "note": note, "ledger": commit}, text, EXIT_OK


def review_record(ctx, L, target, verdict, method=None, note="", of=None, commit=None, resolves=None):
    tgt = L.records.get(target)
    if not tgt:
        raise CairnError(f"no record {target}")
    payload = {"title": f"{verdict} {target}: {tgt.get('title', '')}"[:200], "target": target, "verdict": verdict,
               "areas": tgt.get("areas", []), "locs": tgt.get("locs", []), "body": note or ""}
    if tgt.get("lens"):
        payload["lens"] = tgt["lens"]
    if method:
        payload["method"] = method
    links = {}
    if of:
        links["duplicate_of"] = of.upper()
    if resolves:
        links["resolves"] = resolves.upper()
    payload["links"] = links
    rec, cnote = finalize("review", payload, ctx, L, commit)
    guard(rec, ctx, L)
    return rec, cnote


def cmd_review(ctx):
    args, t = ctx.args, now()
    target = args.target.upper()
    if args.verdict in ("confirms", "refutes") and not args.method:
        raise CairnError(f"{args.verdict} needs --method ({', '.join(METHODS)})")
    if args.verdict == "duplicate" and not args.of:
        raise CairnError("duplicate needs --of <original id>")

    def mutate(L):
        tgt = L.records.get(target)
        if not tgt:
            raise CairnError(f"no record {target}")
        me = ctx.ident.user.lower()
        if args.verdict == "retract" and tgt["author"]["github"].lower() != me:
            raise CairnError("only the author can retract; file `refutes` instead")
        session = ctx.ident.author().get("session")
        if args.verdict == "confirms" and tgt["author"]["github"].lower() == me \
                and session and tgt["author"].get("session") == session \
                and Index(L, ctx.cfg, t).freshness(tgt).get("state") == "fresh":
            raise Blocked(f"{target} is a record you filed in this session and it is still fresh, so confirming it "
                          f"adds nothing. Put what you verified in the record's evidence or repro; confirm your own "
                          f"record only to re-check it after the code has moved.")
        rec, cnote = review_record(ctx, L, target, args.verdict, args.method, args.note, args.of, args.commit,
                                   args.resolves)
        for v in L.of_kind("review"):
            if v.get("target") == target and v.get("verdict") == args.verdict \
                    and v["author"]["github"].lower() == me and v.get("commit") == rec.get("commit"):
                raise Blocked(f"you already filed {args.verdict} on {target} at this commit ({v['id']})")
        changes = {record_path(rec): serialize_record(rec)}
        if args.release:
            changes.update(release_change(ctx, L, args.release, t))
        return changes, (rec, cnote)

    commit, L, (rec, cnote) = write(ctx.cfg, ctx.ident, mutate, f"cairn: review {target} {args.verdict}")
    ix = Index(Ledger(commit), ctx.cfg)
    st = ix.status(target)
    extra = ""
    if args.verdict == "confirms" and args.method not in COUNTING_METHODS:
        extra = " (this method is shown but does not count toward confirmed)"
    elif args.verdict == "confirms" and rec["author"]["github"].lower() == L.records[target]["author"]["github"].lower():
        extra = (" (recorded, and it refreshes the record; it does not count toward confirmed, which needs "
                 "a reproduction by a different person)")
    text = f"REVIEWED {target}: {args.verdict} as {rec['id']}; {target} is now {st}{extra}" + (f"\n{cnote}" if cnote else "")
    return {"id": rec["id"], "target": target, "target_status": st}, text, EXIT_OK


def cmd_task(ctx):
    args = ctx.args
    payload = {"title": args.title, "areas": args.area or [], "priority": args.priority, "body": args.body or ""}
    if args.lens:
        payload["lens"] = args.lens
    if args.paths:
        payload["locs"] = [{"path": p} for p in args.paths]
    tmp = os.path.join(tempfile.mkdtemp(), "task.json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    args.kind, args.file, args.release, args.commit = "task", tmp, None, None
    return cmd_submit(ctx)


def cmd_next(ctx):
    args, ident, t = ctx.args, ctx.ident, now()
    ttl = parse_ttl(args.ttl)

    def mutate(L):
        ix = Index(L, ctx.cfg, t)
        tasks = [r for r in L.of_kind("task") if ix.status(r["id"]) == "open" and in_scope(r, args.area, args.lens)]
        tasks.sort(key=lambda r: (r.get("priority", "p9"), r.get("created_at", "")))
        if not tasks:
            return {}, None
        task = tasks[0]
        key = f"task/{task['id'].lower()}"
        c = {"key": key, "intent": task.get("title", ""), "holder": ident.author(), "areas": task.get("areas", []),
             "lens": task.get("lens"), "paths": [l["path"] for l in task.get("locs") or []],
             "acquired_at": iso(t), "heartbeat_at": iso(t), "expires_at": iso(t + ttl)}
        return {claim_path(key): dump_json(c)}, (task, c)

    commit, _, res = write(ctx.cfg, ident, mutate, "cairn: next task")
    if not res:
        return {"task": None}, "No open, unclaimed tasks" + (" in scope" if args.area or args.lens else ""), \
            EXIT_BLOCKED
    task, c = res
    text = (f"CLAIMED {c['key']} until {c['expires_at']}\n{task['id']} {task.get('priority')} {task['title']}\n"
            f"{task.get('body', '')}\nWhen done, submit a record with \"links\": {{\"resolves\": \"{task['id']}\"}} "
            f"(or `cairn review ... --resolves {task['id']}`) and --release {c['key']}")
    return {"task": task, "claim": c}, text.strip(), EXIT_OK


def freshness_lines(ix, r):
    fr = ix.freshness(r)
    lines = [f"Freshness: {fr['state']}"
             + (f" · verified at {short(fr['verified_at'])}, {fr.get('commits', '?')} commits and "
                f"{fr.get('days', '?')} days ago" if fr["state"] != "unknown" else "")]
    lines += [f"  - {s['kind']}: {s['detail']}" for s in fr["signals"]]
    rel = ix.release_presence(r) if r["kind"] not in ("change", "note") else None
    if rel:
        lines.append(f"In release ({ix.cfg.release_branch}): {rel}")
    return lines


def cmd_show(ctx):
    L = ctx.require_ledger()
    ix = Index(L, ctx.cfg)
    rid = ctx.args.id.upper()
    r = L.records.get(rid)
    if not r:
        raise CairnError(f"no record {rid}")
    lines = [f"{rid}  {r['kind']}  status: {ix.status(rid)}", f"path: {L.paths[rid]}"]
    if r["kind"] in ("finding", "report", "change", "note"):
        lines += freshness_lines(ix, r)
    lines += ["", serialize_record(r).rstrip()]
    revs = ix.reviews.get(rid, [])
    if revs:
        lines.append("\nReviews")
        for v in revs:
            lines.append(f"  {v['id']} {v['verdict']:<9} {v.get('method', ''):<16} {v['author']['github']} "
                         f"at {short(v.get('commit'))} {ago(v['created_at'])}  {v.get('body', '')[:80]}")
    rel = [x for x in L.records.values() if rid in sum((as_list((x.get("links") or {}).get(k))
           for k in ("resolves", "supersedes", "cites", "addresses")), [])]
    if rel:
        lines.append("\nLinked from")
        lines += [rec_line(ix, x) for x in rel]
    data = {"record": r, "status": ix.status(rid), "reviews": revs, "linked_from": [x["id"] for x in rel]}
    if r["kind"] in ("finding", "report", "change", "note"):
        data["freshness"] = ix.freshness(r)
        data["in_release"] = ix.release_presence(r) if r["kind"] not in ("change", "note") else None
    return data, "\n".join(lines), EXIT_OK


def cmd_status(ctx):
    L = ctx.require_ledger()
    ix, args = Index(L, ctx.cfg), ctx.args
    rows = []
    for r in L.records.values():
        if r["kind"] == "review" and "review" not in (args.kind or []):
            continue
        if args.kind and r["kind"] not in args.kind:
            continue
        if not in_scope(r, args.area, args.lens, args.facet):
            continue
        st = ix.status(r["id"])
        if args.state and st not in args.state:
            continue
        if args.mine and r["author"]["github"].lower() != ctx.ident.user.lower():
            continue
        if args.stale:
            if r["kind"] not in ("finding", "report") or st not in LIVE_STATES \
                    or ix.freshness(r)["state"] in ("fresh", "unknown"):
                continue
        rows.append(r)
    rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    lines = [f"{len(rows)} records"] + [rec_line(ix, r) for r in rows[: args.limit]]
    return {"records": [rec_summary(ix, r) for r in rows[: args.limit]]}, "\n".join(lines), EXIT_OK


def recheck_queue(ix):
    q = []
    for r in ix.L.of_kind("finding"):
        if ix.status(r["id"]) not in LIVE_STATES:
            continue
        fr = ix.freshness(r)
        if fr["state"] in ("watch", "expired", "stale", "gone"):
            q.append((FRESH_RANK[fr["state"]], sev_rank(r), r, fr))
    q.sort(key=lambda x: (-x[0], -x[1], x[2].get("created_at", "")))
    return q


def note_recheck_queue(ix):
    """Live notes that can no longer be taken on trust: expired (by age or by commits to the area), stale or
    gone. `watch` is left out: a note points at whole files, and files change all the time."""
    q = []
    for r in ix.L.of_kind("note"):
        if ix.status(r["id"]) not in LIVE_STATES:
            continue
        fr = ix.freshness(r)
        if fr["state"] in ("expired", "stale", "gone"):
            q.append((FRESH_RANK[fr["state"]], 0, r, fr))
    q.sort(key=lambda x: (-x[0], x[2].get("created_at", "")))
    return q


def recheck_task(ix, rid):
    """The open or claimed re-check task for a record, if one has been posted."""
    for r in ix.L.of_kind("task"):
        if r.get("title", "").startswith(f"Re-check {rid}:") and ix.status(r["id"]) in ("open", "claimed"):
            return r
    return None


def cmd_recheck(ctx):
    args, cfg = ctx.args, ctx.cfg
    L = ctx.require_ledger()
    ix = Index(L, cfg)
    if not args.id:
        q, nq = recheck_queue(ix), note_recheck_queue(ix)
        lines = [f"{len(q)} live findings need re-checking (strongest signal first)"]
        lines += [rec_line(ix, r) for _, _, r, _ in q[: args.limit]]
        if nq:
            lines.append(f"{len(nq)} notes need checking against the code")
            lines += [rec_line(ix, r) for _, _, r, _ in nq[: args.limit]]
        data = {"queue": [rec_summary(ix, r) for _, _, r, _ in q[: args.limit]],
                "notes": [rec_summary(ix, r) for _, _, r, _ in nq[: args.limit]]}
        if args.post_tasks:
            existing = {r["title"].split(":")[0] for r in L.of_kind("task") if ix.status(r["id"]) in ("open", "claimed")}

            def mutate(L2):
                changes, made = {}, []
                for queue in (q, nq):   # up to N of each, so a long queue of findings never starves the notes
                    posted = 0
                    for _, _, r, fr in queue:
                        if posted >= args.post_tasks:
                            break
                        title = f"Re-check {r['id']}: {r['title']}"[:200]
                        if f"Re-check {r['id']}" in existing:
                            continue
                        why = f"{r['id']} is {fr['state']}: " + "; ".join(s["detail"] for s in fr["signals"])
                        if r["kind"] == "note":
                            pri = "p3"
                            body = (f"{why}.\nRead the note (`cairn show {r['id']}`) against the code at the current "
                                    f"{cfg.main_branch}. Still right: `cairn review {r['id']} confirms --method "
                                    f"checked-evidence --resolves <this task>`. Wrong or out of date: submit a corrected "
                                    f"note with \"links\": {{\"supersedes\": \"{r['id']}\", \"resolves\": \"<this task>\"}}.")
                        else:
                            pri = {"critical": "p1", "high": "p1", "medium": "p2"}.get(r.get("severity"), "p3")
                            body = (f"{why}.\nReproduce it at the current {cfg.main_branch}, then `cairn review {r['id']} "
                                    f"confirms|refutes|fixed --method ... --resolves <this task>`.")
                        payload = {"title": title, "areas": r.get("areas", []), "priority": pri, "body": body,
                                   "links": {"cites": [r["id"]]}}
                        if r.get("lens"):
                            payload["lens"] = r["lens"]
                        rec, _ = finalize("task", payload, ctx, L2)
                        guard(rec, ctx, L2)
                        changes[record_path(rec)] = serialize_record(rec)
                        made.append(rec["id"])
                        posted += 1
                return changes, made

            _, _, made = write(cfg, ctx.ident, mutate, "cairn: post re-check tasks")
            lines.append(f"posted {len(made or [])} re-check task(s): {', '.join(made or []) or 'none new'}")
            data["posted"] = made
        return data, "\n".join(lines), EXIT_OK
    rid = args.id.upper()
    r = L.records.get(rid)
    if not r:
        raise CairnError(f"no record {rid}")
    repro = r.get("repro")
    lines = freshness_lines(ix, r) if r["kind"] in ("finding", "report", "note") else []
    if r["kind"] == "note":
        lines.append(f"Read {rid} against the current code. Still right: `cairn review {rid} confirms --method "
                     f"checked-evidence`. Wrong or out of date: submit a note with \"links\": {{\"supersedes\": \"{rid}\"}}.")
        return {"id": rid, "repro": None}, "\n".join(lines), EXIT_OK
    if not repro:
        lines.append(f"{rid} has no executable repro: re-check it by hand, then `cairn review {rid} confirms|refutes|fixed`.")
        return {"id": rid, "repro": None}, "\n".join(lines), EXIT_OK
    if not repro_allowed(repro.get("run", ""), cfg.repro_runners):
        raise CairnError(f"repro command {repro.get('run')!r} does not match this ledger's repro_runners "
                         f"({cfg.repro_runners or 'none configured'}); not running it")
    lines.append(f"Repro: {repro['run']}  (expected to {repro['expect']})")
    if not args.run:
        lines.append("Add --run to execute it here (and --file to record a confirmation if it still reproduces).")
        return {"id": rid, "repro": repro}, "\n".join(lines), EXIT_OK
    try:
        p = subprocess.run(shlex.split(repro["run"]), cwd=cfg.root, capture_output=True, text=True,
                           timeout=cfg.repro_timeout)
        outcome = "fail" if p.returncode != 0 else "pass"
        tail = (p.stdout + p.stderr)[-1500:]
    except subprocess.TimeoutExpired:
        raise CairnError(f"repro timed out after {cfg.repro_timeout}s")
    except OSError as e:
        raise CairnError(f"could not run repro: {e}")
    data = {"id": rid, "outcome": outcome, "expected": repro["expect"], "output_tail": tail}
    if outcome != repro["expect"]:
        lines.append(f"NO LONGER REPRODUCES: expected {repro['expect']}, got {outcome}. It may be fixed, or the repro "
                     f"is broken. Investigate, then file `fixed` or `refutes`.")
        return data, "\n".join(lines), EXIT_BLOCKED
    lines.append(f"STILL REPRODUCES ({outcome} as expected).")
    if args.file:
        def mutate(L2):
            rec, cnote = review_record(ctx, L2, rid, "confirms", "reproduced", f"recheck: `{repro['run']}` -> {outcome}",
                                       resolves=args.resolves)
            return {record_path(rec): serialize_record(rec)}, (rec, cnote)

        _, _, (rec, cnote) = write(cfg, ctx.ident, mutate, f"cairn: recheck {rid}")
        lines.append(f"filed {rec['id']} (confirms, reproduced) at {short(rec.get('commit'))}" + (f"\n{cnote}" if cnote else ""))
        data["review"] = rec["id"]
    return data, "\n".join(lines), EXIT_OK


def work_remote(cfg):
    return cfg.settings.get("work_remote") or cfg.remote


def merge_check(base, main, head):
    """Would `head` (built on `base`) apply to `main`? (True, tree) | (False, [conflicted paths]) | (None, why)."""
    p = run_git(["merge-tree", "--write-tree", "--name-only", "--no-messages", f"--merge-base={base}", main, head],
                check=False)
    if p.returncode == 0:
        return True, p.stdout.split("\n", 1)[0].strip()
    if p.returncode == 1:
        out = p.stdout.split("\n")
        return False, [l for l in out[1:] if l.strip()]
    return None, f"git merge-tree unavailable or failed: {p.stderr.strip()[:200]} (needs git 2.40+)"


def rebuild_from_patch(base, patch, message, when):
    """Recreate a change's commit from its stored patch, without touching any worktree (deterministic sha)."""
    with tempfile.TemporaryDirectory() as td:
        env = {"GIT_INDEX_FILE": os.path.join(td, "index")}
        git("read-tree", base, env=env)
        p = run_git(["apply", "--cached", "--whitespace=nowarn", "-"], input=patch, env=env, check=False)
        if p.returncode != 0:
            return None
        tree = git("write-tree", env=env).strip()
        cenv = dict(env, GIT_AUTHOR_NAME="cairn", GIT_AUTHOR_EMAIL="cairn@localhost", GIT_COMMITTER_NAME="cairn",
                    GIT_COMMITTER_EMAIL="cairn@localhost", GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
        return git("commit-tree", tree, "-p", base, "-m", message, env=cenv).strip()


def change_commit(ix, rec):
    """A local commit holding the change: the original if we have it, else fetched, else rebuilt from the patch."""
    code = rec.get("code") or {}
    if commit_exists(code.get("commit", "")):
        return code["commit"]
    if code.get("branch"):
        run_git(["fetch", "--quiet", code.get("remote") or work_remote(ix.cfg),
                 f"+refs/heads/{code['branch']}:refs/remotes/cairn-work/{rec['id'].lower()}"], check=False)
        _commit_cache.pop(code["commit"], None)
        if commit_exists(code["commit"]):
            return code["commit"]
    patch = ix.L.text(ix.L.paths[rec["id"]][:-3] + ".patch") if code.get("patch") else None
    if patch and commit_exists(code.get("base", "")):
        return ix.D.get(("rebuilt", rec["id"]), lambda: rebuild_from_patch(
            code["base"], patch, rec.get("title", rec["id"]), rec.get("created_at", "2000-01-01T00:00:00Z")))
    return None


def change_record(ctx, L, cfg, *, title, addresses, head, base, files, patch, body=None, tests=None, extra=None,
                  supersedes=None, branch=None, remote=None):
    first = L.records[addresses[0]]
    rec = dict(extra or {})
    rec.update(title=title[:200], locs=[{"path": f} for f in files[:50]],
               code={"commit": head, "base": base, "patch": patch is not None,
                     "diffstat": git("diff", "--shortstat", base, head).strip()},
               links={**(rec.get("links") or {}), "addresses": addresses})
    if branch:
        rec["code"].update(branch=branch, remote=remote)
    if supersedes:
        rec["links"]["supersedes"] = supersedes
    if tests:
        rec["tests"] = tests
    if body:
        rec["body"] = body
    if first.get("lens") and "lens" not in rec:
        rec["lens"] = first["lens"]
    rec, _ = finalize("change", rec, ctx, L, base)
    guard(rec, ctx, L)
    changes = {record_path(rec): serialize_record(rec)}
    if patch is not None:
        changes[record_path(rec)[:-3] + ".patch"] = patch
    return rec, changes


def cmd_work(ctx):
    """Code produced for findings, stored patch-first: `start` reserves the fix and makes a branch, `submit` stores
    the change as a patch next to a `change` record (pushing a branch only if the patch is too big), `refresh`
    re-applies a change on the current main tip, `checkout` rebuilds it locally."""
    args, cfg, t = ctx.args, ctx.cfg, now()
    if args.action == "start":
        target = (args.target or "").upper()
        L = ctx.require_ledger()
        if target not in L.records:
            raise CairnError(f"no record {target}")
        key = f"fix/{target.lower()}" + (f"/{args.alt.lower()}" if args.alt else "")
        ttl = parse_ttl(args.ttl)

        def mutate(L2):
            cur = L2.claims.get(key)
            if cur and not claim_expired(cur, t) and not ctx.ident.holds(cur):
                raise Blocked(f"HELD: {cur['holder']['github']} is already working on {key} until {cur['expires_at']}; "
                              f"review their change when it lands, or start an alternative with --alt <name>",
                              {"claim": cur})
            tgt = L2.records[target]
            c = {"key": key, "intent": f"fix {target}: {tgt.get('title', '')}"[:200], "holder": ctx.ident.author(),
                 "areas": tgt.get("areas", []), "lens": tgt.get("lens"),
                 "paths": [l.get("path") for l in tgt.get("locs") or []],
                 "acquired_at": iso(t), "heartbeat_at": iso(t), "expires_at": iso(t + ttl)}
            return {claim_path(key): dump_json(c)}, c

        write(cfg, ctx.ident, mutate, f"cairn: work start {key}")
        lines = [f"CLAIMED {key}"]
        branch = f"cairn/work/{target.lower()}-{re.sub(r'[^a-z0-9-]+', '-', ctx.ident.user.lower())}"
        if not args.no_branch:
            start_point = rev_parse(cfg.main_ref) or "HEAD"
            if args.worktree:
                run_git(["worktree", "add", "-b", branch, args.worktree, start_point])
                lines.append(f"created worktree {args.worktree} on new local branch {branch} from {cfg.main_branch}")
            else:
                p = run_git(["switch", "-c", branch, start_point], check=False)
                lines.append(f"switched to new local branch {branch} (from {cfg.main_branch} {short(start_point)})"
                             if p.returncode == 0 else f"could not create branch {branch} here ({p.stderr.strip()})")
        lines.append(f"Keep the change small. When it's committed: cairn work submit --addresses {target} --title \"...\"")
        return {"claim": key, "branch": branch}, "\n".join(lines), EXIT_OK

    if args.action == "submit":
        fetch(cfg)
        head, main = rev_parse("HEAD"), rev_parse(cfg.main_ref)
        if not head or not main:
            raise CairnError(f"need a committed HEAD and a fetched {cfg.main_branch}")
        base = git("merge-base", head, main).strip()
        if base == head:
            raise CairnError(f"HEAD has no commits beyond {cfg.main_branch}; commit your change first")
        if run_git(["status", "--porcelain", "--untracked-files=no"], check=False).stdout.strip():
            raise CairnError("you have uncommitted changes to tracked files; commit them so the record matches the code")
        payload = read_payload(args.file) if args.file else {}
        addresses = [a.upper() for a in (args.addresses or [])] + \
            [a.upper() for a in as_list((payload.get("links") or {}).get("addresses"))]
        if not addresses:
            raise CairnError("say which findings or tasks this addresses: --addresses F-...")
        files = [f for f in git("diff", "--name-only", base, head).splitlines() if f]
        patch = git("format-patch", "--stdout", f"{base}..{head}")
        too_big = len(patch.encode()) > cfg.settings.get("patch_max_bytes", 200000)
        branch = remote = None
        if too_big or args.push_branch:
            branch, remote = f"cairn/work/{head[:12]}", work_remote(cfg)
            p = run_git(["push", "--porcelain", remote, f"{head}:refs/heads/{branch}"], check=False)
            if p.returncode != 0:
                raise CairnError(f"could not push the code to {remote}/{branch}: {(p.stderr or p.stdout).strip()}")
        tests = []
        for spec in args.test or []:
            run, _, result = spec.rpartition("=")
            tests.append({"run": run.strip(), "result": result.strip()})

        def mutate(L):
            for a in addresses:
                if a not in L.records:
                    raise CairnError(f"--addresses {a}: no such record")
            title = args.title or payload.get("title") or f"Fix for {addresses[0]}: {L.records[addresses[0]].get('title', '')}"
            extra = {k: v for k, v in payload.items() if k not in ("links", "title", "code", "tests", "body")}
            rec, changes = change_record(ctx, L, cfg, title=title, addresses=addresses, head=head, base=base,
                                         files=files, patch=None if too_big else patch, body=args.body or payload.get("body"),
                                         tests=tests or payload.get("tests"), extra=extra,
                                         supersedes=args.supersedes.upper() if args.supersedes else None,
                                         branch=branch, remote=remote)
            for a in addresses:
                k = f"fix/{a.lower()}"
                if k in L.claims and ctx.ident.holds(L.claims[k]):
                    changes[claim_path(k)] = None
            return changes, rec

        commit, _, rec = write(cfg, ctx.ident, mutate, f"cairn: change for {', '.join(addresses)}")
        where = f"branch {remote}/{branch} (patch too large to store)" if too_big else "patch stored next to the record" \
            + (f"; also pushed {remote}/{branch}" if branch else "")
        text = (f"SUBMITTED {rec['id']} (change) at ledger {short(commit)}\n{short(head)} on base {short(base)}, "
                f"{rec['code']['diffstat'] or 'no diffstat'}; {where}")
        return {"id": rec["id"], "record": rec, "ledger": commit}, text, EXIT_OK

    if args.action == "refresh":
        L = ctx.require_ledger()
        ix = Index(L, cfg)
        if args.target:
            targets = [L.records.get(args.target.upper())]
            if not targets[0] or targets[0].get("kind") != "change":
                raise CairnError(f"no change {args.target}")
        elif args.all:
            targets = [r for r in L.of_kind("change") if ix.status(r["id"]) in LIVE_STATES
                       and ix.freshness(r)["state"] == "watch"]
        else:
            raise CairnError("usage: cairn work refresh <change id> | --all")
        main = ix.main
        refreshed, conflicts, skipped = [], [], []
        for old in targets:
            fr = ix.freshness(old)
            if not any(s["kind"] in ("drift", "conflict") for s in fr["signals"]):
                skipped.append(old["id"])
                continue
            head = change_commit(ix, old)
            clean, info = merge_check(old["code"]["base"], main, head) if head else (None, "code not available")
            if not clean:
                conflicts.append((old, info))
                continue
            when = iso(now())
            cenv = {"GIT_AUTHOR_NAME": ctx.ident.user, "GIT_COMMITTER_NAME": ctx.ident.user,
                    "GIT_AUTHOR_EMAIL": git_config("user.email") or "cairn@localhost",
                    "GIT_COMMITTER_EMAIL": git_config("user.email") or "cairn@localhost"}
            new = git("commit-tree", info, "-p", main, *sign_args(), "-m",
                      f"{old.get('title', old['id'])}\n\nRefreshed onto {cfg.main_branch} {short(main)} from {old['id']}",
                      env=cenv).strip()
            patch = git("format-patch", "--stdout", f"{main}..{new}")
            too_big = len(patch.encode()) > cfg.settings.get("patch_max_bytes", 200000)
            branch = remote = None
            if too_big or old["code"].get("branch"):
                branch, remote = f"cairn/work/{new[:12]}", work_remote(cfg)
                run_git(["push", "--porcelain", remote, f"{new}:refs/heads/{branch}"])

            def mutate(L2, old=old, new=new, patch=patch, too_big=too_big, branch=branch, remote=remote):
                files = [f for f in git("diff", "--name-only", main, new).splitlines() if f]
                note = (f"Mechanical refresh of {old['id']} onto {cfg.main_branch} {short(main)} (applied cleanly). "
                        f"Tests were not re-run; reviews of {old['id']} do not carry over.\n\n{old.get('body', '')}").strip()
                extra = {k: old[k] for k in ("assumptions", "facets", "tags") if k in old}
                rec, changes = change_record(ctx, L2, cfg, title=old.get("title", old["id"]),
                                             addresses=as_list(old["links"]["addresses"]), head=new, base=main,
                                             files=files, patch=None if too_big else patch, body=note, extra=extra,
                                             supersedes=old["id"], branch=branch, remote=remote)
                return changes, rec

            _, _, rec = write(cfg, ctx.ident, mutate, f"cairn: refresh {old['id']}")
            refreshed.append((old["id"], rec["id"]))
        lines = [f"refreshed {o} -> {n} (applies cleanly to {cfg.main_branch} {short(main)})" for o, n in refreshed]
        for old, info in conflicts:
            lines.append(f"CONFLICT {old['id']}: " + (", ".join(info) if isinstance(info, list) else str(info)))
        lines += [f"{i} is up to date" for i in skipped]
        posted = []
        if conflicts and args.post_task:
            open_titles = {r["title"] for r in L.of_kind("task") if ix.status(r["id"]) in ("open", "claimed")}

            def mutate(L2):
                changes = {}
                for old, info in conflicts:
                    title = f"Rebase {old['id']}: {old.get('title', '')}"[:200]
                    if title in open_titles:
                        continue
                    payload = {"title": title, "areas": old.get("areas", []), "priority": "p2",
                               "body": f"{old['id']} conflicts with {cfg.main_branch} in: "
                                       + (", ".join(info) if isinstance(info, list) else str(info))
                                       + f".\nRebuild it (`cairn work checkout {old['id']}`), resolve, and submit a new "
                                         f"change with --supersedes {old['id']}.",
                               "links": {"cites": [old["id"]]}}
                    if old.get("lens"):
                        payload["lens"] = old["lens"]
                    rec, _ = finalize("task", payload, ctx, L2)
                    changes[record_path(rec)] = serialize_record(rec)
                    posted.append(rec["id"])
                return changes

            write(cfg, ctx.ident, mutate, "cairn: rebase tasks")
            lines.append(f"posted rebase task(s): {', '.join(posted) or 'none new'}")
        if not lines:
            lines = ["nothing to refresh"]
        code = EXIT_BLOCKED if conflicts and args.target else EXIT_OK
        return {"refreshed": refreshed, "conflicts": [o["id"] for o, _ in conflicts], "tasks": posted}, "\n".join(lines), code

    if args.action == "checkout":
        L = ctx.require_ledger()
        ix = Index(L, cfg)
        cid = (args.target or "").upper()
        rec = L.records.get(cid)
        if not rec or rec.get("kind") != "change":
            raise CairnError(f"no change {cid}")
        head = change_commit(ix, rec)
        if not head:
            raise CairnError(f"can't rebuild {cid}: its branch is gone, no patch applies, or base "
                             f"{short(rec['code']['base'])} isn't fetched")
        local = f"cairn/work/{cid.lower()}"
        run_git(["branch", "-f", local, head])
        lines = []
        if args.worktree:
            run_git(["worktree", "add", args.worktree, local])
            lines.append(f"checked out {cid} in worktree {args.worktree} (branch {local})")
        else:
            lines.append(f"branch {local} is ready at {short(head)}; `git switch {local}` to try it")
        fr = ix.freshness(rec)
        if fr["state"] != "fresh":
            lines.append(f"note: {fresh_str(fr)}")
        lines.append(f"After testing: cairn review {cid} confirms|refutes --method reproduced --note \"what you ran\"")
        return {"id": cid, "branch": local, "commit": head}, "\n".join(lines), EXIT_OK
    raise CairnError("usage: cairn work start <id> | submit --addresses <id> | refresh <change id>|--all | checkout <change id>")


# ------------------------------------------------------------------ agent context

def est_tokens(text):
    return (len(text) + 3) // 4


def fit(lines, budget):
    """Keep whole lines until the token budget is spent; say how many were cut."""
    out, used = [], 0
    for i, line in enumerate(lines):
        t = est_tokens(line) + 1
        if used + t > budget and out:
            out.append(f"(+{len(lines) - i} more lines: use `cairn brief` or `cairn show <id>`)")
            break
        out.append(line)
        used += t
    return "\n".join(out)


def note_for(ix, area):
    notes = [r for r in ix.L.of_kind("note") if area in as_list(r.get("areas")) and ix.status(r["id"]) in LIVE_STATES]
    notes.sort(key=lambda r: (FRESH_RANK.get(ix.freshness(r)["state"], 0) <= 1, ix.status(r["id"]) == "confirmed",
                              r.get("created_at", "")), reverse=True)
    return notes[0] if notes else None


def clip(text, n):
    """Shorten to at most n characters at a word boundary, marking the cut."""
    text = text or ""
    if len(text) <= n:
        return text
    cut = text[: n - 1].rsplit(" ", 1)[0].rstrip(",;:")
    return (cut or text[: n - 1]) + "…"


def area_context(ix, area, budget, seen=None):
    L, t = ix.L, ix.now
    a = L.area(area) or {}
    lines = [f"[cairn] Shared context for area `{area}` ({', '.join(a.get('globs', [])[:3])}):"]
    n = note_for(ix, area)
    if n:
        fr = ix.freshness(n)
        warn = "" if fr["state"] in ("fresh", "unknown") else f" — {fresh_str(fr)}; verify before relying on it"
        fix = "If it is wrong or outdated, submit a better note that supersedes it."
        if warn:   # whoever is already reading this code is the cheapest person to bring the note up to date
            task = recheck_task(ix, n["id"])
            fix = (f"You are reading this code anyway: if the note still holds, say so with `cairn review {n['id']} "
                   f"confirms --method checked-evidence" + (f" --resolves {task['id']}" if task else "")
                   + "`; if not, submit a better note that supersedes it"
                   + (f" and resolves {task['id']}." if task else "."))
        lines.append(f"Note {n['id']} \"{n['title']}\" ({ix.status(n['id'])}{warn}): {n.get('summary', '')}"
                     f" Full note: `cairn show {n['id']}`. {fix}")
    else:
        lines.append(f"No note explains `{area}` yet. If you build up an understanding of it, record one "
                     f"(`cairn submit note`) so the next agent doesn't have to re-read the code.")
    live = sorted([r for r in L.of_kind("finding") if area in as_list(r.get("areas")) and ix.status(r["id"]) in LIVE_STATES],
                  key=lambda r: (-sev_rank(r), r.get("created_at", "")))
    seen = set() if seen is None else seen
    floor = ix.cfg.settings.get("context_min_severity", "medium")
    floor = SEVERITIES.index(floor) if floor in SEVERITIES else 0
    minor = [r for r in live if sev_rank(r) < floor and r["id"] not in seen]
    live = [r for r in live if sev_rank(r) >= floor or r["id"] in seen]
    shown_before = [r for r in live if r["id"] in seen]
    live = [r for r in live if r["id"] not in seen]
    if live or shown_before:
        lines.append(f"Live findings ({len(live) + len(shown_before)}" +
                     (f"; {len(shown_before)} already shown above: {', '.join(r['id'] for r in shown_before[:6])}" if shown_before else "")
                     + ("):" if live else ")."))
        for r in live[:8]:
            flag = fresh_str(ix.freshness(r))
            lines.append(f"- {r['id']} {r.get('severity')} {r.get('lens')}: {r['title']} ({loc_str(r)})"
                         + (f" [{flag}]" if flag else ""))
    if minor:
        lines.append(f"{len(minor)} lower-severity finding(s) not listed: `cairn status --kind finding --area {area}`.")
    claims = [c for c in ix.active.values() if area in claim_areas(c)[0]]
    for c in claims[:4]:
        lines.append(f"In progress: {c['key']} by {c['holder']['github']} until {c['expires_at'][11:16]}Z — don't duplicate it.")
    cov = ix.coverage([area])
    clean = [f"{l} ({c['depth']}, {c['commits_since'] if c['commits_since'] is not None else '?'} commits since)"
             for (a_, l), c in sorted(cov.items()) if c["depth"] in ("standard", "deep")]
    if clean:
        lines.append("Already examined: " + "; ".join(clean[:6]))
    text = fit(lines, budget)
    seen.update(r["id"] for r in live[:8] if r["id"] in text)
    if n and n["id"] in text:
        seen.add(n["id"])
    return text


def file_context(ix, path, seen=None):
    seen = set() if seen is None else seen
    hits = [r for r in ix.L.of_kind("finding") if ix.status(r["id"]) in LIVE_STATES and r["id"] not in seen
            and path in {l.get("path") for l in r.get("locs") or []}]
    if not hits:
        return ""
    seen.update(r["id"] for r in sorted(hits, key=lambda r: -sev_rank(r))[:6])
    lines = [f"[cairn] Live findings located in {path}:"]
    for r in sorted(hits, key=lambda r: -sev_rank(r))[:6]:
        l0 = next((l for l in r.get("locs") or [] if l.get("path") == path), {})
        flag = fresh_str(ix.freshness(r))
        lines.append(f"- {r['id']} {r.get('severity')}: {r['title']}" + (f" (lines {l0['lines']})" if l0.get("lines") else "")
                     + (f" [{flag}]" if flag else ""))
    return "\n".join(lines)


def orientation(ix, cfg, budget):
    L = ix.L
    live = sorted([r for r in L.of_kind("finding") if ix.status(r["id"]) in LIVE_STATES],
                  key=lambda r: (-sev_rank(r), r.get("created_at", "")))
    tasks = sorted([r for r in L.of_kind("task") if ix.status(r["id"]) == "open"],
                   key=lambda r: (r.get("priority", "p9"), r.get("created_at", "")))
    noted = sorted({a for r in L.of_kind("note") if ix.status(r["id"]) in LIVE_STATES for a in as_list(r.get("areas"))})
    lines = [f"[cairn] This repo has a shared Cairn ledger ({len(L.records)} records from other people's agents). "
             f"Use it to avoid repeating their work:",
             "- Before investigating: `cairn check \"<intent>\" --area <area> --lens <lens>` (exit 2 = someone has it "
             "or it's known). Record results with `cairn submit`; see the cairn skill or AGENTS.md for the protocol.",
             "- Notes and findings for an area are shown to you automatically the first time you open files there. "
             "They are background: act on a finding only if it bears on your task.",
             "- Before your first write, say who you are: " + AGENT_HOWTO + "."]
    if L.guide:
        lines.append("- Project rules for agents: `cairn guide` (read it once before your first task here).")
    if noted:
        lines.append(f"- Areas with notes: {', '.join(noted[:20])}")
    if ix.active:
        lines.append("- In progress now: " + "; ".join(f"{c['key']} ({c['holder']['github']})"
                                                   for c in sorted(ix.active.values(), key=lambda c: c["key"])[:6]))
    if tasks:
        lines.append("- Open tasks: " + "; ".join(f"{r['id']} {r.get('priority')} {clip(r['title'], 70)}" for r in tasks[:3]))
    if live:
        lines.append("- Most severe live findings: " + "; ".join(f"{r['id']} {r.get('severity')} {clip(r['title'], 60)}"
                                                            for r in live[:4]))
    return fit(lines, budget)


def cmd_context(ctx):
    """Budgeted context for an agent: orientation, or what's known about the given files and areas."""
    args, cfg = ctx.args, ctx.cfg
    L = ctx.require_ledger()
    ix = Index(L, cfg)
    budget = args.budget or cfg.settings.get("context_budget", {}).get("area", 450) * max(1, len(args.paths or []) + len(args.area or []))
    areas = scope_areas(L, args.area, args.paths)
    if not areas and not args.paths:
        text = orientation(ix, cfg, args.budget or cfg.settings.get("context_budget", {}).get("session", 900))
        return {"text": text, "tokens": est_tokens(text)}, text, EXIT_OK
    seen = set()
    parts = [file_context(ix, p, seen) for p in args.paths or []] + [area_context(ix, a, budget, seen) for a in areas]
    text = fit("\n".join(x for x in parts if x).split("\n"), budget)
    return {"text": text, "tokens": est_tokens(text), "areas": areas}, text, EXIT_OK


# ------------------------------------------------------------------ harness hooks

SESSION_DIR = os.environ.get("CAIRN_SESSION_DIR") or os.path.join(tempfile.gettempdir(), "cairn-sessions")


def session_file(session_id):
    return os.path.join(SESSION_DIR, hashlib.sha1(str(session_id).encode()).hexdigest()[:16] + ".json")


def export_cli_path():
    """Claude Code: put the plugin's copy of the CLI on the session's PATH."""
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    bindir = os.environ.get("CAIRN_BIN_DIR") or (os.path.join(os.environ["CLAUDE_PLUGIN_ROOT"], "bin")
                                               if os.environ.get("CLAUDE_PLUGIN_ROOT") else None)
    if env_file and bindir:
        with open(env_file, "a", encoding="utf-8") as f:
            f.write(f'export PATH="{bindir}:$PATH"\n')


def session_resumed(session_id, data):
    """True when the session-start hook fires for a session that already has its state: a resume, not a new start."""
    if session_id == "default" or (data.get("source") or "").lower() in ("clear", "compact"):
        return False
    try:
        with open(session_file(session_id), encoding="utf-8") as f:
            return isinstance(json.load(f), dict)
    except (OSError, ValueError):
        return False


def ledger_configured(cfg):
    """Opt-in: only repos that already know about a ledger get context (no network for other repos)."""
    return bool(git_config("cairn.remote") or cfg.client or rev_parse(cfg.ledger_ref))


def paths_in(value, root, cwd):
    """File paths mentioned by a tool call: explicit path fields, shell commands, or patch headers."""
    found, strings = [], []

    def walk(v):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
        elif isinstance(v, str):
            strings.append(v)
    walk(value)
    for sv in strings:
        cands = [sv] if "\n" not in sv and len(sv) < 300 else []
        cands += re.findall(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", sv, re.M)
        cands += [t.strip("'\"") for t in re.split(r"[\s;|&<>()]+", sv) if "/" in t or "." in t][:40]
        for cand in cands:
            full = cand if os.path.isabs(cand) else os.path.join(cwd or root, cand)
            full = os.path.normpath(full)
            if full.startswith(root + os.sep) and os.path.isfile(full):
                rel = os.path.relpath(full, root)
                if rel not in found:
                    found.append(rel)
    return found[:10]


def hook_output(fmt, event, text):
    if not text:
        return ""
    if fmt == "text":
        return text
    return json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}})


def read_hook_input(timeout=2.0):
    """Harnesses send one JSON object on stdin and close it. Never wait forever if nothing arrives."""
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        import select
        if not select.select([sys.stdin], [], [], timeout)[0]:
            return {}
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except (ValueError, OSError):
        return {}


def append_metric(git_dir, entry):
    try:
        with open(os.path.join(git_dir, "cairn-metrics.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def transcript_usage(path):
    """Best-effort token totals from a harness transcript (JSONL). Returns None if nothing recognisable."""
    if not path or not os.path.exists(path):
        return None
    totals, cumulative, seen = Counter(), None, set()
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                stack = [(obj, None, None)]
                while stack:
                    v, parent_key, msg_id = stack.pop()
                    if isinstance(v, dict):
                        mid = v.get("id") if isinstance(v.get("id"), str) else msg_id
                        if "input_tokens" in v and "output_tokens" in v:
                            nums = {k: v.get(k) or 0 for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                                                                "cache_creation_input_tokens", "cached_input_tokens")
                                    if isinstance(v.get(k, 0), (int, float))}
                            if parent_key == "total_token_usage":
                                cumulative = nums
                            elif (mid, tuple(sorted(nums.items()))) not in seen:
                                seen.add((mid, tuple(sorted(nums.items()))))
                                totals.update(nums)
                            continue
                        stack += [(x, k, mid) for k, x in v.items()]
                    elif isinstance(v, list):
                        stack += [(x, parent_key, msg_id) for x in v]
    except OSError:
        return None
    use = cumulative or (dict(totals) if totals else None)
    if not use:
        return None
    use["total"] = sum(v for k, v in use.items() if k != "total")
    return use


def cmd_hook(ctx_args):
    """Entry point for harness adapters (Claude Code, Codex, OpenCode, Pi, ...). Never fails the agent's session."""
    args = ctx_args
    data = read_hook_input()
    sid = args.session or data.get("session_id") or data.get("sessionID") or os.environ.get("CAIRN_SESSION_ID") or "default"
    cwd = data.get("cwd") or os.getcwd()
    fmt = args.format
    try:
        if args.event == "session-start":
            # A resumed session keeps what it was already given: the harness fires this hook again (Codex does on
            # every turn), and starting over would repeat the orientation and every area's context. After a clear
            # or a compaction the earlier context is gone from the conversation, so those do start over.
            if session_resumed(sid, data):
                export_cli_path()
                return 0
            os.chdir(cwd)
            cfg = Config(offline=False)
            global CACHE_DIR
            CACHE_DIR = cfg.git_dir
            if not ledger_configured(cfg):
                return 0
            tip = fetch(cfg)
            if not tip:
                return 0
            L = Ledger(tip)
            ix = Index(L, cfg)
            hold = float(cfg.settings.get("context_holdout", 0) or 0)
            arm = "control" if int(hashlib.sha1(sid.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF < hold else "context"
            budgets = cfg.settings.get("context_budget", {})
            state = {"root": cfg.root, "git_dir": cfg.git_dir, "arm": arm, "started": iso(now()), "ledger": tip,
                     "areas": {a["id"]: a.get("globs", []) for a in L.areas()}, "served": [], "served_tokens": 0,
                     "area_budget": budgets.get("area", 450)}
            text = orientation(ix, cfg, budgets.get("session", 900)) if arm == "context" else ""
            notice = update_notice(cfg.settings) if arm == "context" else ""
            if notice:
                text += "\n" + notice
            state["served_tokens"] += est_tokens(text)
            os.makedirs(SESSION_DIR, exist_ok=True)
            with open(session_file(sid), "w", encoding="utf-8") as f:
                json.dump(state, f)
            export_cli_path()
            out = text if fmt == "text" else hook_output(fmt, "SessionStart", text)
            if out:
                print(out)
            return 0
        if args.event in ("tool", "file"):
            sf = session_file(sid)
            if not os.path.exists(sf):
                return 0
            with open(sf, encoding="utf-8") as f:
                state = json.load(f)
            if state.get("arm") != "context":
                return 0
            root = state["root"]
            files = args.path or paths_in(data.get("tool_input") or data.get("args") or data.get("input") or {}, root, cwd)
            want_areas, want_files = [], []
            for p in files:
                rel = os.path.relpath(p, root) if os.path.isabs(p) else p
                best = max(((max(glob_specificity(g) for g in gl if glob_re(g).match(rel)), a)
                            for a, gl in state["areas"].items() if any(glob_re(g).match(rel) for g in gl)),
                           default=None)
                if best and f"area:{best[1]}" not in state["served"] and best[1] not in want_areas:
                    want_areas.append(best[1])
                if f"file:{rel}" not in state["served"]:
                    want_files.append(rel)
            if not want_areas and not want_files:
                return 0
            os.chdir(root)
            cfg = Config(offline=True)
            CACHE_DIR = cfg.git_dir
            tip = rev_parse(cfg.ledger_ref)
            cfg.use_ledger_settings(json.loads(blob_text(tip, SETTINGS_FILE) or "{}") if tip else {})
            if not tip:
                return 0
            ix = Index(Ledger(tip), cfg)
            seen = set(state.get("served_ids", []))
            parts = [file_context(ix, p, seen) for p in want_files] + \
                    [area_context(ix, a, state.get("area_budget", 450), seen) for a in want_areas]
            state["served_ids"] = sorted(seen)
            text = "\n".join(x for x in parts if x)
            state["served"] += [f"file:{p}" for p in want_files] + [f"area:{a}" for a in want_areas]
            state["served_tokens"] += est_tokens(text)
            with open(sf, "w", encoding="utf-8") as f:
                json.dump(state, f)
            out = hook_output(fmt, "PostToolUse", text)
            if out:
                print(out)
            return 0
        if args.event == "session-end":
            sf = session_file(sid)
            if not os.path.exists(sf):
                return 0
            with open(sf, encoding="utf-8") as f:
                state = json.load(f)
            usage = None
            if args.usage:
                usage = json.loads(args.usage)
            else:
                usage = transcript_usage(data.get("transcript_path") or args.transcript)
            append_metric(state["git_dir"], {"session": hashlib.sha1(sid.encode()).hexdigest()[:12], "arm": state["arm"],
                                             "started": state["started"], "ended": iso(now()),
                                             "harness": args.harness or data.get("harness") or "unknown",
                                             "context_tokens": state["served_tokens"],
                                             "areas_served": len([x for x in state["served"] if x.startswith("area:")]),
                                             "usage": usage})
            os.remove(sf)
            return 0
    except Exception as e:  # noqa: BLE001 - a hook must never break the agent's session
        print(f"cairn hook {args.event}: {e}", file=sys.stderr)
        return 0
    return 0


WRITE_STATS = {"retries": 0}    # pushes this process had to redo because someone else wrote first


def log_event(cfg, cmd, code, data, why=None):
    """Local log of CLI outcomes, so `cairn metrics` can count duplicate work avoided."""
    entry = {"t": iso(now()), "cmd": cmd, "exit": code}
    if cmd == "check" and isinstance(data, dict):
        entry["verdict"] = data.get("verdict")
    if why:
        entry["why"] = " ".join(str(why).split())[:160]
    if WRITE_STATS["retries"]:
        entry["retries"] = WRITE_STATS["retries"]
    try:
        with open(os.path.join(cfg.git_dir, "cairn-events.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def summarize_sessions(rows):
    out = {}
    for arm in ("context", "control"):
        rs = [r for r in rows if r.get("arm") == arm]
        tot = sorted(r["usage"]["total"] for r in rs if r.get("usage") and r["usage"].get("total"))
        out[arm] = {"sessions": len(rs), "with_usage": len(tot),
                    "median_tokens": tot[len(tot) // 2] if tot else None,
                    "mean_tokens": round(sum(tot) / len(tot)) if tot else None,
                    "mean_context_tokens": round(sum(r.get("context_tokens", 0) for r in rs) / len(rs)) if rs else None}
    return out


def cmd_metrics(ctx):
    """Is Cairn paying off? Token use with and without injected context, and duplicate work avoided."""
    cfg, args = ctx.cfg, ctx.args
    rows, events = [], []
    for name, sink in (("cairn-metrics.jsonl", rows), ("cairn-events.jsonl", events)):
        try:
            with open(os.path.join(cfg.git_dir, name), encoding="utf-8") as f:
                sink += [json.loads(l) for l in f if l.strip()]
        except (OSError, ValueError):
            pass
    verdicts = Counter(e.get("verdict") for e in events if e.get("cmd") == "check")
    blocked = Counter(e["cmd"] for e in events if e.get("exit") == EXIT_BLOCKED)
    mine = {"user": ctx.ident.user, "updated": iso(now()), "sessions": summarize_sessions(rows),
            "checks": dict(verdicts), "blocked": dict(blocked)}
    if args.publish:
        def mutate(L):
            return {f"metrics/{ctx.ident.user.lower()}.json": dump_json(mine)}
        write(cfg, ctx.ident, mutate, "cairn: metrics")
    data = {"mine": mine}
    lines = [f"Your sessions: " + ", ".join(f"{a}: {v['sessions']} (median {v['median_tokens'] or '?'} tokens, "
                                           f"{v['mean_context_tokens'] or 0} injected)" for a, v in mine["sessions"].items()),
             "Checks: " + (", ".join(f"{k} {v}" for k, v in verdicts.items()) or "none")
             + " — HELD/JOIN/COVERED are pieces of duplicate work avoided",
             "Blocked commands: " + (", ".join(f"{k} {v}" for k, v in blocked.items()) or "none")]
    if args.team:
        L = ctx.require_ledger()
        team = [json.loads(t) for p, t in L.texts.items() if p.startswith("metrics/")]
        agg = {"context": Counter(), "control": Counter()}
        for m in team:
            for arm, v in m.get("sessions", {}).items():
                if v.get("mean_tokens"):
                    agg[arm]["sessions"] += v["with_usage"]
                    agg[arm]["tokens"] += v["mean_tokens"] * v["with_usage"]
        team_view = {arm: (round(c["tokens"] / c["sessions"]) if c["sessions"] else None, c["sessions"]) for arm, c in agg.items()}
        data["team"] = {"people": len(team), "mean_tokens_by_arm": team_view}
        lines.append(f"Team ({len(team)} people): mean tokens per session with context "
                     f"{team_view['context'][0]} (n={team_view['context'][1]}), without {team_view['control'][0]} "
                     f"(n={team_view['control'][1]})")
    return data, "\n".join(lines), EXIT_OK


# ------------------------------------------------------------------ MCP server

MCP_TOOLS = [
    ("cairn_context", "What other agents already know about these files or areas (notes, live findings, work in progress). "
     "Call before exploring an unfamiliar part of the repo.",
     {"paths": {"type": "array", "items": {"type": "string"}}, "areas": {"type": "array", "items": {"type": "string"}},
      "budget": {"type": "integer"}}, []),
    ("cairn_check", "Before starting work: PROCEED, HELD (someone has it), JOIN (already found) or COVERED.",
     {"intent": {"type": "string"}, "area": {"type": "string"}, "lens": {"type": "string"},
      "paths": {"type": "array", "items": {"type": "string"}}}, ["intent"]),
    ("cairn_claim", "Reserve a unit of work (key like audit/<area>/<lens>).",
     {"key": {"type": "string"}, "intent": {"type": "string"}, "ttl": {"type": "string"}}, ["key"]),
    ("cairn_release", "Release a claim you hold.", {"key": {"type": "string"}}, ["key"]),
    ("cairn_submit", "Record a finding, report, note, benchmark or task. `record` is the JSON object described in the skill.",
     {"kind": {"type": "string", "enum": ["finding", "report", "note", "benchmark", "task"]}, "record": {"type": "object"},
      "release": {"type": "string"}, "not_duplicate": {"type": "string"},
      "dry_run": {"type": "boolean", "description": "run every check and write nothing"}}, ["kind", "record"]),
    ("cairn_review", "Confirm, refute, or mark a record fixed/duplicate/stale/retract.",
     {"target": {"type": "string"}, "verdict": {"type": "string", "enum": VERDICTS},
      "method": {"type": "string", "enum": METHODS}, "note": {"type": "string"}}, ["target", "verdict"]),
    ("cairn_show", "Full record with status and freshness.", {"id": {"type": "string"}}, ["id"]),
    ("cairn_search", "Search records.", {"query": {"type": "string"}}, ["query"]),
    ("cairn_guide", "Project rules for agents working in this repo.", {}, []),
    ("cairn_map", "This project's areas, lenses and facets: the ids records must use.", {}, []),
    ("cairn_status", "List records, newest first; filter by kind, area or lens.",
     {"kind": {"type": "string"}, "area": {"type": "string"}, "lens": {"type": "string"},
      "limit": {"type": "integer"}}, []),
    ("cairn_whoami", "Show who you write as, or set your agent name once as <harness>/<model>.",
     {"agent": {"type": "string"}}, []),
]


def mcp_call(name, a, root):
    argv = {"cairn_context": ["context"] + sum((["--paths", p] for p in a.get("paths") or []), [])
            + sum((["--area", x] for x in a.get("areas") or []), []) + (["--budget", str(a["budget"])] if a.get("budget") else []),
            "cairn_check": ["check", a.get("intent", "")] + (["--area", a["area"]] if a.get("area") else [])
            + (["--lens", a["lens"]] if a.get("lens") else []) + (["--paths", *a["paths"]] if a.get("paths") else []),
            "cairn_claim": ["claim", a.get("key", ""), "--intent", a.get("intent", "")] + (["--ttl", a["ttl"]] if a.get("ttl") else []),
            "cairn_release": ["release", a.get("key", "")],
            "cairn_submit": ["submit", a.get("kind", ""), "-"] + (["--release", a["release"]] if a.get("release") else [])
            + (["--not-duplicate", a["not_duplicate"]] if a.get("not_duplicate") else [])
            + (["--dry-run"] if a.get("dry_run") else []),
            "cairn_map": ["map"],
            "cairn_status": ["status"] + (["--kind", a["kind"]] if a.get("kind") else [])
            + (["--area", a["area"]] if a.get("area") else []) + (["--lens", a["lens"]] if a.get("lens") else [])
            + (["--limit", str(a["limit"])] if a.get("limit") else []),
            "cairn_whoami": ["whoami"] + (["--agent", a["agent"]] if a.get("agent") else []),
            "cairn_review": ["review", a.get("target", ""), a.get("verdict", "")] + (["--method", a["method"]] if a.get("method") else [])
            + (["--note", a["note"]] if a.get("note") else []),
            "cairn_show": ["show", a.get("id", "")], "cairn_search": ["search", a.get("query", "")], "cairn_guide": ["guide"]}[name]
    stdin = json.dumps(a.get("record") or {}) if name == "cairn_submit" else ""
    p = subprocess.run([sys.executable, os.path.abspath(__file__), "-C", root] + argv, input=stdin, capture_output=True,
                       text=True, env=dict(os.environ, CAIRN_AGENT=os.environ.get("CAIRN_AGENT", "mcp")))
    return (p.stdout + (("\n" + p.stderr) if p.stderr.strip() else "")).strip() or "(no output)", p.returncode == EXIT_ERROR


def cmd_mcp(args):
    """Minimal MCP server over stdio (JSON-RPC 2.0, one message per line) for harnesses that speak MCP."""
    root = os.path.abspath(args.repo or os.getcwd())
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid, method = msg.get("id"), msg.get("method")
        if mid is None:
            continue
        if method == "initialize":
            res = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2025-06-18"),
                   "capabilities": {"tools": {}}, "serverInfo": {"name": "cairn", "version": VERSION}}
        elif method == "ping":
            res = {}
        elif method == "tools/list":
            res = {"tools": [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": props,
                                                                             "required": req}}
                             for n, d, props, req in MCP_TOOLS]}
        elif method == "tools/call":
            params = msg.get("params") or {}
            if params.get("name") not in {t[0] for t in MCP_TOOLS}:
                res = {"content": [{"type": "text", "text": f"unknown tool {params.get('name')}"}], "isError": True}
            else:
                text, err = mcp_call(params["name"], params.get("arguments") or {}, root)
                res = {"content": [{"type": "text", "text": text}], "isError": err}
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}}),
                  flush=True)
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": mid, "result": res}), flush=True)
    return 0


def cell_mark(c, fr):
    if not c:
        return "·"
    stale = c["commits_since"] is not None and c["commits_since"] > fr.get("covered_max_commits", 5)
    if c["live_findings"]:
        return f"{c['live_findings']}!" + ("~" if stale else "")
    if c["depth"] == "spot":
        return "·"
    return "~" if stale else "✓"


def cmd_coverage(ctx):
    L = ctx.require_ledger()
    ix, args = Index(L, ctx.cfg), ctx.args
    areas = args.area or L.area_ids()
    cells = ix.coverage(areas)
    lenses = L.lens_ids()
    fr = ctx.cfg.freshness
    w = max([len(a) for a in areas] + [6])
    lines = [" " * (w + 2) + " ".join(f"{l[:5]:>5}" for l in lenses)]
    for a in areas:
        lines.append(f"{a:<{w}}  " + " ".join(f"{cell_mark(cells.get((a, l)), fr):>5}" for l in lenses))
    lines.append(f"✓ covered (≤{fr.get('covered_max_commits', 5)} commits since)  ~ stale  N! live findings  · not examined")
    data = {"cells": list(cells.values()), "areas": areas, "lenses": lenses}
    return data, "\n".join(lines), EXIT_OK


def stats(xs):
    s = sorted(xs)
    n = len(s)
    med = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    return {"n": n, "median": round(med, 4), "min": s[0], "max": s[-1]}


def cmd_bench(ctx):
    L = ctx.require_ledger()
    ix, args = Index(L, ctx.cfg), ctx.args
    runs = [r for r in L.of_kind("benchmark") if r.get("bench") == args.bench and ix.status(r["id"]) not in GONE_STATES]
    if args.env:
        runs = [r for r in runs if r.get("env", {}).get("class") == args.env]
    groups, first = defaultdict(lambda: defaultdict(list)), {}
    for r in sorted(runs, key=lambda r: r["created_at"]):
        g = (r["env"]["class"], r["commit"])
        first.setdefault(g, r["created_at"])
        for m in r.get("metrics", []):
            groups[g][(m["name"], m.get("unit", ""), m.get("better"))].extend(m["samples"])
    meta = L.bench(args.bench) or {}
    lines, out = [f"{args.bench}: {meta.get('command', '(unregistered)')}"], []
    for g in sorted(groups, key=lambda g: (g[0], first[g])):
        for (name, unit, better), xs in groups[g].items():
            st = stats(xs)
            out.append(dict(st, env=g[0], commit=g[1], metric=name, unit=unit, better=better))
            lines.append(f"  {g[0]:<28} {short(g[1])}  {name:<14} median {st['median']}{unit}  "
                         f"[{st['min']}–{st['max']}]  n={st['n']}"
                         + ("  (enough samples)" if st['n'] >= BENCH_ENOUGH_SAMPLES else ""))
    if not out:
        lines.append("  no runs yet")
    return {"bench": args.bench, "series": out}, "\n".join(lines), EXIT_OK


def env_fingerprint():
    system, machine = platform.system().lower(), platform.machine().lower()
    cores, cpu = os.cpu_count() or 0, platform.processor() or ""
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as f:
            for line in f:
                if line.lower().startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    if not cpu and system == "darwin":
        cpu = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
    mem_gb = 0
    try:
        mem_gb = round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30)
    except (ValueError, OSError, AttributeError):
        pass
    h = hashlib.sha1(cpu.encode()).hexdigest()[:4]
    return {"class": f"{system}-{machine}-{cores}c-{mem_gb}g-{h}", "os": system, "arch": machine, "cores": cores,
            "mem_gb": mem_gb, "cpu": cpu, "python": platform.python_version()}


def cmd_env(ctx):
    e = env_fingerprint()
    return e, f"{e['class']}  ({e['cpu'] or 'unknown cpu'}, {e['cores']} cores, {e['mem_gb']} GB)", EXIT_OK


MAP_SECTIONS = {"area": "areas", "areas": "areas", "lens": "lenses", "lenses": "lenses", "facet": "facets",
                "facets": "facets", "benchmark": "benchmarks", "benchmarks": "benchmarks", "term": "glossary",
                "glossary": "glossary"}


def cmd_map(ctx):
    args = ctx.args
    if args.action in ("add", "rm"):
        section = MAP_SECTIONS.get(args.section or "")
        if not section:
            raise CairnError(f"section must be one of {sorted(set(MAP_SECTIONS))}")

        def mutate(L):
            m = json.loads(json.dumps(L.map))
            if args.action == "add":
                try:
                    entry = json.loads(args.value)
                except (TypeError, ValueError) as e:
                    raise CairnError(f"value must be JSON: {e}")
                if section in ("glossary", "facets"):
                    if not isinstance(entry, dict):
                        raise CairnError(f'{section} entries look like {{"id": {{...}}}}' if section == "facets"
                                         else 'glossary entries look like {"term": "definition"}')
                    m.setdefault(section, {}).update(entry)
                else:
                    items = m.setdefault(section, [])
                    if any(x.get("id") == entry.get("id") for x in items):
                        if not args.replace:
                            raise Blocked(f"{section} already has {entry.get('id')}; use --replace")
                        items[:] = [x for x in items if x.get("id") != entry.get("id")]
                    entry.setdefault("description", "")
                    if section == "areas":
                        entry.setdefault("owners", [])
                    items.append(entry)
            else:
                if section in ("glossary", "facets"):
                    m.get(section, {}).pop(args.value, None)
                else:
                    m[section] = [x for x in m.get(section, []) if x.get("id") != args.value]
            problems = validate_map(m)
            if problems:
                raise CairnError("map rejected: " + "; ".join(problems))
            return {MAP_FILE: dump_json(m)}, m

        commit, _, m = write(ctx.cfg, ctx.ident, mutate, f"cairn: map {args.action} {section}")
        return {"map": m}, f"UPDATED map.json ({args.action} {section}) at ledger {short(commit)}", EXIT_OK
    L = ctx.require_ledger()
    if args.action == "check":
        files = git("ls-tree", "-r", "--name-only", ctx.cfg.main_sha() or "HEAD").splitlines()
        unmapped = [f for f in files if not L.where(f)]
        dirs = Counter(f.split("/")[0] if "/" in f else "(root)" for f in unmapped)
        lines = [f"{len(unmapped)} of {len(files)} files are in no area"]
        lines += [f"  {d}: {n} files" for d, n in dirs.most_common(20)]
        data = {"unmapped": unmapped}
        if unmapped and args.post_task:
            ix = Index(L, ctx.cfg)
            title = f"Map {len(unmapped)} unmapped files into areas"
            if any(r["title"].startswith("Map ") and "unmapped files" in r["title"] and ix.status(r["id"]) in ("open", "claimed")
                   for r in L.of_kind("task")):
                lines.append("an open mapping task already exists")
            else:
                def mutate(L2):
                    body = "Files no area covers (top folders): " + ", ".join(f"{d} ({n})" for d, n in dirs.most_common(10)) \
                           + ".\nAdd or widen areas with `cairn map add area ... --replace`."
                    rec, _ = finalize("task", {"title": title, "priority": "p3", "body": body}, ctx, L2)
                    return {record_path(rec): serialize_record(rec)}, rec["id"]
                _, _, tid = write(ctx.cfg, ctx.ident, mutate, "cairn: map drift task")
                lines.append(f"posted task {tid}")
                data["task"] = tid
        return data, "\n".join(lines), (EXIT_OK if not unmapped else EXIT_BLOCKED)
    m = L.map
    lines = ["Areas"] + [f"  {a['id']:<20} {', '.join(a.get('globs', []))}"
                         + (f"  - {a['description']}" if a.get("description") else "") for a in m.get("areas", [])]
    lines += ["Lenses"] + [f"  {l['id']:<16} {l.get('description', '')}" + ("  [sensitive]" if l.get("sensitive") else "")
                           for l in m.get("lenses", [])]
    if m.get("facets"):
        lines += ["Facets"] + [f"  {k:<16} {v.get('description', '')}  values: {', '.join(v.get('values', [])) or 'free'}"
                               for k, v in m["facets"].items()]
    if m.get("benchmarks"):
        lines += ["Benchmarks"] + [f"  {b['id']:<22} {b.get('command')}  metrics: "
                                   + ", ".join(f"{x.get('name')} ({x.get('unit', '')}, {x.get('better')} is better)"
                                               for x in b.get("metrics", [])) for b in m["benchmarks"]]
    if m.get("glossary"):
        lines += ["Glossary"] + [f"  {k}: {v}" for k, v in sorted(m["glossary"].items())]
    return m, "\n".join(lines), EXIT_OK


def cmd_where(ctx):
    L = ctx.require_ledger()
    out = {p: L.where(p) for p in ctx.args.paths}
    return out, "\n".join(f"{p}: {', '.join(a) or '(no area)'}" for p, a in out.items()), EXIT_OK


def check_change(cfg, L, base, head, actor, t, allow_workflow, label=""):
    """Rules for one change to the ledger: append-only records, claim ownership, authorship, known paths."""
    errors = []
    is_bot = bool(actor) and actor.lower() in cfg.bot_logins
    for line in git("diff", "--name-status", "--no-renames", base or EMPTY_TREE, head).splitlines():
        status, path = line.split("\t", 1)
        where = f"{label}{path}"
        if path.startswith("records/"):
            if status != "A":
                errors.append(f"{where}: records are append-only (status {status})")
                continue
            if path.endswith(".patch"):
                if blob_text(head, path[:-len(".patch")] + ".md") is None:
                    errors.append(f"{where}: a .patch needs its change record next to it")
                continue
            text = blob_text(head, path) or ""
            try:
                rec = parse_record(text)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{where}: {e}")
                continue
            errors += [f"{where}: {p}" for p in validate_record(rec, L)]
            if path != record_path(rec):
                errors.append(f"{where}: should be at {record_path(rec)}")
            if actor and not is_bot and rec.get("author", {}).get("github", "").lower() != actor.lower():
                errors.append(f"{where}: author {rec.get('author', {}).get('github')} is not the writer {actor}")
            leaks = scan_secrets(text)
            if leaks:
                errors.append(f"{where}: possible secret ({', '.join(leaks)})")
        elif path.startswith("claims/"):
            try:
                old = json.loads(blob_text(base, path)) if base and status in ("M", "D") else None
                new = json.loads(blob_text(head, path)) if status in ("A", "M") else None
            except (TypeError, ValueError) as e:
                errors.append(f"{where}: {e}")
                continue
            if actor and not is_bot:
                if new and new.get("holder", {}).get("github", "").lower() != actor.lower():
                    errors.append(f"{where}: claim holder is not the writer {actor}")
                if old and not claim_expired(old, t) and old.get("holder", {}).get("github", "").lower() != actor.lower():
                    errors.append(f"{where}: changed a live claim held by {old['holder'].get('github')}")
        elif path in (MAP_FILE, SETTINGS_FILE, GUIDE_FILE, "README.md"):
            continue
        elif path.startswith("metrics/") and path.endswith(".json"):
            owner = path[len("metrics/"):-len(".json")]
            if actor and not is_bot and owner != actor.lower():
                errors.append(f"{where}: only {owner} may write their metrics")
        elif path.startswith(".github/"):
            if not allow_workflow:
                errors.append(f"{where}: workflow files are not accepted on the ledger branch")
        else:
            errors.append(f"{where}: unexpected path on the ledger branch")
    return errors


def commit_time(c):
    return parse_ts(git("show", "-s", "--format=%cd", "--date=format-local:%Y-%m-%dT%H:%M:%SZ", c,
                        env={"TZ": "UTC"}).strip())


def cmd_validate(ctx):
    args, cfg = ctx.args, ctx.cfg
    needs_history = bool(args.base or args.since)
    head = rev_parse(args.head) if args.head else fetch(cfg, full=needs_history)
    if not head:
        raise CairnError("nothing to validate")
    if args.head and needs_history and not cfg.offline:
        fetch(cfg, full=True)
    L = Ledger(head)
    errors = list(L.errors)
    if L.map_error:
        errors.append(L.map_error)
    errors += [f"{MAP_FILE}: {p}" for p in validate_map(L.map)]
    errors += [f"{SETTINGS_FILE}: {p}" for p in validate_settings(L.settings)]
    for rid, r in L.records.items():
        path = L.paths[rid]
        errors += [f"{path}: {p}" for p in validate_record(r)]
        if r.get("kind") in KIND_PREFIX and isinstance(rid, str) and path != record_path(r):
            errors.append(f"{path}: should be at {record_path(r)}")
    for path, text in L.texts.items():
        if path.startswith("records/"):
            rid = os.path.basename(path)[:-len(".patch")] if path.endswith(".patch") else None
            if not rid or rid not in L.records or L.paths[rid][:-3] + ".patch" != path:
                errors.append(f"{path}: only <change id>.patch files may sit next to records")
        if path.startswith("claims/"):
            try:
                errors += validate_claim(json.loads(text), path)
            except ValueError as e:
                errors.append(f"{path}: {e}")
    checked = 0
    if args.base:  # one push: the pusher is known (per-push CI)
        base = rev_parse(args.base)
        if not base:
            raise CairnError(f"unknown base {args.base}")
        errors += check_change(cfg, L, base, head, args.actor, commit_time(head), args.allow_workflow)
        checked = 1
    elif args.since:  # daily audit: each commit against its parent, writer = commit author
        commits = git("rev-list", "--first-parent", f"--since={args.since} days ago", head).split()
        for c in commits:
            parent = rev_parse(c + "^")
            actor = git("show", "-s", "--format=%an", c).strip()
            errors += check_change(cfg, L, parent, c, actor, commit_time(c), args.allow_workflow, f"{short(c)} ")
        checked = len(commits)
    text = (f"OK: ledger is valid" + (f" ({checked} commit(s) checked)" if checked else "")) if not errors \
        else f"{len(errors)} problems:\n  " + "\n  ".join(errors)
    return {"ok": not errors, "errors": errors, "head": head, "commits_checked": checked}, text, \
        (EXIT_OK if not errors else EXIT_ERROR)


def cmd_fixes(ctx):
    """Turn `Cairn-Fixes: F-…` trailers in recent commits on the main branch into `fixed` reviews (idempotent)."""
    cfg, args = ctx.cfg, ctx.args
    ctx.require_ledger()
    main = rev_parse(cfg.main_ref) or rev_parse("HEAD")
    log = git("log", "--format=%H%x1f%B%x1e", f"--since={args.since} days ago", main)
    pairs = []
    for entry in log.split("\x1e"):
        if "\x1f" not in entry:
            continue
        sha, body = entry.strip("\n").split("\x1f", 1)
        for m in re.finditer(r"^Cairn-Fixes:\s*(.+)$", body, re.M):
            pairs += [(sha.strip(), i.upper()) for i in re.split(r"[,\s]+", m.group(1).strip()) if ID_RE.match(i.upper())]

    def mutate(L):
        done = {(v.get("target"), v.get("commit")) for v in L.of_kind("review") if v.get("verdict") == "fixed"}
        changes, made = {}, []
        for sha, rid in pairs:
            if rid not in L.records or (rid, sha) in done:
                continue
            rec, _ = review_record(ctx, L, rid, "fixed", "checked-evidence", f"Fixed by {sha} (Cairn-Fixes trailer)",
                                   commit=sha)
            changes[record_path(rec)] = serialize_record(rec)
            made.append(rid)
            done.add((rid, sha))
        return changes, made

    if not pairs:
        return {"fixed": []}, f"no Cairn-Fixes trailers on {cfg.main_branch} in the last {args.since} days", EXIT_OK
    _, _, made = write(cfg, ctx.ident, mutate, "cairn: fixed reviews from Cairn-Fixes trailers")
    return {"fixed": made}, f"marked fixed: {', '.join(made) if made else 'nothing new'}", EXIT_OK


def render_markdown(L, ix):
    t = ix.now
    fr = ix.cfg.freshness
    lines = ["# Cairn ledger", "",
             f"_Generated {iso(t)} at ledger `{short(L.commit)}`, {ix.cfg.main_branch} `{short(ix.main)}`. "
             f"Do not edit by hand._", ""]
    kinds = Counter(r["kind"] for r in L.records.values())
    lines.append(" · ".join(f"**{v}** {k}s" for k, v in sorted(kinds.items())) + f" · **{len(ix.active)}** active claims")
    cells = ix.coverage()
    lenses = L.lens_ids()
    lines += ["", "## Coverage", "", "| area | note | " + " | ".join(lenses) + " |", "|---" * (len(lenses) + 2) + "|"]

    def note_mark(area):   # the note agents are shown for this area, and whether it can still be taken on trust
        n = note_for(ix, area)
        if not n:
            return "·"
        state = ix.freshness(n)["state"]
        return f"[`{n['id']}`]({L.paths[n['id']]})" + ("" if state in ("fresh", "unknown") else f" ({state})")

    for a in L.area_ids():
        lines.append(f"| {a} | {note_mark(a)} | " + " | ".join(cell_mark(cells.get((a, l)), fr) for l in lenses) + " |")
    lines += ["", "note: the note that explains how the area works, marked when it needs re-checking · "
              f"✓ covered · ~ stale (>{fr.get('covered_max_commits', 5)} commits since) · N! live findings · · none / not examined"]
    tasks = sorted([r for r in L.of_kind("task") if ix.status(r["id"]) in ("open", "claimed")],
                   key=lambda r: (r.get("priority", "p9"), r["created_at"]))
    lines += ["", "## Open tasks", ""]
    lines += [f"- `{r['id']}` {r.get('priority')} {r['title']} ({ix.status(r['id'])})" for r in tasks[:50]] or ["None."]
    lines += ["", "## Active claims", ""]
    lines += [f"- `{c['key']}` {c['holder']['github']} ({c['holder'].get('agent')}): {c.get('intent', '')} "
              f"(until {c['expires_at']})" for c in sorted(ix.active.values(), key=lambda c: c["key"])] or ["None."]
    live = sorted([r for r in L.of_kind("finding") if ix.status(r["id"]) in LIVE_STATES],
                  key=lambda r: (-sev_rank(r), r["created_at"]))
    rel_col = f" in {ix.cfg.release_branch} |" if ix.release else ""
    lines += ["", f"## Live findings ({len(live)})", "",
              "| id | status | severity | title | where | freshness |" + rel_col,
              "|---|---|---|---|---|---|" + ("---|" if ix.release else "")]
    for r in live[:200]:
        title = r["title"].replace("|", "\\|")
        row = (f"| [`{r['id']}`]({L.paths[r['id']]}) | {ix.status(r['id'])} | {r.get('severity')} | {title} | "
               f"`{loc_str(r)}` | {fresh_str(ix.freshness(r)) or 'fresh'} |")
        if ix.release:
            row += f" {ix.release_presence(r)} |"
        lines.append(row)
    if not live:
        lines.append("| | | | none | | |" + (" |" if ix.release else ""))
    recent = sorted(L.records.values(), key=lambda r: r["created_at"], reverse=True)[:15]
    lines += ["", "## Recent activity", ""]
    lines += [f"- {r['created_at']} `{r['id']}` {r['kind']} by {r['author']['github']}: {r['title']}" for r in recent]
    return "\n".join(lines) + "\n"


def cmd_render(ctx):
    if ctx.args.commit:
        def mutate(L):
            md = render_markdown(L, Index(L, ctx.cfg))
            old = L.texts.get("README.md", "")
            strip = lambda s: re.sub(r"_Generated .*?_", "", s)  # noqa: E731
            changes = {} if strip(old) == strip(md) else {"README.md": md}
            # The daily job always runs the newest Cairn, so this is where the ledger learns which version that is.
            # It only ever goes up; sessions on an older Cairn then get a notice to update.
            if version_tuple(VERSION) > version_tuple(L.settings.get("latest_version") or "0"):
                changes[SETTINGS_FILE] = dump_json(dict(L.settings, latest_version=VERSION))
            return changes, ("README.md" in changes)

        commit, _, rendered = write(ctx.cfg, ctx.ident, mutate, "cairn: render dashboard")
        text = ("RENDERED README.md at " + short(commit)) if commit and rendered else \
            (f"dashboard unchanged; recorded Cairn {VERSION} at {short(commit)}" if commit else "dashboard unchanged")
        return {"commit": commit}, text, EXIT_OK
    L = ctx.require_ledger()
    md = render_markdown(L, Index(L, ctx.cfg))
    return {"markdown": md}, md, EXIT_OK


def cmd_sweep(ctx):
    t = now()

    def mutate(L):
        gone = [k for k, c in L.claims.items() if claim_expired(c, t)]
        return {claim_path(k): None for k in gone}, gone

    commit, _, gone = write(ctx.cfg, ctx.ident, mutate, "cairn: sweep expired claims")
    return {"removed": gone}, f"removed {len(gone)} expired claims" + (f": {', '.join(gone)}" if gone else ""), EXIT_OK


def cmd_touching(ctx):
    L = ctx.require_ledger()
    ix, args = Index(L, ctx.cfg), ctx.args
    changed = set(args.paths or [])
    if args.diff:
        changed |= {p for p in git("diff", "--name-only", args.diff).splitlines() if p}
    hits = [r for r in L.of_kind("finding") if ix.status(r["id"]) in LIVE_STATES
            and {l.get("path") for l in r.get("locs") or []} & changed]
    hits.sort(key=lambda r: -sev_rank(r))
    areas = {a for p in changed for a in L.where(p)[:1]}
    claims = [c for c in ix.active.values() if claim_areas(c)[0] & areas]
    if args.markdown:
        if not hits and not claims:
            return {"findings": [], "claims": []}, "", EXIT_OK
        md = [f"### Cairn: {len(hits)} live finding(s) touch files in this change", ""]
        if hits:
            md += ["| id | status | severity | title | where |", "|---|---|---|---|---|"]
            md += [f"| `{r['id']}` | {ix.status(r['id'])} | {r.get('severity')} | {r['title'].replace('|', '/')} | "
                   f"`{loc_str(r)}` |" for r in hits]
            md += ["", "If this change fixes one, add a `Cairn-Fixes: <id>` trailer to the commit message."]
        if claims:
            md += ["", "Agents are working in these areas right now: " + ", ".join(
                f"`{c['key']}` ({c['holder']['github']})" for c in claims)]
        return {"findings": [rec_summary(ix, r, fresh=False) for r in hits], "claims": claims}, "\n".join(md), EXIT_OK
    lines = [f"{len(hits)} live findings touch {len(changed)} changed files"] + [rec_line(ix, r) for r in hits]
    if claims:
        lines += ["Active claims in those areas"] + [claim_line(c, now()) for c in claims]
    return {"findings": [rec_summary(ix, r, fresh=False) for r in hits], "claims": claims}, "\n".join(lines), EXIT_OK


# ------------------------------------------------------------------------- main

def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="machine-readable output")
    common.add_argument("--offline", action="store_true", default=argparse.SUPPRESS,
                        help="read the last fetched ledger; never touch the network")
    p = argparse.ArgumentParser(prog="cairn", parents=[common],
                                description="Shared context for the coding agents working on this repo: notes, findings, coverage, claims and work. Agents: follow the cairn skill (or the AGENTS.md section), then run `cairn guide`. "
                                            "Exit 2 = blocked: someone holds it, or it is already known.")
    p.add_argument("-C", dest="repo", help="run as if started in this directory")
    p.add_argument("--version", action="version", version=f"cairn {VERSION}")
    sub = p.add_subparsers(dest="cmd", required=True, metavar="command")

    def add(name, fn, help_):
        sp = sub.add_parser(name, parents=[common], help=help_, description=help_)
        sp.set_defaults(fn=fn)
        return sp

    sp = add("init", cmd_init, "create the ledger branch (once per project)")
    sp.add_argument("--profile", help="directory with map.json and optionally ledger.json and GUIDE.md")
    sp.add_argument("--map", help="start from this map.json")
    sp = add("whoami", cmd_whoami, "show or set your identity (GitHub login, agent name, session)")
    sp.add_argument("--user", help="your GitHub login (stored as git config cairn.user)")
    sp.add_argument("--agent", help="<harness>/<model>: the tool the agent runs in and the model it is, "
                                    "e.g. claude-code/claude-opus-5-5 (not a task or person's name)")
    sp.add_argument("--new-session", action="store_true", help="start a new session id for this worktree")
    sp = add("guide", cmd_guide, "print or replace this project's notes for agents (GUIDE.md in the ledger)")
    sp.add_argument("action", nargs="?", default="show", choices=["show", "set"])
    sp.add_argument("file", nargs="?", help="with set: the new guide, or - for standard input")
    sp = add("settings", cmd_settings, "show or change ledger settings (ledger.json)")
    sp.add_argument("action", nargs="?", default="show", choices=["show", "set"])
    sp.add_argument("key", nargs="?")
    sp.add_argument("value", nargs="?")
    sp = add("brief", cmd_brief, "orientation: areas, claims, tasks, findings, coverage")
    sp.add_argument("--area", action="append")
    sp.add_argument("--path", action="append")
    sp = add("check", cmd_check, "before starting work: PROCEED, HELD, JOIN or COVERED")
    sp.add_argument("intent", help="one line: what you are about to do")
    sp.add_argument("--area", action="append")
    sp.add_argument("--lens")
    sp.add_argument("--paths", nargs="*")
    sp = add("search", cmd_search, "ranked search over records and claims")
    sp.add_argument("query", nargs="?", default="")
    sp.add_argument("--kind", action="append", choices=list(KIND_PREFIX))
    sp.add_argument("--area", action="append")
    sp.add_argument("--lens")
    sp.add_argument("--facet", action="append", help="facet=value, e.g. platform=linux")
    sp.add_argument("--path")
    sp.add_argument("--state", action="append")
    sp.add_argument("--all", action="store_true", help="include retracted, duplicate and superseded records")
    sp.add_argument("--limit", type=int, default=15)
    sp = add("claim", cmd_claim, "take a lease on a unit of work")
    sp.add_argument("key")
    sp.add_argument("--intent", default="")
    sp.add_argument("--ttl", default=DEFAULT_TTL)
    sp.add_argument("--area", action="append")
    sp.add_argument("--lens")
    sp.add_argument("--paths", nargs="*")
    sp = add("heartbeat", cmd_heartbeat, "extend a lease you hold")
    sp.add_argument("key")
    sp.add_argument("--ttl", default=DEFAULT_TTL)
    sp = add("release", cmd_release, "drop a lease you hold")
    sp.add_argument("key")
    add("claims", cmd_claims, "list leases")
    sp = add("submit", cmd_submit, "write a record from JSON (file or - for stdin)")
    sp.add_argument("kind", choices=[k for k in KIND_PREFIX if k != "review"])
    sp.add_argument("file")
    sp.add_argument("--release", help="release this claim key in the same commit")
    sp.add_argument("--not-duplicate", metavar="REASON", help="override the duplicate check, with a reason")
    sp.add_argument("--commit", help="code commit the work examined (default: HEAD if pushed, else its merge-base)")
    sp.add_argument("--dry-run", action="store_true", help="run every check (fields, limits, duplicates, coverage) "
                                                           "and write nothing")
    sp = add("review", cmd_review, "confirm, refute, or mark a record fixed/duplicate/stale/retracted")
    sp.add_argument("target")
    sp.add_argument("verdict", choices=VERDICTS)
    sp.add_argument("--method", choices=METHODS)
    sp.add_argument("--note", default="")
    sp.add_argument("--of", help="for duplicate: the original record id")
    sp.add_argument("--commit", help="code commit you checked (default: HEAD if pushed, else its merge-base)")
    sp.add_argument("--resolves", help="task id this review answers")
    sp.add_argument("--release", help="release this claim key in the same commit")
    sp = add("task", cmd_task, "post an open question or job")
    sp.add_argument("title")
    sp.add_argument("--area", action="append")
    sp.add_argument("--lens")
    sp.add_argument("--priority", default="p2", choices=PRIORITIES)
    sp.add_argument("--body", default="")
    sp.add_argument("--paths", nargs="*")
    sp.add_argument("--not-duplicate", metavar="REASON")
    sp = add("next", cmd_next, "atomically claim the highest-priority open task")
    sp.add_argument("--area", action="append")
    sp.add_argument("--lens")
    sp.add_argument("--ttl", default=DEFAULT_TTL)
    sp = add("show", cmd_show, "one record with status, freshness, release presence and reviews")
    sp.add_argument("id")
    sp = add("status", cmd_status, "list records with derived status")
    sp.add_argument("--kind", action="append", choices=list(KIND_PREFIX))
    sp.add_argument("--area", action="append")
    sp.add_argument("--lens")
    sp.add_argument("--facet", action="append", help="facet=value")
    sp.add_argument("--state", action="append")
    sp.add_argument("--stale", action="store_true", help="only live records that are not fresh")
    sp.add_argument("--mine", action="store_true")
    sp.add_argument("--limit", type=int, default=50)
    sp = add("recheck", cmd_recheck, "re-verification queue, or re-run one finding's repro")
    sp.add_argument("id", nargs="?")
    sp.add_argument("--run", action="store_true", help="execute the repro (only allow-listed runners)")
    sp.add_argument("--file", action="store_true", help="file a confirms review if it still reproduces")
    sp.add_argument("--resolves", help="task id this re-check answers")
    sp.add_argument("--post-tasks", type=int, default=0, metavar="N", help="post tasks for the top N in the queue")
    sp.add_argument("--limit", type=int, default=20)
    sp = add("work", cmd_work, "code for findings: start, submit (patch-first), refresh, checkout")
    sp.add_argument("action", choices=["start", "submit", "refresh", "checkout"])
    sp.add_argument("target", nargs="?", help="start: finding/task id; refresh/checkout: change id")
    sp.add_argument("--alt", help="start: work on an alternative fix alongside someone else's")
    sp.add_argument("--worktree", metavar="PATH", help="use a new git worktree instead of switching this one")
    sp.add_argument("--no-branch", action="store_true", help="start: only reserve the work")
    sp.add_argument("--ttl", default="8h")
    sp.add_argument("--addresses", action="append", help="submit: finding or task id (repeatable)")
    sp.add_argument("--title")
    sp.add_argument("--body")
    sp.add_argument("--file", help="submit: optional JSON with extra fields (assumptions, tests, ...)")
    sp.add_argument("--test", action="append", metavar="CMD=pass|fail", help="submit: a test you ran and its result")
    sp.add_argument("--push-branch", action="store_true", help="submit: also push a branch (automatic for big changes)")
    sp.add_argument("--supersedes", help="submit: an earlier change this replaces")
    sp.add_argument("--all", action="store_true", help="refresh: every live change that drifted but applies cleanly")
    sp.add_argument("--post-task", action="store_true", help="refresh: post a rebase task for conflicting changes")
    sp = add("context", cmd_context, "budgeted context for an agent: orientation, or what's known about files/areas")
    sp.add_argument("--paths", nargs="*")
    sp.add_argument("--area", action="append")
    sp.add_argument("--budget", type=int, help="token budget (default from ledger settings)")
    sp = add("hook", None, "for harness adapters: session-start, tool (after a tool call), session-end")
    sp.add_argument("event", choices=["session-start", "tool", "file", "session-end"])
    sp.add_argument("--format", choices=["hooks-json", "text"], default="hooks-json",
                    help="hooks-json: Claude Code / Codex hook output; text: plain text for other adapters")
    sp.add_argument("--session", help="session id when the harness does not pass one on stdin")
    sp.add_argument("--path", action="append", help="file the agent opened (instead of a tool call on stdin)")
    sp.add_argument("--usage", help='session-end: token usage JSON, e.g. {"total": 51234}')
    sp.add_argument("--transcript", help="session-end: transcript path when not on stdin")
    sp.add_argument("--harness", help="session-end: harness name for metrics")
    sp = add("metrics", cmd_metrics, "token use with and without injected context; duplicate work avoided")
    sp.add_argument("--publish", action="store_true", help="share your summary in the ledger (metrics/<you>.json)")
    sp.add_argument("--team", action="store_true", help="compare everyone's published summaries")
    add("mcp", None, "run an MCP server on stdio (for MCP-capable harnesses)")
    sp = add("coverage", cmd_coverage, "area × lens grid")
    sp.add_argument("--area", action="append")
    sp = add("bench", cmd_bench, "merged stats for a registered benchmark")
    sp.add_argument("bench")
    sp.add_argument("--env")
    add("env", cmd_env, "this machine's benchmark environment class")
    sp = add("map", cmd_map, "show, check or extend the shared vocabulary")
    sp.add_argument("action", nargs="?", default="show", choices=["show", "check", "add", "rm"])
    sp.add_argument("section", nargs="?")
    sp.add_argument("value", nargs="?")
    sp.add_argument("--replace", action="store_true")
    sp.add_argument("--post-task", action="store_true", help="with check: post a task if files are unmapped")
    sp = add("where", cmd_where, "which area(s) a path belongs to")
    sp.add_argument("paths", nargs="+")
    sp = add("validate", cmd_validate, "check schema, append-only rules and authorship")
    sp.add_argument("--base", help="per-push mode: the ref before the push")
    sp.add_argument("--head")
    sp.add_argument("--actor", help="per-push mode: the GitHub login that pushed")
    sp.add_argument("--since", type=int, metavar="DAYS", help="audit mode: check every commit from the last DAYS")
    sp.add_argument("--allow-workflow", action="store_true")
    sp = add("fixes", cmd_fixes, "turn Cairn-Fixes commit trailers on the main branch into fixed reviews")
    sp.add_argument("--since", type=int, default=3, metavar="DAYS")
    sp = add("render", cmd_render, "build the dashboard (stdout, or --commit to the ledger README)")
    sp.add_argument("--commit", action="store_true")
    add("sweep", cmd_sweep, "delete expired leases now (writes do this anyway)")
    sp = add("touching", cmd_touching, "live findings on files in a diff (for PR comments)")
    sp.add_argument("--diff", help="git diff range, e.g. origin/main...HEAD")
    sp.add_argument("--paths", nargs="*")
    sp.add_argument("--markdown", action="store_true")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.repo:
        os.chdir(args.repo)
    want_json = getattr(args, "json", False)
    args.offline = getattr(args, "offline", False)
    if args.cmd == "task":
        args.not_duplicate = getattr(args, "not_duplicate", None)
    if args.cmd == "hook":
        return cmd_hook(args)
    if args.cmd == "mcp":
        return cmd_mcp(args)
    try:
        ctx = Ctx(args)
        data, text, code = args.fn(ctx)
        log_event(ctx.cfg, args.cmd, code, data)
    except Blocked as b:
        print(json.dumps({"blocked": str(b), **b.data}, indent=2, default=str) if want_json else str(b))
        try:
            log_event(ctx.cfg, args.cmd, EXIT_BLOCKED, None, why=b)
        except NameError:
            pass
        return EXIT_BLOCKED
    except CairnError as e:
        try:
            log_event(ctx.cfg, args.cmd, EXIT_ERROR, None, why=e)
        except NameError:
            pass
        if want_json:
            print(json.dumps({"error": str(e)}))
        print(f"error: {e}", file=sys.stderr)
        return EXIT_ERROR
    if want_json:
        print(json.dumps(data, indent=2, default=str, ensure_ascii=False))
    elif text:
        print(text)
    if args.cmd in WRITE_CMDS and code == EXIT_OK:
        hint = agent_hint(ctx.ident, ctx.cfg)
        if hint:
            print(hint, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
