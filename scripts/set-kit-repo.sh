#!/bin/sh
# Record where this kit lives on GitHub, once, before publishing it:
#
#   scripts/set-kit-repo.sh <owner>/<repo>
#
# Fills the address into the job files (ci/*.yml) and the docs (README.md, docs/*.md), replacing the
# placeholders OWNER/cairn and <owner>/cairn. Safe to run again with a different address.
set -eu
KIT=$(cd "$(dirname "$0")/.." && pwd)
case "${1:-}" in
  */*) ;;
  *) sed -n '2,7p' "$0"; exit 2 ;;
esac
python3 - "$KIT" "$1" <<'PY'
import glob, os, re, sys
kit, new = sys.argv[1], sys.argv[2]
pat = re.compile(r"(vars\.CAIRN_KIT \|\| ')([^']+)(')")
m = pat.search(open(os.path.join(kit, "ci", "cairn-daily.yml")).read())
old = m.group(2) if m else "OWNER/cairn"
changed = []
for p in sorted(glob.glob(os.path.join(kit, "ci", "*.yml"))):
    s = open(p).read()
    t = pat.sub(lambda m: m.group(1) + new + m.group(3), s)
    if t != s:
        open(p, "w").write(t); changed.append(p)
for p in [os.path.join(kit, "README.md")] + sorted(glob.glob(os.path.join(kit, "docs", "*.md"))):
    if not os.path.exists(p):
        continue
    s = open(p).read()
    t = s.replace("<owner>/cairn", new)
    if old not in ("OWNER/cairn", new):
        t = t.replace(old, new)
    if t != s:
        open(p, "w").write(t); changed.append(p)
print(f"kit address set to {new}" + (f" (was {old})" if old != "OWNER/cairn" else ""))
for p in changed:
    print("  updated", os.path.relpath(p, kit))
PY
