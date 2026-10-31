# Minimal ACK ARM64 execution core

This is a separate **CI/QEMU experiment**, not an Android-compatible kernel or a
phone image. It starts from the fixed ACK 6.18.33 source commit
`846dd6f9b6c61b6aa95e141455ce8f0ea44b29ef` and an `allnoconfig` seed. It does not
reuse the Android GKI configuration or silently claim the existing generic ACK
binary meets these restrictions. No `boot.img`, DT table, device overlay or
flash command is produced.

The objective is a small execution path with ARM64 MMU/SMP, PSCI, GIC, architectural
timer, static ELF `/init`, external gzip initramfs, proc/sysfs, printk, and built-in
ramoops/console support. PL011 exists for the QEMU `virt` console only. The phone's
UART is not configured. `DEVTMPFS=n` retains the already checked diagnostic
program's static console/kmsg/null fallback. Eight CPUs are permitted; that does
not prove the phone's CPU topology or firmware behaves correctly.

`BLOCK=n` is compatible with `BLK_DEV_INITRD=y` in this fixed source: the latter
is the independent `init/Kconfig` switch enabling external initramfs handling,
not a request for a block-storage driver. ELF/gzip/proc/sysfs prerequisites are
explicitly enabled and checked. CPU fixes are preserved separately from driver
selection; disabling peripheral drivers must not discard normal CPU safeguards.
`MISC_FILESYSTEMS=y` is also required: `fs/Kconfig` includes the pstore options
inside that menu. The parent option adds no code on its own; block storage
remains disabled. The first CI attempt caught this missing parent dependency
before kernel compilation, and the final configuration gate remains mandatory.

## Configuration gate

`scripts/build-minimal-ack.sh` performs the following sequence:

1. Fetch the exact official Git object and require the checked-out HEAD to match;
   record its tree, source timestamp and pristine working-tree state. Apply the
   two declared [conditional-compilation fixes](../../patches/minimal-ack/README.md)
   only after verifying their hashes and both original source hashes. Require
   exactly the two declared modified paths and patched file hashes, save the
   complete diff, and require the same diff and hashes after compilation.
