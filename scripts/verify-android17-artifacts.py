#!/usr/bin/env python3
"""Verify downloaded generic ACK artifacts without executing or flashing them."""

import argparse
import gzip
import hashlib
import json
import re
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def require(condition, message):
    if not condition:
        raise ValueError(message)


def verify(diagnostics, baseline, run_url, expected_target, expected_build_commit):
    require((diagnostics / "exit-code.txt").read_text().strip() == "0", "build did not exit successfully")
    target = json.loads((diagnostics / "target.json").read_text(encoding="utf-8"))
    require(target == json.loads(expected_target.read_text(encoding="utf-8")), "target differs from expected build input")
    require(target["flashable_on_rmx2121"] is False, "unexpected device artifact claim")
    verified = {}
    for line in (baseline / "SHA256SUMS").read_text().splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)", line)
        require(match is not None, "malformed checksum line")
        expected, name = match.groups()
        require(name not in (".", "..", "SHA256SUMS") and name not in verified, "invalid checksum filename")
        path = baseline / name
        require(path.is_file() and not path.is_symlink(), "artifact is not a regular file")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        require(actual == expected, f"checksum mismatch: {name}")
        verified[name] = actual
    require(set(verified) == {p.name for p in baseline.iterdir() if p.is_file() and p.name != "SHA256SUMS"},
            "checksum list does not cover all artifact files")
    require({"Image", "final.config", "DO-NOT-FLASH.txt"} <= set(verified), "required artifacts absent")

    config = (baseline / "final.config").read_bytes()
    require(config == (diagnostics / "final.config").read_bytes(), "configuration copies differ")
    lines = set(config.decode("utf-8").splitlines())
    require("CONFIG_ARM64_4K_PAGES=y" in lines, "expected 4 KiB page configuration")
    require(not {"CONFIG_ARM64_16K_PAGES=y", "CONFIG_ARM64_64K_PAGES=y"} & lines, "conflicting page configuration")
    image = (baseline / "Image").read_bytes()
    require(len(image) >= 64 and image[56:60] == b"ARMd", "missing arm64 Image header magic")
    flags = struct.unpack_from("<Q", image, 24)[0]
    require(((flags >> 1) & 3) == 1, "Image header does not specify 4 KiB pages")
    require((flags & 1) == 0, "expected a little-endian kernel")
    image_size = struct.unpack_from("<Q", image, 16)[0]
    # This is the effective in-memory size, not necessarily the file length.
    # See https://docs.kernel.org/arch/arm64/booting.html.
    require(image_size >= 64, "invalid Image header effective size")
    gzip_matches = None
    if "Image.gz" in verified:
        require(gzip.decompress((baseline / "Image.gz").read_bytes()) == image, "gzip payload differs from Image")
        gzip_matches = True

    makefile = (diagnostics / "kernel-Makefile").read_text()
    version_parts = []
    for field in ("VERSION", "PATCHLEVEL", "SUBLEVEL"):
        match = re.search(rf"^{field}\s*=\s*(\d+)\s*$", makefile, re.M)
        require(match is not None, f"kernel Makefile lacks {field}")
        version_parts.append(match.group(1))
    version = ".".join(version_parts)
    require(version.startswith("6.18."), "unexpected source kernel version")
    banner_match = re.search(rb"Linux version " + re.escape(version.encode()) + rb"(?=[+\-\s])[^\x00\n]+", image)
    require(banner_match is not None, "Image version banner does not match source Makefile")
    banner = banner_match.group().decode("utf-8")

    manifest_data = (diagnostics / "resolved-manifest.xml").read_bytes()
    manifest = ET.fromstring(manifest_data)
    require(manifest.tag == "manifest", "unexpected manifest root")
    for tag in ("include", "submanifest", "extend-project", "remove-project"):
        require(next(manifest.iter(tag), None) is None, "manifest still contains unresolved directives")
    projects = list(manifest.iter("project"))
    require(projects and all(re.fullmatch(r"[0-9a-f]{40}", p.get("revision", "")) for p in projects),
            "manifest contains an unpinned project")

    def project_paths(element, parent_path=""):
        for child in element:
            if child.tag == "project":
                relative = child.get("path", child.get("name", ""))
                require(relative and all(part not in ("", ".", "..") for part in relative.split("/")),
                        "invalid manifest project path")
                path = parent_path + "/" + relative if parent_path else relative
                yield path, child
                yield from project_paths(child, path)
            else:
                yield from project_paths(child, parent_path)

    paths = list(project_paths(manifest))
    require(len({path for path, _ in paths}) == len(paths), "duplicate manifest project path")
    by_path = dict(paths)
    for path, name, key in (("common", "kernel/common", "kernel"), ("build/kernel", "kernel/build", "build")):
        require(path in by_path and by_path[path].get("name") == name, f"missing expected project: {path}")
        require(by_path[path].get("revision") == target[key]["revision"], f"commit mismatch: {path}")
    build_commit = (diagnostics / "build-definitions-commit.txt").read_text().strip()
    require(re.fullmatch(r"[0-9a-f]{40}", build_commit) is not None, "invalid build definitions commit")
    require(build_commit == expected_build_commit, "build definitions commit differs from expected Actions head")
    return {
        "run_url": run_url,
        "build_commit": build_commit,
        "kernel_version": version,
        "banner": banner,
        "kernel_commit": target["kernel"]["revision"],
        "kleaf_commit": target["build"]["revision"],
        "sha256_verified": verified,
        "image_bytes": len(image),
        "image_header_size": image_size,
        "image_header_page_size_bytes": 4096,
        "final_config_page_size_bytes": 4096,
        "gzip_payload_matches_Image": gzip_matches,
        "module_symvers_exported": "Module.symvers" in verified,
        "manifest_project_count": len(projects),
        "resolved_manifest_sha256": hashlib.sha256(manifest_data).hexdigest(),
        "all_manifest_project_revisions_pinned": True,
        "artifact_kind": "generic_ACK_baseline",
        "rmx2121_board_support_added": False,
        "device_boot_tested": False,
        "flashable_on_rmx2121": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("diagnostics", type=Path)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--expected-build-commit", required=True, help="headSha from the specified Actions run")
    parser.add_argument("--expected-target", type=Path,
                        default=Path(__file__).resolve().parent.parent / "targets/android17.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = verify(args.diagnostics, args.baseline, args.run_url, args.expected_target, args.expected_build_commit)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Verified generic ACK {report['kernel_version']}, {report['image_bytes']} bytes, 4 KiB pages")
    except (OSError, ValueError, KeyError, UnicodeError, struct.error, ET.ParseError) as exc:
        print(f"Verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
