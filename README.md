# realme X7 Pro RMX2121CN kernel work

公开的内核源码构建与移植工程。当前任务是用 GitHub Actions 复现 realme 官方 Android S / Linux 4.14.186 源码编译；后续移植目标为 Android 17 对应的 ACK `android17-6.18`。

## 已完成的验证

[4.14.186 构建 #2](https://github.com/lbdslbds/kernel-rmx2121cn/actions/runs/40205461469) 已成功。产出 `Image`、15,400,133 字节的 `Image.gz-dtb`、基础 DTB、符号表、调试内核与 5 个可构建模块；下载后校验 SHA-256，并确认 gzip 载荷等于单独 `Image`、附加 DTB 等于归档中的 `mediatek/mt6885.dtb`。详见 [验证报告](reports/kernel414-verified-build.json)。

[Android 17 / 6.18.33 通用 ACK 构建](https://github.com/lbdslbds/kernel-rmx2121cn/actions/runs/40206193209) 也已编译成功。下载后确认 ARM64 镜像头与配置均为 4 KiB 页，8 个文件的 SHA-256 全部一致，gzip 载荷等于原始 `Image`；原始镜像 43,600,384 字节。详见 [6.18 验证报告](reports/android17-verified-build.json) 和包含 58 个项目提交的 [完整源码快照](reports/android17-resolved-manifest.xml)。这不是 RMX2121 机型内核，尚未移植或验证真机启动。

Kleaf 默认未启用 stamp，因此该镜像 banner 含 `maybe-dirty` 和 1970 时间戳；这不是检测到源码修改的结论，见 [固定 Kleaf 实现](https://android.googlesource.com/kernel/build/+/38e2a76ecb0257fe1f27b0078cd9169191601ec7/kleaf/impl/stamp.bzl)。实际源码与构建提交以诊断文件和 resolved manifest 为准。本次通用产物未收集重命名后的 `kernel_aarch64_Module.symvers`，脚本已补充后续构建的收集规则；本次报告不包含 6.18 模块 ABI 校验。

4.14 结果不包含设备启动验证或完整项目 DTBO。其内核 banner 的构建时间是脚本固定的源码提交时间，不是云端任务实际执行时间。

`olddefconfig` 没有禁用或改变任何既有启用项；新增 `CONFIG_F2FS_BD_STAT=y`，并移除 10 条原先未启用且该源码没有定义的配置记录。完整差异保存在 Actions 产物 `config.diff`。

现有 vendor 另有 9 个厂商模块尚未在本次构建中复现，涉及无线、GPS、FPSGO 和 MET。离线比较共 1276 项版本化导入：1162 匹配，`conninfra.ko` 的 3 个 `alarm_*` 符号 CRC 不匹配，111 项在本次内核符号表中缺少但能找到其他 vendor 模块中的可能提供者。后者尚未核对提供者 CRC，不能当作 111 个缺失内核 API。正常模块版本校验会拒绝上述 CRC 不匹配的 `conninfra.ko`；不能直接宣称旧模块可复用。见 [完整 CRC 报告](reports/vendor-module-crc-comparison.json) 与 [模块缺口记录](reports/vendor-module-gap.json)。

公开源码包含 8 个外部模块的构建入口；原厂 `fpsgo.ko` 所用的 `fpsgo_cus` 源码尚未找到。后续入口、参数依据及依赖顺序见 [外部模块调查](EXTERNAL_MODULES.md)，尚未执行补编译。目录中有模块文件、静态配置中有加载指令，均不代表本轮实际加载成功。构建还报告 `OPLUS_F2FS_DEBUG` 环境变量未定义；原厂值尚未核实。

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

6.18 选择 **Android 17 ACK 6.18 baseline - NOT for flashing**。完整机型移植步骤、可复用上游框架和验收条件见 [PORTING.md](PORTING.md)。

产物保留 14 天。4.14 产物包含日志、输入/实际配置及差异、源码与工具链引用、下载校验、内核映像、符号和可构建模块；6.18 分别上传 diagnostics 和 generic 两个产物，包含依赖快照、最终配置及通用映像。失败时也上传已经收集到的诊断材料。

下载 6.18 的 diagnostics 与 generic 两个产物后，可运行以下离线检查；`--expected-build-commit` 应取自对应 Actions 运行的 `headSha`，预期 target 应取自该提交的 `targets/android17.json`。

```bash
python3 scripts/verify-android17-artifacts.py DIAGNOSTICS_DIR GENERIC_DIR \
  --run-url https://github.com/lbdslbds/kernel-rmx2121cn/actions/runs/40206193209 \
  --expected-build-commit af6bf3a3af2a1fb77fcc43aa0c5ad2a6ff2beaff \
  --output verification.json
```

检查只验证下载内容、配置、版本与指定构建输入的一致性，不执行内核或模块。脚本不访问 GitHub，运行状态与提交来源须通过 Actions 单独核对。

## 验收顺序

1. 官方 4.14.186 编译及硬件配置核验。
2. 本地审核 boot v2 打包、DTB/DTBO、模块 ABI 和回退材料，再独立安排设备测试。
3. 在 6.18 上迁移最小启动、USB/UFS、显示触摸、电源管理和 Android/vendor 接口。

本仓库仅包含构建输入与技术配置，不包含用户数据、设备序列号、分区备份或签名私钥。上游内核和工具链遵循各自许可证；完整源码引用在锁定文件中。
