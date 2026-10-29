#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Host-only archive format checks, using non-executable test data.

These checks do not build or execute an ARM64 program and do not establish boot
compatibility. The test archive stays under the selected validation directory.
"""
import argparse
import gzip
import json
import subprocess
from pathlib import Path

import build


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--tar", default="tar")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    payload = b"FORMAT TEST ONLY - NOT AN EXECUTABLE\n"
    entries = build.archive_entries(payload)
    archive = build.make_newc(entries)
    parsed = build.inspect_newc(archive, entries)
    packed = build.deterministic_gzip(archive)
    assert gzip.decompress(packed) == archive
    assert archive == build.make_newc(entries)
    assert packed == build.deterministic_gzip(archive)
    fixture = args.out / "format-test-only.cpio.gz"
    fixture.write_bytes(packed)
    listing = subprocess.check_output([args.tar, "-tf", str(fixture)], text=True).splitlines()
    assert listing == [entry[0] for entry in entries], listing
    content = subprocess.check_output([args.tar, "-xOf", str(fixture), "init"])
    assert content == payload
    negatives = []
    for name, data in [("empty", b""), ("text", payload), ("PE", b"MZ" + b"\0" * 128)]:
        try:
            build.inspect_elf(data)
        except ValueError:
            negatives.append(name)
        else:
            raise AssertionError("non-ELF accepted: " + name)
    report = {
        "schema": 1,
        "scope": "host-only format validation with deliberately non-executable fixture",
        "arm64_compile": False,
        "runtime_test": False,
        "device_test": False,
        "checks": ["newc metadata and payload round-trip", "gzip standard-library decompression",
                   "deterministic repeated generation", "independent tar archive listing",
                   "independent tar init payload extraction to stdout", "reject non-ELF inputs"],
        "tar_version": subprocess.check_output([args.tar, "--version"], text=True).splitlines()[0],
        "archive_sha256": build.sha(archive),
        "gzip_sha256": build.sha(packed),
        "entries": parsed,
        "rejected_inputs": negatives,
        "limitations": ["Fixture init is text, not an ARM64 executable.",
                        "No ARM64 code generation, linking, emulation or phone boot was performed."]
    }
    (args.out / "format-validation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
