#!/usr/bin/env python3
"""Execute the tiny init on QEMU virt; never use the RMX2121 device tree."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

IMAGE_SHA256 = "f6c881edb063bd55d4ae7319a62a05b9c4f5b7960e5fe30bb5bc0a4d50e7373e"
MARKERS = ["stage=entered", "pid=1", "mount-proc=0", "mount-sysfs=0",
           "mount-devtmpfs=-19", "devtmpfs=fallback-to-archive-character-nodes",
           "uname=0", "machine=aarch64", "stage=idle; persistent-storage=untouched-by-init"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--initrd", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--expected-kernel-sha256", default=IMAGE_SHA256,
                        help="Required image digest from the reviewed build provenance")
    parser.add_argument("--expected-release", default="6.18.33-android17-5-maybe-dirty-4k")
    options = parser.parse_args()
    options.out.mkdir(parents=True, exist_ok=True)
    report = {"passed": False, "platform": "QEMU virt only", "phone_boot_tested": False,
              "rmx2121_dtb_used": False, "storage_devices_attached": False,
              "network_devices_attached": False}
    process = None
    log_path = options.out / "console.log"
    try:
        if not re.fullmatch(r"[a-f0-9]{64}", options.expected_kernel_sha256):
            raise ValueError("invalid expected Image SHA-256")
        if not re.fullmatch(r"[A-Za-z0-9._+\-]{1,160}", options.expected_release):
            raise ValueError("invalid expected kernel release")
        report["expected_kernel_sha256"] = options.expected_kernel_sha256
        report["expected_release"] = options.expected_release
        report["kernel_sha256"] = hashlib.sha256(options.kernel.read_bytes()).hexdigest()
        if report["kernel_sha256"] != options.expected_kernel_sha256:
            raise ValueError("kernel does not match the expected Image digest")
        report["initramfs_sha256"] = hashlib.sha256(options.initrd.read_bytes()).hexdigest()
        report["qemu_version"] = subprocess.check_output(
            ["qemu-system-aarch64", "--version"], text=True).strip()
        command = ["qemu-system-aarch64", "-machine", "virt,gic-version=3", "-accel", "tcg",
                   "-cpu", "max", "-smp", "2", "-m", "1024", "-nodefaults",
                   "-display", "none", "-monitor", "none", "-serial", "stdio",
                   "-nic", "none", "-no-reboot", "-kernel", str(options.kernel.resolve()),
                   "-initrd", str(options.initrd.resolve()), "-append",
                   "console=ttyAMA0 earlycon=pl011,0x09000000 rdinit=/init loglevel=7 panic=-1"]
        report["command"] = command
        start = time.monotonic()
        found_at = None
        with log_path.open("wb") as log:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            while time.monotonic() - start < 180:
                text = log_path.read_text(errors="replace")
                markers = all(re.search(re.escape("rmx618-init: " + marker) + r"(?:\r?\n)", text)
                              for marker in MARKERS)
                version = re.search(re.escape("rmx618-init: release=" + options.expected_release) + r"(?:\r?\n)", text)
                if re.search(r"Kernel panic|Oops:|BUG:|Unable to handle kernel", text):
                    raise ValueError("kernel fault in console log")
                if process.poll() is not None:
                    raise ValueError("QEMU exited before observation completed")
                if markers and version and found_at is None:
                    found_at = time.monotonic()
                if found_at is not None and time.monotonic() - found_at >= 5:
                    report["passed"] = True
                    break
                time.sleep(0.5)
        report["elapsed_seconds"] = round(time.monotonic() - start, 2)
        report["required_markers"] = MARKERS
        report["idle_observation_seconds"] = 5 if report["passed"] else 0
        if not report["passed"]:
            raise ValueError("diagnostic init did not reach the expected state within 180 seconds")
    except Exception as error:
        report["passed"] = False
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        report["qemu_exit_code_after_host_stop"] = process.returncode if process else None
        if log_path.exists():
            final_text = log_path.read_text(errors="replace")
            if re.search(r"Kernel panic|Oops:|BUG:|Unable to handle kernel", final_text):
                report["passed"] = False
                report["error"] = "kernel fault in final console log"
            report["console_sha256"] = hashlib.sha256(log_path.read_bytes()).hexdigest()
        (options.out / "verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "error": report.get("error")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
