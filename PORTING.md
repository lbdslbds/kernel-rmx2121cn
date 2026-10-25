# RMX2121CN 到 Android 17 / Linux 6.18

目标内核为 AOSP ACK `android17-6.18`。`targets/android17.json` 固定本次内核、manifest、Kleaf 与 repo 工具提交；每次云端同步导出 `resolved-manifest.xml`，它才是包含全部工具链依赖的完整快照。

## 当前工程的两条构建

1. **4.14 官方源码复现**：以本机导出的配置编译 realme Android S 源码，验证编译、基础 DTB 和可构建模块。不能由编译成功推断模块 ABI 与原厂完全一致，也不包含既有移植系统的二进制 BPF/bpffs/LPM 补丁。
2. **6.18 通用 ACK 基线**：验证新构建系统及 4 KiB 配置。尚未添加 RMX2121 的设备树和驱动，不是可用的 X7 Pro 内核。

## 已核对的机型输入

- 项目 20601；平台 MT6885，Android 工程 k6889v1_64。
- 基础 DTB 为 `mediatek/mt6885.dtb`。
- 官方提供 `arch/arm64/boot/dts/mediatek/oplus6889_20601.dts`，但它引用生成文件 `<oplus6889_20601/cust.dtsi>`。对应输入是 `drivers/misc/mediatek/dws/mt6885/oplus6889_20601.dws`；公开仓库的已审计目录未找到匹配生成器，完整 DTBO 的生成仍需补齐来源。
- 上述项目 DTBO 依赖不影响当前基础 `mt6885.dts` 的独立编译。不能把基础 DTB 直接当成完整机型 DTBO。
- 4 KiB 内存页、32 位兼容、Trustonic 500、ION、Mali Valhall r32p1 都应作为 vendor 兼容审计的输入。
- 已有启动封装记录为 boot header v2、2048 字节打包页、32 MiB boot 分区。打包页与内核内存页是不同概念。

## 后续验收门槛

| 门槛 | 可证明的结果 |
| --- | --- |
| 官方 4.14 编译 | 产物、配置差异、符号表、工具链版本和日志齐全 |
| 4.14 设备基线 | 经过独立审核的 boot/DTB/DTBO 与模块组合在目标机启动；保存完整日志与恢复结果 |
| 6.18 最小启动 | UART/早期日志、CPU、中断、定时器、必要电源和 initramfs 用户态 |
| 6.18 基础硬件 | USB、UFS、IOMMU、显示触摸、电量、温控、充电、调频与休眠逐项验证 |
| Android 接入 | Binder、SELinux、BPF、cgroups、文件系统加密、TEE 与 vendor ioctl/32 位兼容 |
| 整机功能 | GPU、相机、无线、音频、基带/IMS、指纹、NFC 和持续稳定性测试 |

## 第一阶段的实际实施顺序

1. **冻结硬件输入。** 以完整 20601 DTB/DTBO 和运行时最终设备树交叉核对 UART、CPU 启动方式、GIC、定时器、PMIC、UFS 和 reserved-memory。TEE、基带、SCP 的保留内存不能省略。现有 `cust.dtsi` 缺口需补齐或根据完整硬件证据重新描述，不能猜另一款芯片的寄存器或引脚表。
2. **建立设备专用 ACK 目标。** 在固定 6.18 基线上增加最小 MT6885 DTS、必需驱动和配置，保持 4 KiB 与 32 位兼容。核心启动驱动先内建；用很小的静态 AArch64 initramfs 的 `/init` 挂载 proc/sys/dev、打印阶段标记并提供 shell。先验证内核和用户态交接，再接 Android init 与 vendor。机型内核将是 ACK 的设备分支；不能因源自 GKI 就声称具备 GKI 认证或原厂模块 ABI。
3. **先取得 earlycon，再验证 CPU/IRQ/timer。** UART 参数必须来自实际设备树、寄存器与连线。可先单核定位，随后验证所有预期核心上线。复用 arm64、GICv3、arch timer 与 PSCI 框架，但要核对固件 conduit、CPU enable-method、GIC 区域与中断关系。验收依据是实际进入 `/init`、定时正常、中断计数增长以及完整启动日志。
4. **稳定基础电源与第二条日志通道。** 接通 clock、pinctrl、PMIC IRQ、必要 regulator、USB 控制器与 PHY。Bootloader 留下的时钟状态不等于 clock 驱动完整；需要验证实际管理能力。随后逐步实现温控、调频、休眠，长时间负载测试以有效温度与供电监测为前提。
5. **在 initramfs 中只读验证 UFS。** 根据实际 DMA 拓扑确定所需 IOMMU，验证控制器、PHY、LUN、分区表和已知分区读取校验。不要假定 UFS 必须走多媒体 IOMMU。基础读写路径建立后，再独立验证 inline crypto、fscrypt 与 TEE；初期不以解密 userdata 为启动前提。
6. **逐个接入 Android 消费者。** 先列出设备节点、ioctl、sysfs、32 位结构体、固件和共享缓冲区接口，再启用显示、GPU、音频、相机与通信服务。有源码的驱动需适配并重编译；缺源码者必须有经过验证的替代实现或明确保留功能缺口。

