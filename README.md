# realme X7 Pro RMX2121CN kernel work

公开的内核源码构建与移植工程。当前任务是用 GitHub Actions 复现 realme 官方 Android S / Linux 4.14.186 源码编译；后续移植目标为 Android 17 对应的 ACK `android17-6.18`。

## 当前边界

- 4.14 工作流使用 RMX2121CN 导出的完整内核配置，项目号 20601，内核平台 MT6885、Android 工程 k6889v1_64。
- 固定官方源码提交及与原厂日志一致的 Android Clang r383902 / build 6443078 和 AOSP GCC binutils。参考 `sources.lock.json`。
- 保留官方发布的 `nativefeatures.mk`、`OplusKernelEnvConfig.mk`，记录 `olddefconfig` 前后的差异。
- `patches/` 记录最小编译修复：目前仅修正 MTK6889 充电头文件中多余的 `kernel-4.14/` 路径组件，不关闭驱动。每次构建都会附带实际应用的补丁。
- 构建产物仅用于验证和后续本地封装，不生成可直接刷写的 boot.img。编译成功不等于原厂二进制一致、不等于手机可启动。
- 现有移植系统曾对预编译 4.14 内核做 BPF/bpffs/LPM 二进制修补，这些补丁不包含在本官方源码基线里。
- 6.18 工作流若构建成功，只证明通用 ACK 构建环境可用；MT6885、RMX2121 的驱动移植尚需单独完成。

## 运行

在 Actions 选择 **Reproduce RMX2121CN kernel 4.14**，手动运行。工作流只在 `workflow_dispatch` 时执行，权限为 `contents: read`。

产物保留 14 天，包括完整日志、输入/实际配置及差异、源码与工具链引用、下载校验、内核映像、符号和可构建模块。失败也上传诊断材料。

## 验收顺序

1. 官方 4.14.186 编译及硬件配置核验。
2. 本地审核 boot v2 打包、DTB/DTBO、模块 ABI 和回退材料，再独立安排设备测试。
3. 在 6.18 上迁移最小启动、USB/UFS、显示触摸、电源管理和 Android/vendor 接口。

本仓库仅包含构建输入与技术配置，不包含用户数据、设备序列号、分区备份或签名私钥。上游内核和工具链遵循各自许可证；完整源码引用在锁定文件中。
