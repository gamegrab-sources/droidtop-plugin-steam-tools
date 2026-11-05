#!/usr/bin/env bash
# Builds the plugin bundle (unsigned): build/manifest.json and build/plugin.py.
#
# A python-kind plugin's payload is one file, plugin.py (droidtop docs/plugin-api.md
# 1.3, docs/SPEC.md 12a). src/plugin.py imports the gamegrab-sources shared module
# (the `common` submodule, gamegrab-sources/plugin-common-py); common/embed.py writes
# build/plugin.py with that module's text embedded. The manifest gets the payload's
# hash and the published version <declared>-<CI run> (Droidtop/tracker#126).
#
# Never touches a key. sign.sh is the separate signing step.

set -euo pipefail
cd "$(dirname "$0")"

test -f common/gamegrab.py || { echo "common/ is empty: git submodule update --init" >&2; exit 1; }

OUT=build
rm -rf -- "${OUT:?}"
mkdir -p "$OUT"

python3 common/embed.py src/plugin.py common/gamegrab.py "$OUT/plugin.py"

python3 - <<'PY'
import hashlib, json, os, sys

manifest = json.load(open("manifest.template.json"))
build = os.environ.get("DROIDTOP_PLUGIN_BUILD", "").strip()
if build:
    if not build.isdigit():
        sys.exit(f"DROIDTOP_PLUGIN_BUILD must be a CI run number, got {build!r}")
    manifest["version"] = f'{manifest["version"]}-{build}'
sha = hashlib.sha256(open("build/plugin.py", "rb").read()).hexdigest()
manifest["payload"] = [{"path": "plugin.py", "sha256": sha}]
json.dump(manifest, open("build/manifest.json", "w"), indent=2, sort_keys=True)
print(f"id={manifest['id']} version={manifest['version']} plugin.py sha256={sha}")
PY

echo "Built $OUT/manifest.json and $OUT/plugin.py (unsigned)"
