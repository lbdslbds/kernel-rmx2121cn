#!/usr/bin/env python3
"""Verify downloaded RMX2121CN 4.14 kernel/external-module artifacts offline.

Requires Python 3.9+ and OpenSSL on PATH. Only the requested JSON report and
temporary public-certificate/CMS/payload files are written. No module is loaded,
no device is accessed, and no signing private key is opened or used.

Example:
  python scripts/verify-external-modules.py --artifact-dir RUN/ARTIFACT \
      --output RUN/verification.json --allow-partial

Exit codes: 0 verified (possibly partial when explicitly allowed), 1 verification
findings, 2 malformed/unavailable inputs or a verifier dependency failure.
"""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import struct
import subprocess
import sys
import tempfile


sys.dont_write_bytecode = True

MODULE_DEPENDENCIES = {
    "conninfra": (),
    "connfem": (),
    "bt_drv_6885": ("conninfra", "connfem"),
    "fmradio_drv_connac2x": ("conninfra",),
    "gps_drv": ("conninfra",),
    "wmt_chrdev_wifi": ("conninfra",),
    "wlan_drv_gen4m": ("conninfra", "connfem", "wmt_chrdev_wifi"),
    "met": (),
}
ALARM_SYMBOLS = ("alarm_cancel", "alarm_init", "alarm_start_relative")
SIGNATURE_MARKER = b"~Module signature appended~\n"
SHA512_OID = "2.16.840.1.101.3.4.2.3"
PRIVATE_KEY_HEADER = re.compile(rb"-----BEGIN (?:[A-Z0-9]+ ){0,4}PRIVATE KEY-----")


class VerificationError(ValueError):
    """An input cannot safely or unambiguously be verified."""


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def is_link(path):
    # Reject Windows junctions as well as ordinary symbolic links.
    return path.is_symlink() or getattr(path, "is_junction", lambda: False)()


def artifact_files(root):
    """Inventory regular files without following links, including directory links."""
    def walk_error(error):
        raise error

    result = []
    for current, directories, files in os.walk(root, followlinks=False, onerror=walk_error):
        for name in directories + files:
            path = Path(current) / name
            if is_link(path) or not path.resolve(strict=True).is_relative_to(root):
                raise VerificationError(f"Artifact link/path escapes or aliases its root: {path.relative_to(root)}")
        for name in files:
            path = Path(current) / name
            if not path.is_file():
                raise VerificationError(f"Not a regular artifact file: {path.relative_to(root)}")
            result.append(path)
    return sorted(result)


def safe_relative_file(root, name):
    """Accept only relative POSIX names; reject traversal and Windows ADS/drives."""
    if (not name or "\\" in name or ":" in name or "\x00" in name or
            any(ord(character) < 32 for character in name)):
        raise VerificationError("Unsafe filename in artifact manifest/list")
    relative = PurePosixPath(name)
    if relative.is_absolute() or PureWindowsPath(name).drive or ".." in relative.parts:
        raise VerificationError(f"Non-relative/traversing artifact filename: {name}")
    if not relative.parts:
        raise VerificationError("Empty artifact filename")
    current = root
    for part in relative.parts:
        current /= part
        if is_link(current):
            raise VerificationError(f"Artifact filename traverses a link: {name}")
    resolved = current.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise VerificationError(f"Artifact filename is outside root or not a file: {name}")
    return resolved


