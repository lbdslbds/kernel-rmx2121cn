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

设备测试与固件刷写不在当前云端工作流内。所有门槛按实际证据更新，通用 ACK 编译和版本号变化不计作硬件移植完成。

参考：[官方 Android S 源码](https://github.com/realme-kernel-opensource/realme_X7_X7Pro_X7ProExtreme_X7-5G_Q2Pro_V15_V5_Q2_Narzo30pro-5G_7-5G-AndroidS-kernel-source)、[AOSP Android common kernels](https://source.android.com/docs/core/architecture/kernel/android-common)、[Android 17 内核源码](https://android.googlesource.com/kernel/common/+/refs/heads/android17-6.18)。
