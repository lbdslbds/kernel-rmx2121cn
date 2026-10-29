#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Compile a freestanding AArch64 init and deterministic newc/gzip archive.

This program performs only host-side file operations. It does not create an
Android boot image or operate a connected device.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
import struct
import subprocess
from pathlib import Path
from zlib import crc32

ROOT = Path(__file__).resolve().parent


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def inspect_elf(data: bytes) -> dict:
    if len(data) < 64 or data[:7] != b"\x7fELF\x02\x01\x01":
        raise ValueError("init must be a little-endian ELF64 executable")
    fields = struct.unpack_from("<HHIQQQIHHHHHH", data, 16)
    kind, machine, version, entry, phoff = fields[:5]
    ehsize, phentsize, phnum = fields[7:10]
    if (kind, machine, version, ehsize, phentsize) != (2, 183, 1, 64, 56):
        raise ValueError("init must be a static AArch64 ET_EXEC with standard ELF headers")
    if not 0 < phnum < 128 or phoff + phentsize * phnum > len(data):
        raise ValueError("invalid ELF program header table")
    loads = []
    entry_mapped = False
    for index in range(phnum):
        ptype, flags, offset, vaddr, _, filesz, memsz, alignment = struct.unpack_from(
            "<IIQQQQQQ", data, phoff + index * phentsize
        )
        if ptype in (2, 3):
            raise ValueError("dynamic linking / ELF interpreter is not allowed")
        if ptype != 1:
            continue
        if offset + filesz > len(data) or memsz < filesz:
            raise ValueError("ELF segment exceeds input bounds")
        if flags & 3 == 3:
            raise ValueError("writable executable ELF segment is not allowed")
        if alignment > 4096 or (alignment > 1 and
            (alignment & (alignment - 1) or vaddr % alignment != offset % alignment)):
            raise ValueError("unexpected ELF load alignment")
        if flags & 1 and vaddr <= entry < vaddr + filesz:
            entry_mapped = True
        loads.append({"vaddr": hex(vaddr), "file_size": filesz,
                      "memory_size": memsz, "flags": flags, "alignment": alignment})
    if not loads or not entry_mapped:
        raise ValueError("ELF entry point is not in an executable file-backed load segment")
    return {"machine": "AArch64", "type": "ET_EXEC", "static": True,
            "entry": hex(entry), "load_segments": loads, "sha256": sha(data),
            "bytes": len(data)}


def archive_entries(init: bytes) -> list[tuple]:
    return [
        ("dev", stat.S_IFDIR | 0o755, b"", 0, 0),
        ("proc", stat.S_IFDIR | 0o755, b"", 0, 0),
        ("sys", stat.S_IFDIR | 0o755, b"", 0, 0),
        ("dev/console", stat.S_IFCHR | 0o600, b"", 5, 1),
        ("dev/kmsg", stat.S_IFCHR | 0o600, b"", 1, 11),
        ("dev/null", stat.S_IFCHR | 0o666, b"", 1, 3),
        ("init", stat.S_IFREG | 0o755, init, 0, 0),
    ]


def make_newc(entries: list[tuple]) -> bytes:
    out = bytearray()
    for ino, (name, mode, data, rmajor, rminor) in enumerate(
        entries + [("TRAILER!!!", 0, b"", 0, 0)], 1
    ):
        encoded = name.encode("ascii") + b"\0"
        values = [ino, mode, 0, 0, 2 if stat.S_ISDIR(mode) else 1, 0,
                  len(data), 0, 0, rmajor, rminor, len(encoded), 0]
        out.extend(b"070701" + b"".join(f"{value:08x}".encode("ascii") for value in values))
        out.extend(encoded)
        out.extend(b"\0" * (-len(out) % 4))
        out.extend(data)
        out.extend(b"\0" * (-len(out) % 4))
    out.extend(b"\0" * (-len(out) % 512))
    return bytes(out)


def inspect_newc(data: bytes, expected: list[tuple]) -> list[dict]:
    entries = []
    offset = 0
    while True:
        if data[offset:offset + 6] != b"070701":
            raise ValueError("bad newc header")
        fields = [int(data[offset + 6 + i * 8:offset + 14 + i * 8], 16) for i in range(13)]
        mode, uid, gid, nlink, mtime, size = fields[1:7]
        namesize = fields[11]
        start = offset + 110
        name_raw = data[start:start + namesize]
        if namesize < 1 or not name_raw.endswith(b"\0"):
            raise ValueError("bad newc pathname")
        name = name_raw[:-1].decode("ascii")
        start = (start + namesize + 3) & ~3
        content = data[start:start + size]
        offset = (start + size + 3) & ~3
        if len(content) != size or uid or gid or mtime or fields[12]:
            raise ValueError("bad newc data or nondeterministic metadata")
        if name == "TRAILER!!!":
            if size or any(data[offset:]) or len(data) % 512:
                raise ValueError("bad archive trailer or padding")
            break
        index = len(entries)
        if index >= len(expected) or (name, mode, content, fields[9], fields[10]) != expected[index]:
            raise ValueError("newc entry mismatch")
        if nlink != (2 if stat.S_ISDIR(mode) else 1):
            raise ValueError("bad newc link count")
        entries.append({"name": name, "mode": oct(mode), "bytes": size,
                        "major": fields[9], "minor": fields[10],
                        "sha256": sha(content) if content else None})
    if len(entries) != len(expected):
        raise ValueError("missing archive entries")
    return entries


