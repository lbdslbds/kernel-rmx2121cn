#!/usr/bin/env python3
"""Merge real fixed-source defconfig CPU fixes into the minimal configuration."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re

spec = importlib.util.spec_from_file_location("config_reader", Path(__file__).with_name("verify-config.py"))
config_reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config_reader)

MANDATORY = ["ARM64_ERRATUM_1024718", "ARM64_ERRATUM_1530923", "ARM64_ERRATUM_1508412",
             "ARM64_ERRATUM_3194386", "ARM64_ERRATUM_4118414",
             "UNMAP_KERNEL_AT_EL0", "MITIGATE_SPECTRE_BRANCH_HISTORY"]
MITIGATION = re.compile(r"MITIGAT|SPECTRE|^UNMAP_KERNEL_AT_EL0$|^ARM64_(?:PAN|SW_TTBR0_PAN|EPAN|E0PD|PTR_AUTH|PTR_AUTH_KERNEL|BTI|BTI_KERNEL)$")
# Each exception must match a literal depends-on expression in fixed Kconfig,
# and that feature must be explicitly disabled in the user's experiment seed.
INACTIVE = {
    "ARM64_ERRATUM_1742098": "COMPAT",
    "ARM64_ERRATUM_845719": "COMPAT",
    "ARM64_ERRATUM_1418040": "COMPAT",
    "ARM64_ERRATUM_2119858": "CORESIGHT_TRBE",
    "ARM64_ERRATUM_2139208": "CORESIGHT_TRBE",
    "ARM64_ERRATUM_2253138": "CORESIGHT_TRBE",
    "ARM64_ERRATUM_2224489": "CORESIGHT_TRBE",
    "ARM64_ERRATUM_2064142": "CORESIGHT_TRBE",
    "ARM64_ERRATUM_2038923": "CORESIGHT_TRBE",
    "ARM64_ERRATUM_1902691": "CORESIGHT_TRBE",
    "ARM64_ERRATUM_2457168": "ARM64_AMU_EXTN",
    "ARM64_ERRATUM_4193714": "ARM64_SME",
    "ARM64_ERRATUM_834220": "KVM",
    "CAVIUM_ERRATUM_23144": "NUMA",
}


def definitions(text):
    matches = list(re.finditer(r"^(?:menu)?config\s+([A-Za-z0-9_]+)\s*$", text, re.M))
    return {match.group(1): text[match.end():matches[i+1].start() if i+1 < len(matches) else len(text)]
            for i, match in enumerate(matches)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--requested", type=Path, required=True)
    parser.add_argument("--output-config", type=Path, required=True)
    parser.add_argument("--output-report", type=Path, required=True)
    args = parser.parse_args()
    baseline = config_reader.parse(args.baseline)
    requested = config_reader.parse(args.requested)
    source = args.source / "arch/arm64/Kconfig"
    defined = definitions(source.read_text())
    source_paths = {name: "arch/arm64/Kconfig" for name in defined}
    source_hashes = {"arch/arm64/Kconfig": hashlib.sha256(source.read_bytes()).hexdigest()}
    # Some CPU errata (for example ARM64_ERRATUM_858921) live with their timer
    # implementation rather than in arch/arm64/Kconfig. Include ARM64-named
    # errata from every Kconfig, without sweeping in unrelated device quirks.
    for path in args.source.rglob("Kconfig*"):
        if not path.is_file() or path == source:
            continue
        text = path.read_text(encoding="utf-8")
        if not re.search(r"^(?:menu)?config\s+ARM64_ERRATUM_", text, re.M):
            continue
        relative = path.relative_to(args.source).as_posix()
        for name, block in definitions(text).items():
            if not name.startswith("ARM64_ERRATUM_"):
                continue
            if name in defined:
                raise ValueError(f"Ambiguous CPU erratum declaration: {name}")
            defined[name] = block
            source_paths[name] = relative
        source_hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    for name in MANDATORY:
        if name not in defined or baseline.get("CONFIG_" + name) != "y":
            raise ValueError(f"Mandatory CPU safeguard absent from resolved defconfig: {name}")
    preserved, inactive, candidate_defaults = {}, [], {}
    for symbol, block in sorted(defined.items()):
        name = "CONFIG_" + symbol
        value = baseline.get(name, "n")
        is_erratum = "ERRATUM" in symbol
        is_mitigation = bool(MITIGATION.search(symbol))
        if not ((is_erratum and value == "y") or is_mitigation):
            continue
        if value not in ("y", "n"):
            raise ValueError(f"Unexpected CPU safeguard type: {name}={value}")
        candidate_defaults[name] = value
        feature = INACTIVE.get(symbol)
        if value == "y" and feature and requested.get("CONFIG_" + feature) == "n":
            dependencies = re.findall(r"^\s*depends on\s+(.+)$", block, re.M)
            if feature not in dependencies:
                raise ValueError(f"Inactive-dependency policy no longer matches fixed source: {symbol}")
            inactive.append({"symbol": name, "baseline_value": "y", "final_required_value": "n",
                             "dependency_expression": feature,
                             "disabled_feature": "CONFIG_" + feature,
                             "disabled_feature_required_value": "n",
                             "source_path": source_paths[symbol]})
            continue
        if name in requested and requested[name] != value:
            raise ValueError(f"Experiment conflicts with baseline CPU protection: {name}")
        preserved[name] = value
    if not preserved:
        raise ValueError("No baseline CPU defaults were found")
    merged = dict(requested)
    merged.update(preserved)
    for entry in inactive:
        if entry["symbol"] in merged and merged[entry["symbol"]] != "n":
            raise ValueError(f"Inactive CPU erratum explicitly enabled: {entry['symbol']}")
        merged[entry["symbol"]] = "n"
    lines = ["# Generated from minimal.config plus fixed-source resolved defconfig CPU safeguards."]
    lines += [f"# {name} is not set" if value == "n" else f"{name}={value}"
              for name, value in sorted(merged.items())]
    args.output_config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = {"schema_version": 1, "source_commit": "846dd6f9b6c61b6aa95e141455ce8f0ea44b29ef",
              "baseline_method": "make ARCH=arm64 LLVM=1 LLVM_IAS=1 defconfig with the same build toolchain",
              "baseline_config_sha256": hashlib.sha256(args.baseline.read_bytes()).hexdigest(),
              "source_kconfig_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "cpu_kconfig_sources_sha256": source_hashes,
              "scan_scope": "All ERRATUM-named symbols and mitigation/PAN/PAC/BTI defaults declared in arch/arm64/Kconfig, plus ARM64_ERRATUM_* declarations in every other Kconfig",
              "seed_config_sha256": hashlib.sha256(args.requested.read_bytes()).hexdigest(),
              "merged_config_sha256": hashlib.sha256(args.output_config.read_bytes()).hexdigest(),
              "baseline_cpu_safeguard_values": candidate_defaults,
              "preserved_required_values": preserved,
              "mandatory_required_enabled": ["CONFIG_" + name for name in MANDATORY],
              "inactive_dependency": inactive,
              "limitations": ["Only explicit source-verified dependencies allow inactive errata; every other dropped baseline safeguard fails the final gate.",
                              "A disabled optional CPU/trace/NUMA feature does not require enabling its hardware just to retain the feature-specific workaround.",
                              "This does not prove complete hardware errata coverage or phone safety."]}
    args.output_report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"preserved_values": len(preserved), "inactive_dependencies": len(inactive)}))


if __name__ == "__main__":
    main()
