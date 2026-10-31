#!/usr/bin/env python3
"""Apply only the declared fixed-source build fixes, or verify them after Kbuild."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

SOURCE = "846dd6f9b6c61b6aa95e141455ce8f0ea44b29ef"
ALLOWED = {"drivers/irqchip/irq-gic-v3.c", "fs/binfmt_elf.c"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--patches", type=Path, required=True)
    parser.add_argument("--diagnostics", type=Path, required=True)
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    args.diagnostics.mkdir(parents=True, exist_ok=True)

    def git(*options):
        return subprocess.check_output(["git", "-C", str(args.source), *options])

    if git("rev-parse", "HEAD").decode().strip() != SOURCE:
        raise ValueError("Unexpected source revision")
    manifest_bytes = (args.patches / "series.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest["schema_version"] != 1 or manifest["source_commit"] != SOURCE:
        raise ValueError("Unexpected patch manifest")
    declared = manifest["files"]
    if len(declared) != len(ALLOWED) or {item["path"] for item in declared} != ALLOWED:
        raise ValueError("Unexpected source patch scope")
    patch_paths = []
    for item in manifest["patches"]:
        name = item["file"]
        if not name.endswith(".patch") or Path(name).name != name:
            raise ValueError("Invalid patch filename")
        path = args.patches / name
        if sha(path.read_bytes()) != item["sha256"]:
            raise ValueError("Patch digest differs: " + name)
        patch_paths.append(path.resolve())
    if len(patch_paths) != 2 or len(set(patch_paths)) != 2:
        raise ValueError("Expected exactly two separate build fixes")
    if not args.verify_only:
        if git("status", "--porcelain"):
            raise ValueError("Source is not pristine before patches")
        for item in declared:
            if sha((args.source / item["path"]).read_bytes()) != item["original_sha256"]:
                raise ValueError("Original source digest differs: " + item["path"])
        git("apply", "--check", "--", *map(str, patch_paths))
        git("apply", "--", *map(str, patch_paths))

    changed = set(git("diff", "--name-only", "--no-renames").decode().splitlines())
    if changed != ALLOWED or git("ls-files", "--others", "--exclude-standard"):
        raise ValueError("Source contains unexpected changes or untracked files")
    for item in declared:
        if sha((args.source / item["path"]).read_bytes()) != item["patched_sha256"]:
            raise ValueError("Patched source digest differs: " + item["path"])
    diff = git("diff", "--no-ext-diff", "--no-textconv", "--full-index", "--binary",
               "--src-prefix=a/", "--dst-prefix=b/")
    status = git("status", "--porcelain")
    report = {"source_commit": SOURCE, "patch_manifest_sha256": sha(manifest_bytes),
              "diff_sha256": sha(diff), "modified_source_files": sorted(ALLOWED),
              "files_match_declared_hashes": True, "unexpected_source_changes": False}
    if args.verify_only:
        before = json.loads((args.diagnostics / "source-patches-before-build.json").read_text())
        if report != before or diff != (args.diagnostics / "source-diff.patch").read_bytes():
            raise ValueError("Source differs from reviewed pre-build patch state")
        if status != (args.diagnostics / "source-status-patched.txt").read_bytes():
            raise ValueError("Source status changed during build")
        if manifest_bytes != (args.diagnostics / "source-patch-manifest.json").read_bytes():
            raise ValueError("Patch manifest changed during build")
        (args.diagnostics / "source-patches-after-build.json").write_text(json.dumps(report, indent=2) + "\n")
    else:
        (args.diagnostics / "source-diff.patch").write_bytes(diff)
        (args.diagnostics / "source-status-patched.txt").write_bytes(status)
        (args.diagnostics / "source-patch-manifest.json").write_bytes(manifest_bytes)
        (args.diagnostics / "patches").mkdir(exist_ok=True)
        for path in patch_paths:
            (args.diagnostics / "patches" / path.name).write_bytes(path.read_bytes())
        (args.diagnostics / "source-patches-before-build.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"source_patch_state_verified": True, "after_build": args.verify_only, **report}))


if __name__ == "__main__":
    main()