def verify_manifest(root, complete_coverage=False):
    manifest = safe_relative_file(root, "SHA256SUMS")
    entries, mismatches = {}, []
    for number, line in enumerate(manifest.read_text(encoding="utf-8").splitlines(), 1):
        match = re.fullmatch(r"([0-9a-fA-F]{64}) ([ *])(.+)", line)
        if not match:
            raise VerificationError(f"Invalid SHA256SUMS line {number}")
        expected, _, name = match.groups()
        path = safe_relative_file(root, name)
        if path == manifest or path in entries:
            raise VerificationError(f"Self-reference or duplicate SHA256SUMS entry: {name}")
        actual = sha256_file(path)
        entries[path] = actual
        if actual != expected.lower():
            mismatches.append({"file": path.relative_to(root).as_posix(),
                               "expected": expected.lower(), "actual": actual})
    if not entries:
        raise VerificationError("Empty SHA256SUMS manifest")
    uncovered = []
    if complete_coverage:
        uncovered = sorted(path.relative_to(root).as_posix()
                           for path in artifact_files(root)
                           if path != manifest and path not in entries)
    return {"verified_files": len(entries) - len(mismatches), "listed_files": len(entries),
            "manifest_sha256": sha256_file(manifest), "mismatches": mismatches,
            "uncovered_files": uncovered, "complete_coverage_required": complete_coverage,
            "valid": not mismatches and not uncovered}, entries


def scan_private_key_headers(files, root):
    """Detect PEM headers only; never parse, log, archive, or use key material."""
    found = []
    for path in files:
        carry = b""
        with path.open("rb") as stream:
            while True:
                block = stream.read(1024 * 1024)
                if not block:
                    break
                data = carry + block
                if PRIVATE_KEY_HEADER.search(data):
                    found.append(path.relative_to(root).as_posix())
                    break
                carry = data[-256:]
    return found


def load_parser():
    parser_path = Path(__file__).resolve().with_name("compare-module-crcs.py")
    if not parser_path.is_file():
        raise VerificationError("The existing scripts/compare-module-crcs.py parser is unavailable")
    spec = importlib.util.spec_from_file_location("kernel_module_crc_parser", parser_path)
    if spec is None or spec.loader is None:
        raise VerificationError("Cannot import the module CRC parser")
    parser = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(parser)
    return parser, parser_path


def read_symvers(parser, path, allow_empty=False):
    text = path.read_text(encoding="utf-8")
    return {} if allow_empty and not text.strip() else parser.parse_symvers(text)


def merged_symbols(*tables):
    result = {}
    for table in tables:
        for name, entry in table.items():
            if name in result and result[name] != entry:
                raise VerificationError(f"Conflicting Module.symvers providers/CRCs for {name}")
            result[name] = entry
    return result


def compare_imports(module, symbols):
    missing, mismatches = [], []
    for name, crc in sorted(module["versions"].items()):
        target = symbols.get(name)
        if target is None:
            missing.append(name)
        elif target["crc"] != crc:
            mismatches.append({"symbol": name, "module_crc": f"0x{crc:08x}",
                               "candidate_crc": f"0x{target['crc']:08x}",
                               "candidate_provider": target["provider"]})
    return missing, mismatches


def split_module_signature(data):
    if not data.endswith(SIGNATURE_MARKER) or len(data) <= len(SIGNATURE_MARKER) + 12:
        raise VerificationError("Missing or truncated module signature trailer")
    header_end = len(data) - len(SIGNATURE_MARKER)
    header_start = header_end - 12
    header = data[header_start:header_end]
    # algo/hash/name/key-id/padding are zero for the Linux PKCS#7 format.
    if header[:8] != b"\x00\x00\x02\x00\x00\x00\x00\x00":
        raise VerificationError("Unsupported/invalid Linux PKCS#7 signature header")
    signature_size = struct.unpack(">I", header[8:])[0]
    if not 0 < signature_size < header_start:
        raise VerificationError("Invalid module signature length")
    signature_start = header_start - signature_size
    payload = data[:signature_start]
    if not payload.startswith(b"\x7fELF") or payload.endswith(SIGNATURE_MARKER):
        raise VerificationError("Signature payload is not a single unsigned ELF module")
    return payload, data[signature_start:header_start]


