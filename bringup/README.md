# ACK 6.18 离线适配检查

这里把 RMX2121 的硬件证据和最小诊断程序接入云端检查，尚未实现 MT6885 平台驱动，也不生成手机 boot.img。

2026-10-30，[运行 40907518385](https://github.com/lbdslbds/kernel-rmx2121cn/actions/runs/40907518385) 的两项检查均通过。设备树 DTC / dt-validate 没有诊断；7 个相关绑定均已载入，`mediatek,MT6885` 仍无机型绑定。完整绑定集中的无关 `firmware/gunyah-cma-mem.yaml` 因格式错误被工具忽略，此限制已记录。诊断程序在 ACK 6.18.33 / QEMU virt 中实际进入 PID 1 并到达空闲阶段。完整范围和下载核验见 [报告](../reports/bringup618-20261030.json)。

手动运行 **ACK 6.18 bring-up checks - NO phone image**，会并行执行两项检查：

1. **设备树**：DTC 编译已审查的 MT6885 草案，使用固定 ACK 提交 `846dd6f9b6c61b6aa95e141455ce8f0ea44b29ef` 的绑定及 dtschema 2026.9 验证。校验器即便返回零，只要输出诊断也判失败。`-m` 的未匹配 compatible 另存日志；实际 `mediatek,MT6885` 尚无机型绑定覆盖，不能将通用规则通过称为完整平台支持或完整内核 `dtbs_check`。
2. **诊断程序**：Clang/LLD 编译无 libc 的 ARM64 `/init` 两次并检查一致性，封装确定性 newc/gzip。下载已核验的 [ACK 6.18.33 构建](https://github.com/lbdslbds/kernel-rmx2121cn/actions/runs/40206193209)，同时核对 Actions 提交、构建证据和预期 Image 哈希，在 QEMU `virt` 中运行。要求 PID 1、proc/sysfs 挂载、uname、静态设备节点回退和空闲阶段标识齐全，并在到达后继续观察五秒，无已检查的 panic/Oops/BUG。

模拟器没有连接手机、块存储或网卡，不传入 MT6885 草案 DTB。它证明的范围仅为这一通用内核下的诊断程序执行；QEMU 的 UART、PSCI、内存和中断控制器不能代表手机硬件。

Actions 产物保存 14 天。诊断程序构建记录编译器/链接器版本与哈希，设备树检查记录工具版本、Python 包快照和绑定归档哈希。Ubuntu 软件包与 Python 传递依赖未完全锁定；重复运行的工具版本不保证相同。旧 ACK 构建产物过期后，本工作流会明确失败，须重新构建并重新核验其来源与哈希，不自动换成最新未验证内核。

当前仍需解决：固件共享内存与动态保留区的地址约定、bootloader 对新 DT 的修改、可用早期日志通道、平台电源/看门狗，以及 USB/UFS 等驱动。详细证据与限制在 [MT6885 草案](mt6885/README.md)；[ramoops 布局调查](mt6885/RAMOOPS.md) 已识别原厂与 ACK 的关键差异。
