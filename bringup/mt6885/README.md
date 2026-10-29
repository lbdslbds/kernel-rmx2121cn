# RMX2121CN / MT6885：ACK 6.18 最小设备树草案

这是基于实机证据的离线移植草案，**不是可刷固件，不保证可启动**。本目录没有产生 boot.img，没有向手机写入。草案接入独立 CI，生成的 `.syntax-check.dtb` 仅用于离线结构与绑定检查，结果记录在上级 bringup 说明中。

绑定参考固定为 Android common commit [`846dd6f9b6c61b6aa95e141455ce8f0ea44b29ef`](https://android.googlesource.com/kernel/common/+/846dd6f9b6c61b6aa95e141455ce8f0ea44b29ef/)。完整的逐节点来源、属性映射、来源哈希和绑定链接保存在 [evidence-map.json](evidence-map.json)。

## 文件与验证

- [rmx2121cn-minimal-draft.dts](rmx2121cn-minimal-draft.dts)：CPU、PSCI、GIC、timer、RAM 与 reserved-memory 草案。
- [evidence-map.json](evidence-map.json)：白名单硬件字段、来源哈希与绑定参考。
- [offline-validation.json](offline-validation.json)：本地 DTC 1.8.1 语法和结构回读结果，未做完整 dt-schema / dtbs_check、ACK 编译或实机启动。

后续云端检查已通过通用与匹配 ACK 绑定，范围和未覆盖项见 [2026-10-30 报告](../../reports/bringup618-20261030.json)。这没有改变 `offline-validation.json` 的本地历史检查范围，也不是完整内核 `dtbs_check`。持久日志的源码推导与布局差异见 [RAMOOPS.md](RAMOOPS.md)。

8 个 CPU、30 个固定区和 13 个动态区已逐项回读核对。30 个固定区均位于声明的 RAM 内、彼此不重叠。原始 FDT 和本地提取工具未随公共草案发布，不能将这些校验等同于独立确认全部硬件约定。

## 实机来源和关键决策

使用两个**正常启动**后的诊断 FDT，未使用 recovery 的 FDT：

| 来源 | 路径（相对 kernel-work） | SHA-256 |
| --- | --- | --- |
| 原内核正常启动 | `device/20261028-current-kernel-diagnostic/running-device-tree.dtb` | `2db8d54d4d6cc8b85bf087ae0d183e822851ba226da0aa1cf3dd4198b1416060` |
| 复现 4.14 正常启动 | `device/20261028-compiled-414-diagnostic/running-device-tree.dtb` | `341f7aacb85314a262296cdab26f97fe3c8b913df78cbedc01e56e1befb866b2` |

两份 FDT 均为 298521 字节，所用硬件属性一致。正常启动有 **30 固定 + 13 动态**保留区；recovery 只有 19 个固定区，不能代替这里的正常启动输入。FDT 头部 memreserve 表均为空；保留区来自 `/reserved-memory`。

CPU0–3 的 runtime MIDR 为 `0x412fd050`，CPU4–7 为 `0x411fd0d0`，结合 `/proc/cpuinfo` 的 implementer `0x41`、part `0xd05/0xd0d`，草案写为 Cortex-A55 / Cortex-A77。原 DT 对大核的 Cortex-A75 声明保留在证据映射的 `dt_claim` 中，未照搬到草案。两份 dmesg 的 secondary processor 行独立确认 MIDR 类型，但没有提供 secondary MPIDR；**不能把 MIDR 当成 MPIDR**。CPU `reg` 按两份 FDT 的单 cell 原值 `0x0, 0x100, …, 0x700` 保留，`/cpus #address-cells=1`、`#size-cells=0`。

root compatible 保留实际的 `mediatek,MT6885`，没有杜撰 realme 或上游 SoC compatible。`model = "realme X7 Pro RMX2121CN"` 是为满足根节点约束而编写的设备描述，**不是从原始 FDT 采集的属性**；证据映射将其单列为 `authored_descriptive_properties`，不改变硬件配置。PSCI 为 `arm,psci-1.0` / SMC。GICD/GICR 地址及中断取自实机，使用 `arm,gic-v3`。timer 的四组 PPI 依次为 secure physical / nonsecure physical / virtual / hypervisor physical。旧 DT 与日志均显示 13 MHz，但未发现 CNTFRQ 错误证据，因此不添加 `clock-frequency` 覆盖。RAM 按实机保留为 `[0x40000000, 0x240000000)`，即 8 GiB。

保留区的 `reg` 或 `size/alignment/alloc-ranges`、`no-map/reusable/status` 按原始证据复制。`reserve-memory-sspm_share` 的原始 `status` 是四字节 `okay`、缺少末尾 NUL；草案将其写成合法字符串，证据映射明确记录这个规范化，未声称该属性字节完全一致。厂商保留区 compatible 被省略，目的仅为通用内存保留，不声称已实现 CMA、DMA pool 或协处理器消费者。固定 ACK 的 `drivers/of/of_reserved_mem.c` 支持无 compatible 的通用 `reg` / `size` 保留；这不等于这些保留区的厂商用途已迁移。

## 动态地址证据与启动阻断项

两份 4.14 正常启动日志给出的下列地址一致，行号和各自日志 SHA-256 在证据映射中。它们是既有内核的观察值，**没有被改写成草案的固定 reg**。

| 动态区 | 观察地址 | 大小 | 证据限制 |
| --- | --- | --- | --- |
| ssmr | `0x23fffe000` | `0x1000` | 仅当前 4.14 分配 |
| scp_share | `0x8d000000` | `0x300000` | 仅当前 4.14 分配 |
| ion-carveout-heap | `0xbff64000` | `0xc000` | 仅当前 4.14 分配 |
| zone-movable-cma-memory | `0x20a000000` | `0x35000000` | 后续厂商逻辑还会切分 CMA，未迁移 |
| gps | `0xbd600000` | `0x100000` | 仅当前 4.14 分配 |
| wifi | `0xbc000000` | `0xf20000` | 日志大小取整为 15 MiB，精确大小来自 DT |
| consys | `0xbd100000` | `0x450000` | 仅当前 4.14 分配 |
| adsp_share | `0x76750000` | `0x9c0000` | ADSP MPU 日志，不能单独证明 bootloader 合同 |

`dram_r0_dummy_read`、`dram_r1_dummy_read`、`sspm_share`、`mcupm_share` 的分配地址尚未由这些日志确认。第十三个动态模板 `zmc-default` 在来源中为 disabled，草案保留 disabled。

**在实机启动之前仍需解决：**

1. 6.18 的动态分配顺序、可用区和厂商初始化路径不同，13 个动态节点可能选择不同位置。现有日志不能证明 bootloader 已启动的协处理器、固件 MPU、共享内存与安全世界允许这些地址变化。所有启用的动态区都需核对其地址约定；未知地址不能用推测补齐。
2. 30 个固定区也是一次正常启动时的 mblock 快照。需要确认 bootloader 对新 DT 的 RAM、reserved-memory、chosen 和 initrd 注入行为，及其跨启动/固件版本稳定性。固定区与动态模板是否描述同一厂商用途尚未厘清；通用 allocator 避让固定区不足以证明固件正确。
3. 尚无可用的早期日志通道。UART 有地址和中断证据，但没有确认 stdout-path、pinmux、实际波特率和物理串口接入，因此未加入 UART。ramoops 的基址 `0x4d010000`、总长 `0xe0000`、console `0x40000`、pmsg `0x10000` 有日志证据，但 record/ftrace/mem-type 与重启持久性尚未完整确认，因此只保留 pstore 内存，不加入 ramoops compatible。
4. 时钟、pinctrl、PMIC、电源/看门狗、USB、UFS、IOMMU 等平台支持未实现。当前草案不能据此声称已达到最小启动，更不能声称 Android 可运行。

原始 chosen、bootargs、熵种子、序列号、MAC、IMEI 及全量日志未复制到本目录证据产物。
