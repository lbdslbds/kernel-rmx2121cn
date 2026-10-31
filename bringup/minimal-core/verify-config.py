#!/usr/bin/env python3
"""Fail if Kconfig dropped a request or enabled excluded hardware subsystems."""
import argparse
import hashlib
import json
from pathlib import Path
import re

ASSIGN = re.compile(r"(CONFIG_[A-Za-z0-9_]+)=(.*)")
UNSET = re.compile(r"# (CONFIG_[A-Za-z0-9_]+) is not set")
DEFINE = re.compile(r"^(?:menu)?config\s+([A-Za-z0-9_]+)\s*$", re.M)
MTK = re.compile(r"(?:^|_)(?:MEDIATEK|MTK)(?:_|$)|(?:^|_)MT\d{2,}(?:_|$)")
FRAMEWORKS = ["ARM_GIC", "ARM_GIC_V3", "ARM_GIC_V3_ITS", "ARM_GIC_V5", "ARM_AMBA",
              "ARM_PSCI_FW", "ARM_ARCH_TIMER", "OF", "OF_EARLY_FLATTREE", "OF_RESERVED_MEM",
              "DMA_DECLARE_COHERENT", "COMMON_CLK", "POWER_RESET", "POWER_SUPPLY", "SMP", "MMU"]
DERIVED = {"CONFIG_ARM64_VA_BITS": "39", "CONFIG_ARM64_PA_BITS": "48",
           "CONFIG_OF_RESERVED_MEM": "y", "CONFIG_DECOMPRESS_GZIP": "y"}


def parse(path):
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = ASSIGN.fullmatch(line) or UNSET.fullmatch(line)
        if not match:
            if line and not line.startswith("#"):
                raise ValueError(f"Unrecognized config line in {path.name}")
            continue
        name = match.group(1)
        if name in values:
            raise ValueError(f"Duplicate configuration symbol: {name}")
        values[name] = match.group(2) if len(match.groups()) == 2 else "n"
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--requested", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--cpu-policy", type=Path, required=True)
    parser.add_argument("--cpu-baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = {"schema_version": 1, "passed": False, "errors": [],
              "not_for_flashing": True, "phone_boot_tested": False}
    try:
        requested, actual = parse(args.requested), parse(args.config)
        cpu_policy = json.loads(args.cpu_policy.read_text())
        if cpu_policy["source_commit"] != "846dd6f9b6c61b6aa95e141455ce8f0ea44b29ef":
            raise ValueError("CPU policy source revision differs")
        if cpu_policy["baseline_config_sha256"] != hashlib.sha256(args.cpu_baseline.read_bytes()).hexdigest():
            raise ValueError("CPU baseline digest differs from policy")
        if cpu_policy["merged_config_sha256"] != hashlib.sha256(args.requested.read_bytes()).hexdigest():
            raise ValueError("Effective requested configuration differs from CPU policy")
        cpu_mismatches = []
        for name, value in cpu_policy["preserved_required_values"].items():
            if actual.get(name, "n") != value:
                cpu_mismatches.append({"symbol": name, "expected": value, "actual": actual.get(name, "n")})
        for name in cpu_policy["mandatory_required_enabled"]:
            if actual.get(name) != "y":
                cpu_mismatches.append({"symbol": name, "expected": "y", "actual": actual.get(name, "n")})
        for inactive in cpu_policy["inactive_dependency"]:
            for name in (inactive["symbol"], inactive["disabled_feature"]):
                if actual.get(name, "n") != "n":
                    cpu_mismatches.append({"symbol": name, "expected": "n", "actual": actual.get(name)})
        if cpu_mismatches:
            report["errors"].append({"cpu_safeguard_mismatches": cpu_mismatches})
        declarations = {}
        for path in args.source.rglob("Kconfig*"):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="strict")
            for symbol in DEFINE.findall(text):
                declarations.setdefault("CONFIG_" + symbol, []).append(path.relative_to(args.source).as_posix())
        unknown = sorted(set(requested) - declarations.keys())
        if unknown:
            report["errors"].append({"unknown_requested_symbols": unknown})
        mismatches = []
        for name, value in {**requested, **DERIVED}.items():
            effective = actual.get(name, "n")
            if effective != value:
                mismatches.append({"symbol": name, "requested": value, "actual": effective})
        if mismatches:
            report["errors"].append({"required_value_mismatches": mismatches})
        platforms = sorted("CONFIG_" + symbol for symbol in DEFINE.findall(
            (args.source / "arch/arm64/Kconfig.platforms").read_text()) if symbol.startswith("ARCH_"))
        if not platforms or "CONFIG_ARCH_MEDIATEK" not in platforms:
            raise ValueError("ARM64 platform inventory could not be established from source")
        selected_platforms = [name for name in platforms if actual.get(name, "n") in ("y", "m")]
        selected_mtk = sorted(name for name, value in actual.items()
                              if value in ("y", "m") and MTK.search(name.removeprefix("CONFIG_")))
        modules = sorted(name for name, value in actual.items() if value == "m")
        if selected_platforms:
            report["errors"].append({"enabled_platform_families": selected_platforms})
        if selected_mtk:
            report["errors"].append({"enabled_mediatek_named_symbols": selected_mtk})
        if modules:
            report["errors"].append({"module_valued_symbols": modules})
        report.update({
            "requested_config_sha256": hashlib.sha256(args.requested.read_bytes()).hexdigest(),
            "final_config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(),
            "requested_count": len(requested),
            "cpu_default_policy": cpu_policy,
            "cpu_safeguard_mismatches": cpu_mismatches,
            "required_enabled": {name: value for name, value in requested.items() if value == "y"},
            "required_disabled": sorted(name for name, value in requested.items() if value == "n"),
            "required_other_values": {name: value for name, value in requested.items() if value not in ("y", "n")},
            "required_derived_values": DERIVED,
            "unknown_requested_symbols": unknown,
            "required_value_mismatches": mismatches,
            "arm64_platform_family_inventory": platforms,
            "enabled_platform_families": selected_platforms,
            "enabled_mediatek_named_symbols": selected_mtk,
            "module_valued_symbols": modules,
            "architecture_framework_values": {"CONFIG_" + name: actual.get("CONFIG_" + name, "n") for name in FRAMEWORKS},
            "source_locations_for_requests": {name: declarations.get(name, []) for name in requested},
            "limitations": [
                "This is a final configuration check, not proof of kernel execution or hardware safety.",
                "ARM64 selects generic AMBA, clocks, GIC variants, OF, power/reset and other infrastructure.",
                "OF reserved-memory allocation still runs, including dynamic size-based reservations.",
                "Disabling WATCHDOG prevents building selected watchdog drivers; it does not stop a firmware-started watchdog.",
                "MediaTek name scanning is additional evidence, not a formal inventory of every possible SoC-specific code path."]})
        report["passed"] = not report["errors"]
    except Exception as error:
        report["errors"].append(f"{type(error).__name__}: {error}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "errors": report["errors"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