def openssl_run(executable, arguments):
    process = subprocess.run([executable, *map(str, arguments)], capture_output=True,
                             text=True, encoding="utf-8", errors="replace", timeout=60)
    if process.returncode:
        # Do not echo parsed certificate/CMS data or file content on failure.
        raise VerificationError(f"OpenSSL {arguments[0]} failed with exit code {process.returncode}")
    return process.stdout


def cms_digests(executable, signature_file):
    printed = openssl_run(executable, ["cms", "-cmsout", "-print", "-inform", "DER", "-in", signature_file])
    block = re.search(r"(?ms)^\s*digestAlgorithms:\s*\n(.*?)^\s*encapContentInfo:", printed)
    if block is None:
        raise VerificationError("Cannot inspect CMS digestAlgorithms")
    pattern = r"(?m)^\s*algorithm:\s*(\S+)\s+\(([0-9.]+)\)\s*$"
    algorithms = re.findall(pattern, block.group(1))
    signer_algorithms = re.findall(
        r"(?m)^\s*digestAlgorithm:\s*\n\s*algorithm:\s*(\S+)\s+\(([0-9.]+)\)\s*$", printed)
    if not algorithms or not signer_algorithms:
        raise VerificationError("CMS has no inspectable digest/signer digest algorithms")
    if any(item != ("sha512", SHA512_OID) for item in algorithms + signer_algorithms):
        raise VerificationError("CMS uses a digest other than SHA-512")
    return {"digest_algorithms": [name for name, _ in algorithms],
            "signer_digest_algorithms": [name for name, _ in signer_algorithms]}


def verify_signature(executable, data, certificate_pem, temporary):
    payload, signature = split_module_signature(data)
    payload_file = temporary / "module-payload.elf"
    signature_file = temporary / "module-signature.der"
    payload_file.write_bytes(payload)
    signature_file.write_bytes(signature)
    digest_info = cms_digests(executable, signature_file)
    openssl_run(executable, ["cms", "-verify", "-binary", "-inform", "DER", "-in", signature_file,
                            "-content", payload_file, "-certfile", certificate_pem,
                            "-nointern", "-noverify", "-out", os.devnull])
    return {"valid_with_run_certificate_only": True, "digest": "sha512",
            "signature_bytes": len(signature), "payload_bytes": len(payload),
            "payload_sha256": hashlib.sha256(payload).hexdigest(),
            "embedded_cms_certificates_ignored": True,
            "system_ca_chain_verification": False, **digest_info}


def read_run_result(root, external):
    result = {}
    for line in safe_relative_file(external, "result.txt").read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if not separator or key in result:
            raise VerificationError("Malformed/duplicate field in external result.txt")
        result[key] = value
    if set(result) != {"exit_code", "stage", "module", "completed"}:
        raise VerificationError("Unexpected fields in external result.txt")
    try:
        external_exit = int(result["exit_code"])
        run_exit = int(safe_relative_file(root, "exit-code.txt").read_text().strip())
    except ValueError as error:
        raise VerificationError("Invalid build exit code") from error
    count = re.fullmatch(r"([0-8])/8", result["completed"])
    if count is None or not 0 <= external_exit <= 255 or not 0 <= run_exit <= 255:
        raise VerificationError("Invalid external completion count or exit code")
    return {"run_exit_code": run_exit, "external_exit_code": external_exit,
            "stage": result["stage"], "module": result["module"],
            "completed_count": int(count.group(1)), "expected_count": 8}