def deterministic_gzip(data: bytes) -> bytes:
    """RFC 1952 gzip using stored DEFLATE blocks; no zlib-version dependency."""
    result = bytearray(bytes.fromhex("1f8b08000000000000ff"))
    chunks = [data[i:i + 65535] for i in range(0, len(data), 65535)] or [b""]
    for index, chunk in enumerate(chunks):
        result.append(1 if index == len(chunks) - 1 else 0)
        result.extend(struct.pack("<HH", len(chunk), len(chunk) ^ 0xffff))
        result.extend(chunk)
    result.extend(struct.pack("<II", crc32(data) & 0xffffffff, len(data) & 0xffffffff))
    return bytes(result)


def tool_path(value: str) -> Path:
    path = shutil.which(value)
    if path is None:
        raise ValueError(f"required tool not found: {value}")
    # LLD selects its driver by argv[0]; resolving ld.lld -> lld loses that mode.
    return Path(path).absolute()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--clang", default="clang")
    parser.add_argument("--lld", default="ld.lld")
    parser.add_argument("--pack-only", type=Path, help="package an already compiled static AArch64 init")
    args = parser.parse_args()
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = {"schema": 1, "artifact_kind": "minimal-diagnostic-initramfs",
                "flash_ready": False, "device_tested": False, "runtime_tested": False,
                "sources": {name: sha((ROOT / name).read_bytes())
                            for name in ("init.c", "start.S", "build.py")}}
    if args.pack_only:
        init = args.pack_only.read_bytes()
        manifest["build"] = {"mode": "pack-only", "compiler_provenance": "not established here"}
    else:
        clang, lld = tool_path(args.clang), tool_path(args.lld)
        flags = ["--target=aarch64-linux-gnu", "-Os", "-std=c11", "-Wall", "-Wextra", "-Werror",
                 "-ffreestanding", "-fno-builtin", "-fno-stack-protector", "-fno-pie",
                 "-fno-unwind-tables", "-fno-asynchronous-unwind-tables", "-mno-outline-atomics",
                 "-mgeneral-regs-only", "-g0", "-nostdlib", "-static", "-fuse-ld=lld",
                 f"--ld-path={lld}", "-Wl,-e,_start,--build-id=none,--no-undefined",
                 "-Wl,-z,max-page-size=4096,-z,noexecstack", "init.c", "start.S"]
        for filename in ("init", "init.rebuild"):
            subprocess.run([str(clang), *flags, "-o", str(output / filename)], cwd=ROOT, check=True)
        init = (output / "init").read_bytes()
        if init != (output / "init.rebuild").read_bytes():
            raise ValueError("two builds from identical inputs were not byte-identical")
        manifest["build"] = {
            "mode": "compile-twice", "identical_rebuild": True,
            "compiler": {"version": subprocess.check_output([str(clang), "--version"], text=True).splitlines()[0],
                         "sha256": sha(clang.read_bytes())},
            "linker": {"version": subprocess.check_output([str(lld), "--version"], text=True).splitlines()[0],
                       "sha256": sha(lld.read_bytes())},
            "command": ["clang", *["--ld-path=<ld.lld>" if f.startswith("--ld-path=") else f for f in flags],
                        "-o", "<output>/init"],
        }
    manifest["elf"] = inspect_elf(init)
    entries = archive_entries(init)
    archive = make_newc(entries)
    manifest["archive_entries"] = inspect_newc(archive, entries)
    compressed = deterministic_gzip(archive)
    import gzip
    if gzip.decompress(compressed) != archive:
        raise ValueError("gzip round-trip did not match")
    if archive != make_newc(entries) or compressed != deterministic_gzip(archive):
        raise ValueError("archive generation was not deterministic")
    artifacts = {"init": init, "initramfs.cpio": archive, "initramfs.cpio.gz": compressed}
    manifest["artifacts"] = {}
    for name, data in artifacts.items():
        (output / name).write_bytes(data)
        manifest["artifacts"][name] = {"bytes": len(data), "sha256": sha(data)}
    manifest["checks"] = ["static ELF64 AArch64", "entry in executable segment", "no PT_INTERP or PT_DYNAMIC",
                          "no writable executable load segment", "newc entry and metadata round-trip",
                          "gzip round-trip", "deterministic archive bytes"]
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (output / "SHA256SUMS").write_text("".join(f"{sha(data)}  {name}\n" for name, data in artifacts.items()),
                                      encoding="ascii")
    print(json.dumps({"status": "built", "flash_ready": False, "artifacts": manifest["artifacts"]}, indent=2))


if __name__ == "__main__":
    main()
