# Minimal ACK configuration compile fixes

These two local patches apply only to Android common kernel commit
`846dd6f9b6c61b6aa95e141455ce8f0ea44b29ef` (6.18.33). They address the two
compile errors observed in experimental CI run `41165802262` while retaining
`CONFIG_PM=n`, `CONFIG_COREDUMP=n`, and `CONFIG_WERROR=y`. They do not enable
subsystems, suppress compiler warnings, or make this kernel suitable for Android
or a phone boot. They are local fixes, not a claim of upstream acceptance.

`series.json` specifies the ordered patch files and their SHA-256 digests,
and the complete original/patched SHA-256 digests of both affected source files.
The build must verify the fixed source commit, clean initial tree, patch hashes
and original file hashes before applying this series. It must then require the
exact two modified paths and patched hashes. Record the resulting diff and
require it to remain unchanged through compilation; a patched source tree must
not be reported as pristine ACK.

## Changes and execution-path reasoning

1. `0001-irqchip-gic-v3-drop-unused-no-pm-suspend-stub.patch` removes one
   unreferenced, non-inline `gic_v3_suspend()` stub from the `!CONFIG_PM` branch.
   At this fixed revision, the only reference to `gic_v3_suspend` is the
   `.suspend` member of `gic_syscore_ops`, and both that object and the real
   function are inside `#ifdef CONFIG_PM`. With PM disabled, the called
   `gic_syscore_init()` remains the same empty inline function. No IRQ
   initialization or handling code changes. The PM-enabled branch is unchanged.

2. `0002-binfmt-elf-guard-min-coredump-initialization.patch` places the assignment
   to `elf_format.min_coredump` inside `#ifdef CONFIG_COREDUMP`, matching the
   existing field declaration and static initializer guards. With core dumps
   enabled, the same assignment remains immediately before `register_binfmt()`.
   With core dumps disabled, the field does not exist; ELF registration and
   executable loading remain unchanged. The patch adds no new runtime branch.

These are source-level reasons that intended execution paths are unchanged.
The original minimal configuration failed to compile, so there is no original
minimal-config executable for a binary or runtime equivalence claim. Full
compilation and QEMU execution of the patched kernel remain CI validation work.

## Fixed-source evidence

- [drivers/irqchip/irq-gic-v3.c](https://android.googlesource.com/kernel/common/+/846dd6f9b6c61b6aa95e141455ce8f0ea44b29ef/drivers/irqchip/irq-gic-v3.c):
  lines 1518–1537 contain the PM guards, callback reference, and unused stub.
- [fs/binfmt_elf.c](https://android.googlesource.com/kernel/common/+/846dd6f9b6c61b6aa95e141455ce8f0ea44b29ef/fs/binfmt_elf.c):
  lines 91–98 guard the existing coredump initializer; lines 2129–2133 contain
  the previously unguarded assignment and unchanged ELF registration.
- [include/linux/binfmts.h](https://android.googlesource.com/kernel/common/+/846dd6f9b6c61b6aa95e141455ce8f0ea44b29ef/include/linux/binfmts.h):
  lines 89–97 guard the `min_coredump` field with `CONFIG_COREDUMP`.
  This supporting header is unchanged; its SHA-256 is
  `2babd6a08878dca84ab3cfceffdbc315fd9d40398fe106bb60d0632e1e9b6136`.

Local validation checks patch application with `git apply --check`, applies
the series to exact downloaded copies, checks the resulting file hashes, and
checks reverse applicability. No phone, boot image, or device operation is part
of this patch series.