def verify_artifact(root, allow_partial, stock_root, report):
    files = artifact_files(root)
    private_headers = scan_private_key_headers(files, root)
    report["private_key_scan"] = {"scanned_files": len(files), "private_key_header_files": private_headers,
                                   "scope": "raw regular artifact files; compressed archives are not expanded",
                                   "key_material_parsed_or_exported": False}
    if private_headers:
        raise VerificationError("Artifact contains a private-key PEM header; stopped without parsing key material")
    external = root / "external-modules"
    if not external.is_dir() or is_link(external):
        raise VerificationError("Missing regular external-modules directory")
    external = external.resolve(strict=True)
    top_hashes, top_entries = verify_manifest(root)
    ext_hashes, ext_entries = verify_manifest(external, complete_coverage=True)
    report["artifact_hashes"] = {"kernel": top_hashes, "external_modules": ext_hashes}
    if not top_hashes["valid"] or not ext_hashes["valid"]:
        raise VerificationError("Artifact SHA256SUMS verification failed")
    required = {safe_relative_file(root, name) for name in
                ("Image", "Image.gz", "Image.gz-dtb", "Module.symvers", "System.map", "vmlinux.xz")}
    if not required.issubset(top_entries):
        raise VerificationError("Kernel manifest omits a required kernel binary/symbol file")
    if any((external / name).exists() for name in ("INTEGRITY-ERROR.txt", "EVIDENCE-ERROR.txt")):
        raise VerificationError("Artifact explicitly reports an integrity/evidence error")

    completed_lines = safe_relative_file(external, "completed.list").read_text(encoding="utf-8").splitlines()
    expected = [f"{name}/{name}.ko" for name in MODULE_DEPENDENCIES]
    if not completed_lines or completed_lines != expected[:len(completed_lines)]:
        raise VerificationError("completed.list is empty or is not the expected unique ordered module prefix")
    completed_names = [PurePosixPath(line).parent.name for line in completed_lines]
    listed_modules = {safe_relative_file(external, line) for line in completed_lines}
    actual_modules = {path for path in ext_entries if path.suffix == ".ko"}
    if listed_modules != actual_modules:
        raise VerificationError("Archived .ko files and completed.list disagree")
    result = read_run_result(root, external)
    report["run_result"] = result
    report["completed_modules"] = completed_names
    report["not_completed_modules"] = list(MODULE_DEPENDENCIES)[len(completed_names):]
    if result["completed_count"] != len(completed_names):
        raise VerificationError("result.txt count disagrees with completed.list")
    if result["external_exit_code"] == 0 and not (
            len(completed_names) == 8 and result["stage"] == "complete" and result["module"] == "none"):
        raise VerificationError("Successful external result is inconsistent with its completion list/stage")
    full_build = (len(completed_names) == 8 and result["run_exit_code"] == 0 and
                  result["external_exit_code"] == 0 and result["stage"] == "complete" and
                  result["module"] == "none")
    report["full_build_success"] = full_build
    if not allow_partial and not full_build:
        report["errors"].append("A successful run with all eight completed modules is required; use --allow-partial to audit partial evidence")

    parser, parser_path = load_parser()
    report["crc_parser_sha256"] = sha256_file(parser_path)
    kernel_table = safe_relative_file(root, "Module.symvers")
    kernel_copy = safe_relative_file(external, "kernel.Module.symvers")
    report["kernel_symvers_sha256"] = sha256_file(kernel_table)
    report["kernel_symvers_copy_matches"] = kernel_table.read_bytes() == kernel_copy.read_bytes()
    if not report["kernel_symvers_copy_matches"]:
        raise VerificationError("External kernel.Module.symvers differs from the kernel table")
    kernel_symbols = read_symvers(parser, kernel_table)
    release = safe_relative_file(root, "kernelrelease.txt").read_text(encoding="utf-8").strip()
    if not release or release != safe_relative_file(external, "kernelrelease.txt").read_text(encoding="utf-8").strip():
        raise VerificationError("Kernel release copies are empty or different")
    report["kernelrelease"] = release

    certificate = safe_relative_file(external, "signing-certificate.der")
    certificate_data = certificate.read_bytes()
    if not certificate_data:
        raise VerificationError("Empty run certificate")
    certificate_occurrences = safe_relative_file(root, "Image").read_bytes().count(certificate_data)
    report["certificate"] = {"sha256": hashlib.sha256(certificate_data).hexdigest(),
                              "occurrences_in_Image": certificate_occurrences}
    if certificate_occurrences < 1:
        raise VerificationError("Run public certificate is not embedded in Image")
    executable = shutil.which("openssl")
    if executable is None:
        raise VerificationError("OpenSSL executable is required on PATH")
    report["openssl_version"] = openssl_run(executable, ["version"]).strip()
    tables = {name: read_symvers(parser, safe_relative_file(external, f"{name}/Module.symvers"), allow_empty=True)
              for name in completed_names}
    combined = merged_symbols(kernel_symbols, *tables.values())
    report["modules"] = []
    with tempfile.TemporaryDirectory(prefix="rmx2121cn-module-verify-") as directory:
        temporary = Path(directory)
        certificate_pem = temporary / "run-public-certificate.pem"
        openssl_run(executable, ["x509", "-inform", "DER", "-in", certificate,
                                "-outform", "PEM", "-out", certificate_pem])
        for name in completed_names:
            path = safe_relative_file(external, f"{name}/{name}.ko")
            data = path.read_bytes()
            module = parser.parse_module(data)
            dependencies = MODULE_DEPENDENCIES[name]
            if any(dependency not in tables for dependency in dependencies):
                raise VerificationError(f"Completed {name} lacks a completed dependency")
            symbols = merged_symbols(kernel_symbols, *(tables[dependency] for dependency in dependencies))
            missing, mismatches = compare_imports(module, symbols)
            metadata_matches = (module["module_name"] == name and
                                (module["vermagic"] or "").split(" ")[0] == release and
                                set(module["declared_dependencies"]) == set(dependencies))
            entry = {"module": path.name, "sha256": hashlib.sha256(data).hexdigest(),
                     "module_name": module["module_name"], "vermagic": module["vermagic"],
                     "depends": module["declared_dependencies"], "expected_dependencies": list(dependencies),
                     "metadata_matches": metadata_matches, "versioned_imports": len(module["versions"]),
                     "missing_imports": missing, "crc_mismatches": mismatches,
                     "dependency_tables": [f"{dependency}/Module.symvers" for dependency in dependencies],
                     "module_symvers_sha256": sha256_file(safe_relative_file(external, f"{name}/Module.symvers")),
                     "signature": {"valid_with_run_certificate_only": False}}
            try:
                entry["signature"] = verify_signature(executable, data, certificate_pem, temporary)
            except (VerificationError, subprocess.TimeoutExpired) as error:
                entry["signature"]["error"] = str(error)
            if name == "conninfra":
                entry["alarm_import_crcs"] = {symbol: (f"0x{module['versions'][symbol]:08x}"
                                                      if symbol in module["versions"] else None)
                                               for symbol in ALARM_SYMBOLS}
                entry["alarm_target_crcs"] = {symbol: (f"0x{kernel_symbols[symbol]['crc']:08x}"
                                                      if symbol in kernel_symbols else None)
                                               for symbol in ALARM_SYMBOLS}
                if any(value is None for value in entry["alarm_import_crcs"].values()):
                    report["errors"].append("conninfra does not import all three expected alarm symbols")
            entry["verified"] = (metadata_matches and not missing and not mismatches and
                                 entry["signature"]["valid_with_run_certificate_only"])
            report["modules"].append(entry)
            if not entry["verified"]:
                report["errors"].append(f"{name}: module metadata, CRC or signature validation failed")

    if stock_root is not None:
        stock_files = [path for path in artifact_files(stock_root) if path.suffix == ".ko"]
        if not stock_files:
            raise VerificationError("No .ko files found in --stock-module-dir")
        comparisons = []
        for path in stock_files:
            data = path.read_bytes()
            module = parser.parse_module(data)
            missing, mismatches = compare_imports(module, combined)
            comparisons.append({"module": path.relative_to(stock_root).as_posix(),
                                "sha256": hashlib.sha256(data).hexdigest(),
                                "module_name": module["module_name"], "vermagic": module["vermagic"],
                                "depends": module["declared_dependencies"],
                                "versioned_imports": len(module["versions"]),
                                "missing_from_kernel_and_completed_modules": missing,
                                "crc_mismatches": mismatches,
                                "signature_or_runtime_compatibility_claimed": False})
        report["stock_modules_static_comparison"] = comparisons
        report["stock_comparison_influences_candidate_verification"] = False


