#!/usr/bin/env python3
"""Compile the MT6885 draft and validate generic bindings, without booting it."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path, PurePosixPath
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parent.parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    out = parser.parse_args().out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": 1, "passed": False, "device_boot_tested": False,
              "scope": "DTC plus generic/core and matching ACK bindings; no MT6885 board binding",
              "full_kernel_dtbs_check": False, "flashable": False}

    def run(label, command, require_silent=False):
        result = subprocess.run(command, text=True, capture_output=True, timeout=600)
        (out / (label + ".log")).write_text(result.stdout + result.stderr, encoding="utf-8")
        report.setdefault("commands", []).append({"label": label, "argv": command,
                                                  "exit_code": result.returncode})
        if result.returncode or (require_silent and (result.stdout.strip() or result.stderr.strip())):
            raise ValueError(f"{label} failed or emitted validation diagnostics; see log")
        return result.stdout

    try:
        target = json.loads((ROOT / "targets/android17.json").read_text())
        commit = target["kernel"]["revision"]
        report["ack_commit"] = commit
        source = ROOT / "bringup/mt6885/rmx2121cn-minimal-draft.dts"
        expected = json.loads((ROOT / "bringup/mt6885/offline-validation.json").read_text())
        report["dts_sha256"] = digest(source)
        if digest(source) != expected["dts_sha256"]:
            raise ValueError("DTS no longer matches the reviewed offline evidence")
        report["dtschema_version"] = importlib.metadata.version("dtschema")
        if report["dtschema_version"] != "2026.9":
            raise ValueError("expected dtschema 2026.9")
        report["dtc_version"] = run("dtc-version", ["dtc", "--version"]).strip()
        run("python-packages", ["python3", "-m", "pip", "freeze"])
        bindings = out / "bindings"
        bindings.mkdir(exist_ok=True)
        archive = out / "ack-bindings.tar.gz"
        url = ("https://android.googlesource.com/kernel/common/+archive/" + commit +
               "/Documentation/devicetree/bindings.tar.gz")
        report["bindings_url"] = url
        with urllib.request.urlopen(url, timeout=120) as response:
            archive.write_bytes(response.read())
        report["bindings_archive_sha256"] = digest(archive)
        # Only regular files/directories; never follow archive links or paths.
        with tarfile.open(archive, "r:gz") as package:
            for member in package.getmembers():
                name = PurePosixPath(member.name)
                if name.is_absolute() or ".." in name.parts or not (member.isfile() or member.isdir()):
                    raise ValueError("unexpected archive entry")
                dest = bindings.joinpath(*name.parts)
                if member.isdir():
                    dest.mkdir(parents=True, exist_ok=True)
                else:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with package.extractfile(member) as stream:
                        dest.write_bytes(stream.read())
        dtb = out / "rmx2121cn-minimal-draft.syntax-check.dtb"
        run("dtc", ["dtc", "-I", "dts", "-O", "dtb", "-o", str(dtb), str(source)], True)
        report["dtb_sha256"] = digest(dtb)
        schema = out / "processed-schema.json"
        run("mk-schema", ["dt-mk-schema", "-j", "-o", str(schema), str(bindings)])
        # dt-validate may exit zero despite findings. Any diagnostic fails this check.
        run("validate", ["dt-validate", "-s", str(schema), str(dtb)], True)
        run("unmatched-compatibles", ["dt-validate", "-m", "-s", str(schema), str(dtb)])
        report["coverage_limitations"] = [
            "mediatek,MT6885 is retained from hardware evidence and has no supported board binding",
            "Vendor reserve consumers, clocks, PMIC, watchdog, USB and UFS have not been ported",
            "Schema validity cannot establish bootloader memory contracts or physical functionality"]
        report["passed"] = True
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    (out / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "error": report.get("error")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
