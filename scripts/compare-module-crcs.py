#!/usr/bin/env python3
"""Compare Linux 4.14 AArch64 module __versions with a build's Module.symvers.

Only reads ELF data; it never loads or executes modules. Linux v4.14 defines
modversion_info as unsigned long crc + char name[64 - sizeof(unsigned long)].
On ELF64 this is an 8-byte CRC field and 56-byte name, totalling 64 bytes.
Sources: include/linux/module.h and include/linux/moduleparam.h in Linux v4.14.
"""

import argparse
import hashlib
import json
import re
import struct
import sys
from collections import defaultdict
from pathlib import Path


class FormatError(ValueError):
    """Reject unsupported or malformed input rather than guessing its layout."""


def bounded(data, offset, size, label):
    if offset < 0 or size < 0 or offset > len(data) or size > len(data) - offset:
        raise FormatError(f"{label}: range outside file")
    return data[offset:offset + size]


def cstring(data, offset, label):
    if not 0 <= offset < len(data):
        raise FormatError(f"{label}: invalid string offset")
    end = data.find(b"\0", offset)
    if end < 0:
        raise FormatError(f"{label}: unterminated string")
    try:
        return data[offset:end].decode("ascii")
    except UnicodeDecodeError as exc:
        raise FormatError(f"{label}: non-ASCII name") from exc


def parse_versions(raw):
    if not raw or len(raw) % 64:
        raise FormatError("__versions: expected nonempty 64-byte entries")
    result = {}
    for offset in range(0, len(raw), 64):
        crc = struct.unpack_from("<Q", raw, offset)[0]
        name_bytes = raw[offset + 8:offset + 64]
        name = cstring(name_bytes, 0, "__versions symbol")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.$]*", name):
            raise FormatError("__versions: invalid symbol name")
        if crc > 0xffffffff:
            raise FormatError("__versions: CRC exceeds 32 bits")
        if any(name_bytes[len(name) + 1:]):
            raise FormatError("__versions: nonzero bytes after symbol name")
        if name in result:
            raise FormatError("__versions: duplicate symbol")
        result[name] = crc
    return result


def parse_module(data):
    header = struct.unpack("<16sHHIQQQIHHHHHH", bounded(data, 0, 64, "ELF header"))
    ident, elf_type, machine, version = header[:4]
    if ident[:4] != b"\x7fELF" or ident[4:7] != b"\x02\x01\x01":
        raise FormatError("expected ELF64 little-endian version 1")
    if elf_type != 1 or machine != 183 or version != 1 or header[8] != 64:
        raise FormatError("expected AArch64 ET_REL with a 64-byte ELF header")
    section_offset, section_entry_size = header[6], header[11]
    section_count, names_index = header[12], header[13]
    if section_entry_size != 64 or section_offset == 0:
        raise FormatError("unsupported or absent ELF section table")
    first = struct.unpack("<IIQQQQIIQQ", bounded(data, section_offset, 64, "section zero"))
    if section_count == 0:
        section_count = first[5]
    if names_index == 0xffff:
        names_index = first[6]
    if not 0 < section_count <= 65536 or not 0 < names_index < section_count:
        raise FormatError("invalid section count or string-table index")
    table = bounded(data, section_offset, section_count * 64, "section table")
    sections = [struct.unpack_from("<IIQQQQIIQQ", table, i * 64)
                for i in range(section_count)]
    for section in sections[1:]:
        if section[1] != 8:  # SHT_NOBITS has no file-backed payload.
            bounded(data, section[4], section[5], "section payload")
    names_section = sections[names_index]
    if names_section[1] != 3:
        raise FormatError("section names must reference SHT_STRTAB")
    names = bounded(data, names_section[4], names_section[5], "section names")
    named = defaultdict(list)
    for index, section in enumerate(sections):
        named[cstring(names, section[0], "section name")].append(index)

    def payload(section_name, required=True):
        indices = named.get(section_name, [])
        if not indices and not required:
            return b""
        if len(indices) != 1:
            raise FormatError(f"{section_name}: expected exactly one section")
        section = sections[indices[0]]
        if section[1] != 1 or section[2] & 0x800:
            raise FormatError(f"{section_name}: expected uncompressed SHT_PROGBITS")
        return bounded(data, section[4], section[5], section_name)

    versions = parse_versions(payload("__versions"))
    info = {}
    for item in payload(".modinfo", required=False).split(b"\0"):
        key, separator, value = item.partition(b"=")
        if separator and key in (b"name", b"vermagic", b"depends"):
            try:
                info[key.decode("ascii")] = value.decode("ascii")
            except UnicodeDecodeError as exc:
                raise FormatError("non-ASCII selected module metadata") from exc

    # Export declarations identify possible providers for imports absent from
    # the target symvers. Their existence does not prove provider CRC/runtime fit.
    exports = set()
    symbol_table_seen = False
    for section in sections:
        if section[1] != 2:  # SHT_SYMTAB
            continue
        symbol_table_seen = True
        if section[9] != 24 or section[5] % 24 or section[6] >= section_count:
            raise FormatError("invalid ELF64 symbol table")
        strings_section = sections[section[6]]
        if strings_section[1] != 3:
            raise FormatError("symbol table link does not reference strings")
        strings = bounded(data, strings_section[4], strings_section[5], "symbol strings")
        symbols = bounded(data, section[4], section[5], "symbol table")
        for offset in range(0, len(symbols), 24):
            symbol = struct.unpack_from("<IBBHQQ", symbols, offset)
            name = cstring(strings, symbol[0], "ELF symbol")
            if symbol[3] != 0 and name.startswith("__ksymtab_"):
                exports.add(name[len("__ksymtab_"):])
    return {
        "versions": versions,
        "vermagic": info.get("vermagic"),
        "module_name": info.get("name"),
        "declared_dependencies": [name for name in info.get("depends", "").split(",") if name],
        "exports": exports,
        "export_scan_has_symbol_table": symbol_table_seen,
    }