2. Resolve the fixed source's `defconfig` with the same compiler. The scan covers
   all `ERRATUM`-named symbols and mitigation/PAN/pointer-authentication/BTI
   defaults declared in `arch/arm64/Kconfig`, plus `ARM64_ERRATUM_*` declared in
   other Kconfig files (such as the architectural timer's 858921 workaround).
   Merge enabled errata and mitigation defaults into `effective-requested.config`.
   Require A55/A77 fixes 1024718,
   1530923, 1508412, 3194386 and 4118414 plus KPTI/Spectre branch-history protection.
   Additional A55 workaround 2441007 is explicitly requested by the seed.
   Feature-specific exceptions have a fixed per-symbol list, a literal
   source-verified `depends on` expression, and an explicitly disabled feature:
   COMPAT, CORESIGHT_TRBE, ARM64_AMU_EXTN, ARM64_SME, KVM or NUMA. Each actual
   exception is reported as `inactive_dependency`; final verification requires
   both feature and dependent erratum to be `n`. It does not silently skip any
   other missing CPU fix or re-enable tracing hardware for an unused workaround.
3. Run `KCONFIG_ALLCONFIG=effective-requested.config allnoconfig`, then
   `olddefconfig` with the same `ARCH=arm64 LLVM=1 LLVM_IAS=1` settings.
4. Require every requested symbol to exist in fixed-source Kconfig and every
   requested value to survive. Disabled invisible symbols may be absent from
   `.config`, which has the same effective `n` meaning. Also check derived
   address widths, reserved-memory support and gzip decompression.
5. Require all `ARCH_*` platform-family symbols declared in
   `arch/arm64/Kconfig.platforms` to be disabled, no `m` values anywhere, and no
   enabled MediaTek-named symbol matching the documented name patterns. This is
   additional evidence, not a formal classification of all architecture code.
6. Build `Image`/`Image.gz`, require final configuration bytes to remain unchanged,
   re-run the configuration gate, and verify the ARM64/4-KiB image header, expected
   release string and gzip equality before writing the output manifest.

The required exclusions include modules, block storage, networking, USB,
MMC/SD/SDIO, SCSI/UFS, ATA/NVMe/MTD, PCI, I2C/SPI/SPMI, pinctrl/GPIO/regulators,
IOMMU driver selection, watchdog drivers, CPU idle/frequency control, PM, ACPI,
EFI, graphics/audio/input/media and remote processors. The checker stops on a
missing, renamed or force-enabled required symbol; it does not turn failures into
warnings or rewrite Kconfig dependencies to force the requested answer.

The fixed ACK source has two build defects when `PM=n` and `COREDUMP=n`: an
unused GIC suspend stub and an unconditional assignment to a coredump-only ELF
field. The local patches fix those conditional-compilation paths while keeping
`WERROR=y`, both features disabled, and every configuration check active. They
do not add a device driver. The output manifest explicitly identifies the
patched source; it is not described as an untouched ACK source build.

ARM64 itself selects extra generic infrastructure: AMBA, COMMON_CLK, power/reset
frameworks, OF/reserved memory, GICv2/v3/v5 and other helpers. GICv3 ITS has a
hidden default following GICv3. The final values are recorded, not incorrectly
forced off or described as absent. In particular, **reserved-memory dynamic
allocation still executes**, and **no watchdog driver does not mean a watchdog
started by firmware has stopped**. Bootloader-applied DT overlays may restore
nodes to `okay`; compiling peripheral drivers out avoids relying on those status
properties, but does not neutralize firmware or prove phone safety.

## Build and integration

On Ubuntu 24.04 with the workflow's dependencies installed, run:

```sh
bash scripts/build-minimal-ack.sh
```

`JOBS` defaults to 4. The build uses direct upstream Kbuild with LLVM integrated
assembly. The fixed source requires LLVM >=15 on ARM64; Ubuntu's actual compiler,
linker and LLVM tool versions/executable hashes and installed package versions
are saved. Packages are not fully pinned, and this is not the official Android
Kleaf/r584948c production toolchain. `CONFIG_LOCALVERSION` and an explicit empty
make `LOCALVERSION` give the expected release `6.18.33-rmx2121-minimal-core`.

Outputs:

- `artifacts/minimal-ack/kernel/`: `Image`, `Image.gz`, `System.map`,
  `final.config`, `kernelrelease.txt`, `target.json`, `build-manifest.json`,
  `SHA256SUMS`, and a non-flashable warning.
- `artifacts/minimal-ack/diagnostics/`: build log/exit code, source identity,
  toolchain/package inventory, requested/resolved configs, the resolved CPU
  defconfig baseline/default-preservation policy, and both configuration reports.

The workflow builds the existing deterministic diagnostic initramfs after the
kernel and runs the existing QEMU checker against this build's `Image`. It passes
the Image SHA-256 from `build-manifest.json` and the exact `kernelrelease.txt`
value as expectations. Initramfs outputs and QEMU logs/reports are saved under
`artifacts/minimal-ack/initramfs/` and `artifacts/minimal-ack/qemu/`, respectively,
and uploaded alongside the kernel and build diagnostics. QEMU uses its own
`virt` DT, with no phone DT, drives or network. Successful
compilation or QEMU execution cannot validate phone memory contracts, overlay
effects, ramoops retention, PMIC state or watchdog behavior.

On 2026-10-31, [run 41166304335](https://github.com/lbdslbds/kernel-rmx2121cn/actions/runs/41166304335)
passed full configuration resolution, compilation, the before/after source and
configuration checks, deterministic initramfs construction, and QEMU execution.
The release is `6.18.33-rmx2121-minimal-core`; Image is 3,975,176 bytes with SHA-256
`8cbfd916d8f8978fd008d7c5972faa5f2ea13fd8aebb4cf59242e521de9d6d2e`.
The diagnostic program reached PID 1 idle, followed by five seconds of host
observation and host termination. See the [artifact review](../../reports/minimal-ack-20261031.json).
No phone execution, Android compatibility or long-term stability is established.
