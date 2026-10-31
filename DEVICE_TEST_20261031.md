# 2026-10-31：run5 4.14 诊断启动与持久日志

同轮编译八个厂商模块的 [run40710293358](https://github.com/lbdslbds/kernel-rmx2121cn/actions/runs/40710293358) 内核已在 RMX2121CN 上进入最小 root ADB 环境，8 个 CPU 在线。唯一内核日志标记经过 **诊断内核→bootloader→原 recovery** 后成功取回。测试完成后恢复原 boot 并读回核验，原 Android 17 / SDK 37 / `4.14.186+` 已正常完成启动。

这是新的 4.14 内核诊断验证，不是 6.18 真机启动，也没有加载八个外部模块或验证完整 Android、外设与长期稳定性。[完整去敏报告](reports/device-diagnostic-20261031.json) 包含校验和、运行范围及独立复核结果。

## 镜像与运行结果

- 使用已实测诊断 ramdisk 的相同字节，保留原 boot v2 地址、DT table 和原 dtbo 分区，替换为 run5 的 `Image.gz`。
- 候选镜像为 30,744,576 字节，SHA-256 `c8d7ba1298678c285c934af21f8c9ecbd0042b6d6dc804ac6b8c03a895f1d28f`。
- 写入前补零至完整 32 MiB，SHA-256 `6f211fe2486ba016f97d859da6027e2dca327e391f2ed50952c0fd7f191ac914`，写后完整读回一致。
- 运行 config 与 run5 `effective.config` 逐字节一致；外部模块数为 0。20 项挂载均为 RAM/虚拟文件系统，设备 major 均为 0。
- 诊断日志覆盖内核 uptime 0–19.81 秒，恢复后保存的 console 延续到 22.04 秒，包含退出到 bootloader 的记录。
- 日志仍有 6 条 WARNING、7 个 Call trace，与此前源码 4.14 诊断的对应项一致。未发现本次检索的 panic、Oops、BUG、无法处理的内核异常或 I/O error；短时运行不等于无缺陷。

## 持久日志证据

标记在测试前材料中不存在，注入后在运行中的 dmesg 出现一次；恢复环境保存的 `console-ramoops` 中保留相同标记、时间戳和进程记录。文件为 262,132 字节，本地与手机读取前后 SHA-256 一致。

recovery 实际读出的参数为：base `0x4d010000`、total `0xe0000`、console `0x40000`、pmsg `0x10000`、record/ftrace 各 `0x1000`、mem_type 0、ECC 0、dump_oops 1。这补充了此前只由源码推导的参数证据，但尚未直接测量每个物理子区的实际映射。

旧 recovery 的 ADB shell 会转换换行，尽管主机请求 `-T`。本次 pstore 证据改用原始字节传输，附加长度及前后远端哈希验证；首次失败的读取尝试没有作为有效证据使用。原始日志仅保留在本地。

成功范围仅为本次温重启链路；未证明 ACK 6.18、冷启动、看门狗复位或 panic 后的日志留存。原厂与 ACK 的布局差异仍须解决，见 [ramoops 调查](bringup/mt6885/RAMOOPS.md)。

## 恢复与下一项阻断

原 boot 的恢复 SHA-256 为 `72a42c948b0d570ccdc009fdacc50523e8766dedc251270c44c96233a633e7d2`，完整读回一致；dtbo、vbmeta、recovery 在写入前后均保持原哈希。恢复后的 Android 检查时间为 `2026-10-31T03:28:27.421227+00:00`。

离线分析确认当前启动路径选择旧 DTBO index 5，其需要的 58 个外部符号在最小 ACK 草案中全部缺失。原厂 base 的 overlay 应用成功，最小 ACK 草案应用失败；必须先解决这个接入问题，不能把“通用 schema 通过”理解为当前 bootloader 接受该树。详见 [启动器与 DTBO 证据](reports/bootloader-dt-20261031.json)。本次没有改动 DTBO 分区，也没有测试带新设备树的启动镜像。
