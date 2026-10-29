# ARM64 minimal diagnostic initramfs

This is a host-side build input for early kernel bring-up. It is **not an Android
boot image or a flash-ready RMX2121CN artifact**. The local preparation did not
compile or execute ARM64 code. A successful later QEMU `virt` run would validate
the generic kernel/initramfs path only; it would not validate the MT6885 device
tree, hardware drivers, reserved-memory contracts or phone boot.

`/init` uses the AArch64 Linux syscall ABI directly, with no libc, shell or dynamic
linker. It opens the archive's `/dev/console` and `/dev/kmsg` character nodes,
reports its PID, mounts only proc, sysfs and (when available) devtmpfs, reports
`uname` sysname/release/machine, and stays as PID 1 in a reaping/sleeping loop.
There is no block-device access, persistent filesystem mount, module loading,
network setup, Android startup, external program execution or automatic reboot.
This describes the init program, not all possible writes by a kernel, firmware
or bootloader.

The archive has seven entries: `dev`, `proc`, `sys`, `dev/console` (character 5:1),
`dev/kmsg` (character 1:11), `dev/null` (character 1:3) and `init`. When the kernel
does not enable `CONFIG_DEVTMPFS`, its mount returns a negative error and init
explicitly reports `devtmpfs=fallback-to-archive-character-nodes`. The previously
opened console and kmsg descriptors continue to use those static nodes.

## Build

Requires Python 3, Clang with an AArch64 backend and LLD. No target sysroot or
target libc is needed. For a Linux CI runner with Clang/LLD already installed:

```sh
python3 build.py --out "$RUNNER_TEMP/minimal-initramfs" --clang clang --lld ld.lld
```

Use explicit `--clang` and `--lld` paths to use a pinned Android toolchain. The
manifest records compiler/linker first-line versions and executable SHA-256,
the normalized command, source hashes and output hashes. Distribution packages
are not pinned by this script; save the manifest and package versions if using
the CI runner's package manager. For bit-for-bit reproduction, use the same
source and compiler/linker binaries.

The script compiles twice and requires identical ELF bytes. It rejects a dynamic
interpreter, PT_DYNAMIC, a non-AArch64/non-ET_EXEC file, a missing executable
entry mapping, malformed load bounds or writable/executable load segments.
It creates deterministic `newc` metadata (UID/GID/mtime zero), checks archive
entries by reading them back, and wraps the archive in deterministic gzip with
stored DEFLATE blocks. This deliberately avoids compression-library version
differences; the small diagnostic archive does not need strong compression.

Outputs are `init`, `init.rebuild`, `initramfs.cpio`, `initramfs.cpio.gz`,
`manifest.json` and `SHA256SUMS`. The manifest keeps `runtime_tested: false` and
`device_tested: false`: compiling cannot establish either. Save emulator runtime
evidence separately. `--pack-only PATH` packages an existing validated ARM64 ELF
but does not establish compiler provenance or reproduction of that ELF.

## Runtime evidence

A subsequent emulator check should require these stable log markers, plus an
expected kernel release and absence of a kernel panic:

```text
rmx618-init: stage=entered
rmx618-init: pid=1
rmx618-init: mount-proc=0
rmx618-init: mount-sysfs=0
rmx618-init: uname=0
rmx618-init: machine=aarch64
rmx618-init: stage=idle; persistent-storage=untouched-by-init
```

`mount-devtmpfs=-19` and the explicit fallback message are expected with
`CONFIG_DEVTMPFS` unset. Console and kmsg may both produce a copy of a marker.
Kernel command line `rdinit=/init console=ttyAMA0` is appropriate for an ARM64
QEMU `virt` machine with PL011 enabled, **not a suggested phone boot command**.
Use the emulator's own generated device tree. No MT6885 DT should be substituted
into that emulator smoke test, and no phone storage or host writable disk needs
to be attached. The init intentionally never exits; the test runner must stop
the emulator after observing the success marker and a short observation period.

On a phone, a working log channel and an independently verified recovery method
must exist first. This init does not implement USB or ADB. Hardware watches and
firmware can still reset the system. The platform prerequisites in the parent
porting README remain unresolved.

## Local validation and sources

`validate_format.py --out validation` uses a deliberately non-executable text
fixture to check the newc/gzip format and independent tar listing/extraction.
It must not be confused with an init build. Results are in
`validation/format-validation.json`. Python syntax checks and these format checks
do not establish AArch64 code generation or boot success. The local Windows GCC
uses LLP64, not AArch64 Linux LP64, and is not used to claim target compilation.

The ABI constants and archive layout were checked against Linux v6.18 primary
sources: [syscall table](https://github.com/torvalds/linux/blob/v6.18/scripts/syscall.tbl),
[open flags](https://github.com/torvalds/linux/blob/v6.18/include/uapi/asm-generic/fcntl.h),
[uname layout](https://github.com/torvalds/linux/blob/v6.18/include/uapi/linux/utsname.h)
and [kernel cpio generator](https://github.com/torvalds/linux/blob/v6.18/usr/gen_init_cpio.c).
The project ACK source remains pinned independently to its recorded Android
17 / 6.18 commit.
