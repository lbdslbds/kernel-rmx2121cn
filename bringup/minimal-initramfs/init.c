/* SPDX-License-Identifier: MIT
 * AArch64 Linux diagnostic PID 1. No libc, shell, modules, network, reboot,
 * persistent filesystem mounts, or block-device access.
 */
#if !defined(__aarch64__)
#error This program targets the AArch64 Linux syscall ABI.
#endif

typedef unsigned long usize;

/* Linux v6.18 scripts/syscall.tbl, AArch64 LP64 ABI. */
enum { NR_mount = 40, NR_openat = 56, NR_write = 64,
       NR_nanosleep = 101, NR_uname = 160, NR_getpid = 172, NR_wait4 = 260 };
enum { AT_FDCWD = -100, O_WRONLY = 1, O_NONBLOCK = 04000,
       O_CLOEXEC = 02000000, MS_NOSUID = 2, MS_NODEV = 4, MS_NOEXEC = 8 };

static long console_fd = -1;
static long kmsg_fd = -1;

static long call6(long number, long a0, long a1, long a2,
                  long a3, long a4, long a5)
{
    register long x0 __asm__("x0") = a0;
    register long x1 __asm__("x1") = a1;
    register long x2 __asm__("x2") = a2;
    register long x3 __asm__("x3") = a3;
    register long x4 __asm__("x4") = a4;
    register long x5 __asm__("x5") = a5;
    register long x8 __asm__("x8") = number;
    __asm__ volatile("svc #0" : "+r"(x0)
                     : "r"(x1), "r"(x2), "r"(x3), "r"(x4), "r"(x5), "r"(x8)
                     : "memory", "cc");
    return x0;
}

static usize length(const char *text)
{
    usize n = 0;
    while (text[n])
        ++n;
    return n;
}

static void write_text(long fd, const char *text)
{
    usize left = length(text);
    /* Nonblocking log endpoints: do not wait forever on an unavailable console. */
    unsigned attempts = 0;
    while (fd >= 0 && left && attempts++ < 8) {
        long done = call6(NR_write, fd, (long)text, (long)left, 0, 0, 0);
        if (done == -4) /* EINTR */
            continue;
        if (done <= 0 || (usize)done > left)
            break;
        text += done;
        left -= (usize)done;
    }
}

static void emit(const char *text)
{
    write_text(kmsg_fd, text);
    write_text(console_fd, text);
}

static void stage_result(const char *label, long result)
{
    char line[160];
    char reversed[24];
    usize used = 0, count = 0;
    const char *prefix = "<6>rmx618-init: ";
    while (*prefix && used < sizeof(line) - 1)
        line[used++] = *prefix++;
    while (*label && used < sizeof(line) - 1)
        line[used++] = *label++;
    line[used++] = '=';
    if (result < 0) {
        line[used++] = '-';
        result = -result; /* syscall results here cannot be LONG_MIN */
    }
    do {
        reversed[count++] = (char)('0' + result % 10);
        result /= 10;
    } while (result && count < sizeof(reversed));
    while (count && used < sizeof(line) - 2)
        line[used++] = reversed[--count];
    line[used++] = '\n';
    line[used] = 0;
    emit(line);
}

static void uname_field(const char *label, const char *value)
{
    char line[160];
    usize used = 0;
    const char *prefix = "<6>rmx618-init: ";
    while (*prefix)
        line[used++] = *prefix++;
    while (*label && used < sizeof(line) - 2)
        line[used++] = *label++;
    line[used++] = '=';
    for (usize i = 0; i < 65 && value[i] && used < sizeof(line) - 2; ++i)
        line[used++] = value[i];
    line[used++] = '\n';
    line[used] = 0;
    emit(line);
}

__attribute__((noreturn)) void init_main(void)
{
    struct { char sysname[65], nodename[65], release[65], version[65],
                  machine[65], domainname[65]; } identity;
    struct { long seconds; long nanoseconds; } delay = {30, 0};
    long flags = O_WRONLY | O_NONBLOCK | O_CLOEXEC;

    /* Archive includes character nodes even when devtmpfs is unavailable. */
    console_fd = call6(NR_openat, AT_FDCWD, (long)"/dev/console", flags, 0, 0, 0);
    kmsg_fd = call6(NR_openat, AT_FDCWD, (long)"/dev/kmsg", flags, 0, 0, 0);
    emit("<6>rmx618-init: stage=entered\n");
    stage_result("pid", call6(NR_getpid, 0, 0, 0, 0, 0, 0));
    stage_result("mount-proc", call6(NR_mount, (long)"proc", (long)"/proc",
                 (long)"proc", MS_NOSUID | MS_NODEV | MS_NOEXEC, 0, 0));
    stage_result("mount-sysfs", call6(NR_mount, (long)"sysfs", (long)"/sys",
                 (long)"sysfs", MS_NOSUID | MS_NODEV | MS_NOEXEC, 0, 0));
    long devtmpfs_rc = call6(NR_mount, (long)"devtmpfs", (long)"/dev",
                           (long)"devtmpfs", MS_NOSUID | MS_NOEXEC,
                           (long)"mode=0755", 0);
    stage_result("mount-devtmpfs", devtmpfs_rc);
    if (devtmpfs_rc < 0)
        emit("<6>rmx618-init: devtmpfs=fallback-to-archive-character-nodes\n");

    long rc = call6(NR_uname, (long)&identity, 0, 0, 0, 0, 0);
    stage_result("uname", rc);
    if (rc == 0) {
        uname_field("sysname", identity.sysname);
        uname_field("release", identity.release);
        uname_field("machine", identity.machine);
    }
    emit("<6>rmx618-init: stage=idle; persistent-storage=untouched-by-init\n");
    for (;;) {
        int child_status;
        /* No children are spawned, but reap any orphan adopted by PID 1. */
        while (call6(NR_wait4, -1, (long)&child_status, 1, 0, 0, 0) > 0) {}
        call6(NR_nanosleep, (long)&delay, 0, 0, 0, 0, 0);
    }
}
