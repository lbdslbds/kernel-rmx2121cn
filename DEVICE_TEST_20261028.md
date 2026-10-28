# 2026-10-28 RMX2121CN 实机诊断启动

官方源码构建的 **4.14.186 已在项目 20601 的真机上启动到 root ADB，8 个 CPU 在线**。测试使用独立的最小诊断 initramfs，没有启动 Android framework 或加载厂商模块。测试结束后恢复原 boot，完整回读哈希与备份一致；原 Android 17 / SDK 37 正常启动，运行原 `4.14.186+` 内核。

本次结果不代表完整 Android、无线、GPU 或其他外设验证通过，也不代表 6.18 已在手机上启动。

## 输入与实测条件

- 设备项目号 20601，内核平台 MT6885，bootloader 产品 k6889v1_64；8 GiB RAM。
- 实机 MIDR 为 4 × Cortex-A55（0xd05）与 4 × Cortex-A77（0xd0d）。旧设备树将大核写成 Cortex-A75，不能直接沿用该声明。
- 原系统为 Android 17 / SDK 37，当前 kernel 为 4.14.186+；设备已解锁。
- 备份并校验当前 boot、dtbo、vbmeta、recovery；每个备份都核对手机读取前、电脑文件和手机读取后的 SHA256。
- 当前 vendor 的 9 个模块与此前备份逐文件一致。正常 Android 下实际观察到 8 个已加载模块，MET 不在其中。
- 新内核来自 [Actions 40205461469](https://github.com/lbdslbds/kernel-rmx2121cn/actions/runs/40205461469)，源码、工具链与构建提交见 [构建验证报告](reports/kernel414-verified-build.json)。

## 测试方法与结果

首先对原 recovery 和原 boot 各做一次 `fastboot boot` 基线测试。两次均在发送完成后的 Booting 阶段报 USB read error，随后原系统启动并报告 `lk_crash`。这条临时启动路径未能用于本次验证；不能把失败归因于新内核。

随后本地生成两份 boot v2 诊断镜像：共同使用原 boot 的地址、独立 DT table 组件和同一诊断 ramdisk，仅替换 kernel 载荷。该 DTB 组件是 Android DT table 容器，不是可直接当作裸 FDT 的文件。原 dtbo 分区保持原样。

诊断 ramdisk 来自已使用的 recovery（Android 12 init），仅启动 ueventd 与 adbd；移除恢复界面、解密服务、自动 init 扫描内容与持久分区挂载入口。USB 控制器使用实测的 `musb-hdrc`。镜像按 32 MiB 补零，避免仅写入短文件后残留原分区的 AVB footer；每次写入 boot 后进行完整回读校验。

| 检查 | 当前原内核 | 源码重编 4.14.186 |
| --- | --- | --- |
| root ADB / 指定 diagnostic init | 通过 | 通过 |
| CPU online | 0–7 | 0–7 |
| 挂载表 | 20 个虚拟或内存文件系统 | 与原内核诊断一致 |
| 块设备文件系统挂载 | 未观察到 | 未观察到 |
| 已加载外部模块 | 0 | 0 |
| 运行 config | 与原 recovery 相同 | 与构建产物 effective.config 字节一致 |
| 保存的 dmesg 覆盖 | 约 69 秒 | 约 40 秒 |
| 日志中的 panic / Oops / BUG / I/O error | 未发现 | 未发现 |

驱动日志仍有与原内核诊断基线对应的警告调用栈，不能用“无 panic”概括为所有驱动正常。新 config 相对原 config 新增 `CONFIG_F2FS_BD_STAT=y`，并移除 10 条旧的未启用、源码未定义配置记录；没有关闭原有启用项。

## 退出与恢复验收

精简环境没有 `/system/bin/reboot`，且直接请求 recovery 重启时，init 因缺少 recovery fstab 而无法处理 bootloader message。因此实际验证的退出路径是诊断 init 的 `reboot,bootloader` 请求，再由 fastboot 重启到原 recovery。

原厂 PHOENIX 在普通 boot 路径启动了 240 秒看门狗。诊断环境不启动完整 Android，也没有证明看门狗完成条件已满足；本次仅做短时采集，不将其当作可长期驻留环境。

最终恢复原 boot 后：

- boot 完整回读 SHA256 为 `72a42c948b0d570ccdc009fdacc50523e8766dedc251270c44c96233a633e7d2`，与测试前一致。
- dtbo、vbmeta、recovery 的回读哈希在三次 boot 写入后均保持原值。
- 原系统 `sys.boot_completed=1`、Android 17 / SDK 37、内核 `4.14.186+`，充电状态正常。

这里的“仅写 boot”指实验镜像安装范围。recovery 本身会挂载数据分区，bootloader 和内核也可能记录启动状态；未声称整个测试过程没有任何其他持久写入。

## 对 6.18 移植的意义

已建立可验证的 4.14 启动、日志采集和恢复基线。通过普通 boot 路径取得的运行时 FDT 与 recovery FDT 存在协处理器与保留内存差异；后续 6.18 必须以普通启动证据为主要对照，不能只照搬 recovery 树。

6.18 通用 ACK 产物仍缺少 MT6885 / RMX2121 的板级支持。下一步是描述 CPU、PSCI、GIC、定时器和完整保留内存，并建立可靠日志通道，再逐步迁移时钟、PMIC、USB、UFS 和 Android/vendor 接口。参考 [移植计划](PORTING.md)。

外部模块需要与同一轮内核的符号表和签名密钥配套；本次测试内核不能直接搭配另一轮构建签名的模块。`fpsgo_cus` 源码缺口仍未解决，见 [外部模块记录](EXTERNAL_MODULES.md)。

公开报告仅含技术结论和校验值。原始分区、FDT、日志及设备标识保留在本地。