每步都保留源码提交、resolved manifest、最终配置、DT/封装哈希、完整设备日志和通过/失败记录。没有 UART、ramoops/pstore 或可用 USB 日志时，只能继续代码与构建检查；不能据此宣称最小启动成功，也不能靠同时加入更多驱动判断无日志的原因。

## 6.18 可复用框架与机型迁移边界

以下依据本工程固定的 [ACK 提交](https://android.googlesource.com/kernel/common/+/846dd6f9b6c61b6aa95e141455ce8f0ea44b29ef/)，是实施清单，尚未完成移植。

| 子系统 | 已有基础 | MT6885 / 20601 待实现或核对 |
| --- | --- | --- |
| 时钟、电源域 | CCF、MediaTek clock 辅助代码、reset、genpd | PLL/mux/gate 表、父子关系、ID、总线保护与上电时序；将旧 `clk-mt6885-pg.c` 的硬件逻辑接入现有框架 |
| Pinctrl / GPIO / EINT | MediaTek Paris、GPIO、irqdomain | MT6885 pin bank、mux、pull、drive、EINT 和板级状态；已有 MT6893 不等于 MT6885 可直接套用 |
| PMIC | MT6359P regulator、MT6360 MFD 及子设备已有实现 | 实际总线、regmap、IRQ、供电约束与辅助 PMIC；原厂电量算法、VOOC 和充电策略另行迁移 |
| UFS | UFSHCD、SCSI、blk-mq、MediaTek host 框架 | MT6885 PHY/UniPro、quirks、SMC、电源与 crypto；现有 host 匹配表不能证明 MT6885 支持完整 |
| IOMMU / SMI | MediaTek IOMMU、DMA API、MT6893 的 34 位支持可参考 | MT6885 LARB/port、bank、IOVA、保留区、失效与 fault 处理；必须按实际硬件核对 |
| TEE | SMCCC、共享内存等基础设施 | 保留 Trustonic 500 secure-world 协议，迁移 MobiCoreDriver 的 SMC、IRQ、缓存同步、节点及 compat ioctl；OP-TEE 不能自动替换现有固件 |
| ION / DMA-BUF | DMA-BUF、DMA heaps、fence/reservation | 原厂 `/dev/ion` heap ID、私有 flags/cache ioctl、secure buffer；选择适配消费者或实现兼容层，节点软链接不能替代 ABI |
| GPU | Panfrost 有 Valhall JM 匹配支持 | 保留原厂 r32p1 libMali 时，仍需匹配 kbase UAPI、内存/同步和 SoC 电源；改用 Panfrost 需同时评估用户态图形栈，不能直接满足 `/dev/mali0` ABI |
| DVFS / thermal / suspend | cpufreq、OPP、devfreq、thermal、cpuidle | 电压频率表、温度校准、SSPM/SCP、唤醒源和休眠顺序；按功能适配旧 EAS 策略，不能整体覆盖新调度器 |

原厂厂商模块的当前缺口见 [EXTERNAL_MODULES.md](EXTERNAL_MODULES.md)。4.14 的 CRC 匹配也不意味着跨版本 6.18 模块兼容。

框架依据：[pinctrl](https://android.googlesource.com/kernel/common/+/846dd6f9b6c61b6aa95e141455ce8f0ea44b29ef/drivers/pinctrl/mediatek/Kconfig)、[MT6359 regulator](https://android.googlesource.com/kernel/common/+/846dd6f9b6c61b6aa95e141455ce8f0ea44b29ef/drivers/regulator/mt6359-regulator.c)、[UFS](https://android.googlesource.com/kernel/common/+/846dd6f9b6c61b6aa95e141455ce8f0ea44b29ef/drivers/ufs/host/ufs-mediatek.c)、[IOMMU](https://android.googlesource.com/kernel/common/+/846dd6f9b6c61b6aa95e141455ce8f0ea44b29ef/drivers/iommu/mtk_iommu.c)、[Panfrost](https://android.googlesource.com/kernel/common/+/846dd6f9b6c61b6aa95e141455ce8f0ea44b29ef/drivers/gpu/drm/panfrost/panfrost_drv.c)、[ION 迁移](https://source.android.com/docs/core/architecture/kernel/dma-buf-heaps)。

设备测试与固件刷写不在当前云端工作流内。所有门槛按实际证据更新，通用 ACK 编译和版本号变化不计作硬件移植完成。

参考：[官方 Android S 源码](https://github.com/realme-kernel-opensource/realme_X7_X7Pro_X7ProExtreme_X7-5G_Q2Pro_V15_V5_Q2_Narzo30pro-5G_7-5G-AndroidS-kernel-source)、[AOSP Android common kernels](https://source.android.com/docs/core/architecture/kernel/android-common)、[Android 17 内核源码](https://android.googlesource.com/kernel/common/+/refs/heads/android17-6.18)。
