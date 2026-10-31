# 4.14 与 6.18 持久日志布局

2026-10-30 的离线调查说明：不能直接把原厂 ramoops 参数视作 6.18 与 recovery 的兼容日志通道。这里是下一阶段的设计依据，尚未添加 ramoops 设备树节点或生成测试镜像。

2026-10-31 已补充 [受控真机证据](../../DEVICE_TEST_20261031.md)：run5 的 4.14 诊断环境写入唯一 kmsg 标记，经 bootloader 回到原 recovery 后成功取回。recovery 的 record/ftrace/console/pmsg/base/total/mem_type/ECC 实际参数与下述推导值一致。这个结果仍不证明 6.18 布局兼容、强制复位或更早阶段的日志可用。

固定 [OEM 4.14 ram.c](https://github.com/realme-kernel-opensource/realme_X7_X7Pro_X7ProExtreme_X7-5G_Q2Pro_V15_V5_Q2_Narzo30pro-5G_7-5G-AndroidS-kernel-source/blob/f0c2afc4d8aa699bacae223bf7506d6912e08da6/fs/pstore/ram.c) 与 [ACK 6.18 ram.c](https://android.googlesource.com/kernel/common/+/846dd6f9b6c61b6aa95e141455ce8f0ea44b29ef/fs/pstore/ram.c) 的分区顺序不同。OEM 多一个 `bconsole`，并把 ftrace 放在 pmsg 前面。

实机 probe 日志确认总区间为 `0x4d010000 / 0xe0000`，console 为 `0x40000`，pmsg 为 `0x10000`。根据固定 OEM 源码默认值和捕获的参数路径，复现内核的 record/ftrace 大小推导为各 `0x1000`、mem_type 为 0、ECC 为 0；这不等于直接读回原 Android/recovery 二进制中的全部驱动变量。

在上述条件下，由源码算得：

| 区域 | OEM 4.14 起址 / 大小 | ACK 6.18 沿用参数时起址 / 大小 |
| --- | --- | --- |
| dmesg records | `0x4d010000 / 0x4f000` | `0x4d010000 / 0x8f000` |
| console | `0x4d05f000 / 0x40000` | `0x4d09f000 / 0x40000` |
| bconsole | `0x4d09f000 / 0x40000` | 无 |
| ftrace | `0x4d0df000 / 0x1000` | `0x4d0ef000 / 0x1000` |
| pmsg | `0x4d0e0000 / 0x10000` | `0x4d0df000 / 0x10000` |

ACK console 恰落在旧 bconsole 区。固定 OEM reader 可能把它导出成 `console-ramoops-2`，但尚未验证当前 recovery 的实际兼容性。两版 console 环形缓冲区均使用 DBGC 签名和 12 字节头；格式相近不能代替跨内核取回测试。

已有证据支持一次原 Android→recovery 温重启后的日志取回：保存的 console 包含前次约 616 秒启动的 reboot-to-recovery 标识，而 recovery 自身日志从零开始。两次最小诊断环境未挂载 pstore，因此其空目录不能证明日志丢失。6.18→recovery、强制复位、看门狗复位和断电后的留存均未验证。

候选方案是只将 ACK console 对齐旧 console 子区，显式关闭 record/ftrace/pmsg，并保护外围全部保留内存。实施前仍须确认 recovery 实际区间、bootloader 对新 DT 的修改及参数/DT 是否会重复注册 ramoops。ACK DT 路径的 record/ftrace 默认值为零，与模块参数路径不同，必须显式定义布局。

已构建的 ACK 配置包含 `CONFIG_PSTORE=y`、`CONFIG_PSTORE_RAM=y`、`CONFIG_PSTORE_CONSOLE=y`。ramoops 仍需要初始化成功才能记录日志，不能代替在更早阶段失效时的物理串口。