def main():
    arguments = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    arguments.add_argument("--artifact-dir", type=Path, required=True)
    arguments.add_argument("--output", type=Path, required=True)
    arguments.add_argument("--allow-partial", action="store_true",
                           help="Allow a failed/incomplete run; all completed modules must still pass every check")
    arguments.add_argument("--stock-module-dir", type=Path,
                           help="Optionally compare old modules against candidate symbol tables; never load or sign them")
    options = arguments.parse_args()
    report = {"schema_version": 1, "audit_type": "offline_kernel_external_module_artifact_verification",
              "artifact_name": options.artifact_dir.name, "allow_partial": options.allow_partial,
              "verifier_sha256": sha256_file(Path(__file__).resolve()),
              "verified": False, "errors": [],
              "limitations": [
                  "Offline verification only; no module was loaded or executed and no device was accessed.",
                  "SHA256SUMS checks consistency with the downloaded manifests, not independent publisher authenticity.",
                  "CMS verification pins the run public certificate with -nointern; -noverify disables system CA-chain checks.",
                  "Certificate bytes embedded in Image plus valid signatures do not establish successful kernel trust-key initialization.",
                  "Matching CRCs and signatures do not establish boot, firmware, initialization or runtime compatibility.",
                  "Matching fpsgo_cus source is not included; these eight targets do not reproduce the separate fpsgo.ko."]}
    fatal = False
    output_safe = False
    try:
        root = options.artifact_dir.resolve()
        stock = options.stock_module_dir.resolve() if options.stock_module_dir else None
        output = options.output.resolve()
        if output.is_relative_to(root) or (stock is not None and output.is_relative_to(stock)):
            raise VerificationError("--output must be outside input artifact/module directories")
        parser_input = Path(__file__).resolve().with_name("compare-module-crcs.py")
        if output in (Path(__file__).resolve(), parser_input.resolve()):
            raise VerificationError("--output must not overwrite the verifier or CRC parser")
        output_safe = True
        report["artifact_directory"] = str(root)
        report["stock_module_directory"] = str(stock) if stock is not None else None
        if not root.is_dir():
            raise VerificationError("--artifact-dir must be a directory")
        if stock is not None and not stock.is_dir():
            raise VerificationError("--stock-module-dir must be a directory")
        verify_artifact(root, options.allow_partial, stock, report)
    except Exception as error:
        # The report records malformed inputs without dumping file contents.
        report["errors"].append(f"{type(error).__name__}: {error}")
        fatal = True
    report["verified"] = not report["errors"]
    report["verification_scope"] = "full" if report.get("full_build_success") else "partial"
    report["summary"] = {"completed_modules": len(report.get("completed_modules", [])),
                         "verified_modules": sum(item["verified"] for item in report.get("modules", [])),
                         "expected_modules": len(MODULE_DEPENDENCIES)}
    # Do not accidentally overwrite inputs when output-path validation failed.
    if not output_safe:
        print(json.dumps({"verified": False, "errors": report["errors"]}), file=sys.stderr)
        return 2
    try:
        options.output.parent.mkdir(parents=True, exist_ok=True)
        options.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    except OSError as error:
        print(f"Cannot write verification report: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"verified": report["verified"], "scope": report["verification_scope"],
                      **report["summary"], "report": str(options.output.resolve())}))
    return 2 if fatal else (0 if report["verified"] else 1)


if __name__ == "__main__":
    raise SystemExit(main())
