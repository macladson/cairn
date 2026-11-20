#!/usr/bin/env python3
"""Stress the compare-and-swap path: many agents race for the same keys and submit at once.

    python3 tests/stress_cairn.py [agents-per-key] [keys] [submits]
"""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_cairn import Agent, finding, make_world  # noqa: E402


def main():
    per_key = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    n_keys = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    n_submits = int(sys.argv[3]) if len(sys.argv) > 3 else 24
    tmp = tempfile.mkdtemp(prefix="cairn-stress-")
    _, c = make_world(tmp)
    boot = Agent("alice", c["alice"])
    assert boot.run("init").returncode == 0
    people = ["alice", "bob", "carol"]
    agents = [Agent(people[i % 3], c[people[i % 3]], session=f"s-agent{i}") for i in range(per_key)]

    t0 = time.time()
    winners_ok = True
    for k in range(n_keys):
        key = f"topic/root/stress-{k}"
        procs = [a.popen("claim", key) for a in agents]
        codes = [p.wait() for p in procs]
        errs = [p.stderr.read() for p, code in zip(procs, codes) if code not in (0, 2)]
        ok = codes.count(0) == 1 and codes.count(2) == per_key - 1
        winners_ok &= ok
        print(f"{key}: {codes.count(0)} winner, {codes.count(2)} held, {len(errs)} errors" + (f" {errs[:1]}" if errs else ""))
    t_claims = time.time() - t0

    t0 = time.time()
    procs = []
    for i in range(n_submits):
        a = agents[i % per_key]
        pl = finding(f"Stress finding {i} about subsystem z{i}q{i * 7}", f"Distinct claim number {i} zz{i}k",
                     "README.md", str(i + 1), None, ("root", "docs"), "info")
        path = os.path.join(tmp, f"s{i}.json")
        with open(path, "w") as f:
            json.dump(pl, f)
        procs.append(a.popen("submit", "finding", path, "--not-duplicate", "stress test"))
    codes = [p.wait() for p in procs]
    t_submits = time.time() - t0
    bad = [(code, p.stderr.read()[:200]) for p, code in zip(procs, codes) if code != 0]
    code, st = boot.json("status", "--kind", "finding", "--limit", "1000")
    landed = len(st.get("records", []))
    print(f"\nclaims: {n_keys} keys x {per_key} agents in {t_claims:.1f}s; exactly one winner each: {winners_ok}")
    print(f"submits: {n_submits} concurrent in {t_submits:.1f}s; {landed} landed; failures: {bad[:3]}")
    return 0 if winners_ok and landed == n_submits and not bad else 1


if __name__ == "__main__":
    sys.exit(main())