def parse_symvers(text):
    symbols = {}
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        fields = line.split()
        if len(fields) < 3:
            raise FormatError(f"Module.symvers line {line_number}: too few columns")
        try:
            crc = int(fields[0], 16)
        except ValueError as exc:
            raise FormatError(f"Module.symvers line {line_number}: invalid CRC") from exc
        if not 0 <= crc <= 0xffffffff:
            raise FormatError(f"Module.symvers line {line_number}: CRC exceeds 32 bits")
        name = fields[1]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.$]*", name):
            raise FormatError(f"Module.symvers line {line_number}: invalid symbol")
        if name in symbols:
            raise FormatError(f"Module.symvers line {line_number}: duplicate symbol")
        # Only preserve the technical module basename, never a host path.
        symbols[name] = {"crc": crc, "provider": fields[2].replace("\\", "/").rsplit("/", 1)[-1]}
    if not symbols:
        raise FormatError("empty Module.symvers")
    return symbols


def compare_modules(module_directory, symvers_file):
    symvers_data = symvers_file.read_bytes()
    target_symbols = parse_symvers(symvers_data.decode("utf-8"))
    modules = []
    providers = defaultdict(list)
    files = sorted(module_directory.rglob("*.ko"))
    if not files:
        raise FormatError("no .ko files in module directory")
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise FormatError("module entries must be regular files, not symlinks")
        data = path.read_bytes()
        module = parse_module(data)
        module["file"] = path.relative_to(module_directory).as_posix()
        module["sha256"] = hashlib.sha256(data).hexdigest()
        module["module_name"] = module["module_name"] or path.stem
        for symbol in module["exports"]:
            providers[symbol].append(module["file"])
        modules.append(module)

    reports = []
    totals = {"versioned_imports": 0, "matched": 0, "mismatched": 0, "absent": 0,
              "absent_with_possible_vendor_provider": 0}
    for module in modules:
        result = {key: module[key] for key in (
            "file", "sha256", "module_name", "vermagic", "declared_dependencies",
            "export_scan_has_symbol_table")}
        result.update({"matched": [], "mismatched": [], "absent": []})
        for symbol, crc in sorted(module["versions"].items()):
            entry = {"symbol": symbol, "module_crc": f"0x{crc:08x}"}
            target = target_symbols.get(symbol)
            if target is None:
                candidates = [item for item in providers.get(symbol, []) if item != module["file"]]
                entry["possible_vendor_providers"] = sorted(candidates)
                result["absent"].append(entry)
                if candidates:
                    totals["absent_with_possible_vendor_provider"] += 1
            else:
                entry.update({"target_crc": f"0x{target['crc']:08x}",
                              "target_provider": target["provider"]})
                result["matched" if crc == target["crc"] else "mismatched"].append(entry)
        result["counts"] = {key: len(result[key]) for key in ("matched", "mismatched", "absent")}
        result["counts"]["versioned_imports"] = len(module["versions"])
        for key in ("versioned_imports", "matched", "mismatched", "absent"):
            totals[key] += result["counts"][key]
        result["static_status"] = ("crc_mismatch_present" if result["mismatched"] else
                                   "target_exports_absent" if result["absent"] else
                                   "all_versioned_imports_match_target")
        result["runtime_load_status"] = "not_observed"
        reports.append(result)
    totals["modules_in_directory"] = len(reports)
    totals["modules_with_crc_mismatch"] = sum(bool(item["mismatched"]) for item in reports)
    totals["modules_with_absent_target_exports"] = sum(bool(item["absent"]) for item in reports)
    return {
        "schema_version": 1,
        "audit_type": "offline_vendor_directory_module_crc_comparison",
        "target_symvers_sha256": hashlib.sha256(symvers_data).hexdigest(),
        "target_export_count": len(target_symbols),
        "layout": {"elf_class": 64, "endianness": "little", "machine": "AArch64",
                   "elf_type": "ET_REL", "versions_entry_bytes": 64,
                   "crc_field_bytes": 8, "symbol_field_bytes": 56},
        "layout_references": [
            "https://github.com/torvalds/linux/blob/v4.14/include/linux/module.h",
            "https://github.com/torvalds/linux/blob/v4.14/include/linux/moduleparam.h"],
        "limitations": [
            "Module presence and static loading configuration do not establish actual loading or necessity.",
            "Counts aggregate per-module versioned imports; a symbol used by two modules is counted twice.",
            "Absent means missing from this build's Module.symvers, not necessarily unavailable at runtime.",
            "Possible vendor providers are inferred from defined __ksymtab_ symbols; their CRCs are not validated here.",
            "Matching CRCs are not a boot, load, signature, dependency, firmware, or runtime compatibility test.",
            "No module was loaded or executed and no device was accessed."],
        "summary": totals,
        "modules": reports,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("module_directory", type=Path)
    parser.add_argument("symvers_file", type=Path)
    parser.add_argument("--output", type=Path, help="Write JSON here; otherwise write to stdout")
    args = parser.parse_args()
    try:
        report = compare_modules(args.module_directory, args.symvers_file)
        output = json.dumps(report, ensure_ascii=True, indent=2) + "\n"
        if args.output:
            args.output.write_text(output, encoding="utf-8")
            print(json.dumps(report["summary"], sort_keys=True))
        else:
            sys.stdout.write(output)
    except (OSError, UnicodeError, FormatError, struct.error) as exc:
        print(f"Audit failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
