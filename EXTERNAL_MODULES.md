# RMX2121CN 4.14 外部模块补编译调查

记录日期：2026-10-25。

**状态：仅完成源码、构建入口及原厂模块的离线检查。尚未执行本文所列外部模块的重新编译，也未加载模块、刷写手机或验证设备启动。** 内核主体的编译成功不能替代这些验证。

源码基线为 [realme 官方 Android S 仓库固定提交](https://github.com/realme-kernel-opensource/realme_X7_X7Pro_X7ProExtreme_X7-5G_Q2Pro_V15_V5_Q2_Narzo30pro-5G_7-5G-AndroidS-kernel-source/tree/f0c2afc4d8aa699bacae223bf7506d6912e08da6)，提交号 `f0c2afc4d8aa699bacae223bf7506d6912e08da6`。工具链版本见 [sources.lock.json](sources.lock.json)。内核主体成功编译证据来自 [Actions 运行 40205461469](https://github.com/lbdslbds/kernel-rmx2121cn/actions/runs/40205461469)。

## 旧模块不能直接复用的已知原因

[vendor-module-crc-comparison.json](reports/vendor-module-crc-comparison.json) 对备份 vendor 目录的 9 个模块进行了静态检查。共比较 1276 个带版本的导入项，其中 1162 项与新内核导出匹配，3 项 CRC 不匹配，111 项未出现在新内核的 `Module.symvers` 中。111 项均找到可能的 vendor 模块提供者，但提供者 CRC 尚未在该报告中验证；不能将它们直接解释为运行时必然缺失。

三项不匹配全部位于原厂 `conninfra.ko`：

| 符号 | 原厂模块要求的 CRC | 新内核导出的 CRC |
| --- | --- | --- |
| `alarm_cancel` | `0x93a4d852` | `0x6f13fc92` |
| `alarm_init` | `0x1a98ba12` | `0x7fc40798` |
| `alarm_start_relative` | `0x64223914` | `0x3f7cc77d` |

本次内核的 `kernelrelease` 为 `4.14.186`；原厂模块的 `vermagic` 为 `4.14.186+ SMP preempt mod_unload modversions aarch64`。版本后缀差异本身不能证明兼容或不兼容；本次确认的阻碍是上述 3 项 CRC 不匹配。仅修改版本字符串或绕过版本检查不能解决接口差异。

即便其余 CRC 匹配，也仍需验证模块依赖、签名要求、固件接口、设备初始化及运行行为。该静态报告没有证明任何模块已成功加载。

初步追查将范围缩小到 `alarmtimer.h` 中 `struct alarm` 及相关类型声明：公开头文件没有 Android vendor data 字段；相关 RTC、hrtimer 配置没有变化；`met.ko` 的四个 `hrtimer_*` 导入与本次内核 CRC 均匹配。这些证据尚不能确定原厂头文件具体差在哪里。原模块不含 DWARF/BTF，下一步应以 `V=1 KBUILD_SYMTYPES=1` 重建 `kernel/time/alarmtimer.o`，保留预处理类型图，并与原厂对应头文件或调试内核比对；不能虚构字段或改写 CRC 来消除检查错误。

## 已找到的 8 个外部模块入口

以下路径均相对于源码根的 `drivers/kernel_modules/`。参数来自相应 `Android.mk`、`Makefile` 或 `Kbuild`；“先编译依赖”同时与备份原厂模块的 `depends=` 字段核对过。

| 目标模块 | `M=` 子路径 | 平台参数或选择条件 | 先编译依赖 |
| --- | --- | --- | --- |
| `conninfra.ko` | `connectivity/conninfra` | `TARGET_BUILD_VARIANT=user`；现有配置含 `CONFIG_MTK_COMBO_CHIP_CONSYS_6885=y` | 无 |
| `connfem.ko` | `connectivity/connfem` | `TARGET_BUILD_VARIANT=user`；使用公开的 customer 配置 | 无 |
| `bt_drv_6885.ko` | `connectivity/bt/mt66xx/btif` | `BT_PLATFORM=6885` | conninfra、connfem |
| `fmradio_drv_connac2x.ko` | `connectivity/fmradio` | `CFG_FM_PLAT=connac2x CFG_BUILD_CONNAC2=true CFG_FM_CHIP=mt6635 CONFIG_FM_USER_LOAD=1` | conninfra |
| `gps_drv.ko` | `connectivity/gps` | 保持 `GPS_CHIP_ID` 非 `common`；由 `CONFIG_MACH_MT6885=y` 选中 `data_link/plat/v010` | conninfra |
| `wmt_chrdev_wifi.ko` | `connectivity/wlan/adaptor` | `MODULE_NAME=wmt_chrdev_wifi CONNAC_VER=2_0` | conninfra |
| `wlan_drv_gen4m.ko` | `connectivity/wlan/core/gen4m` | 见下方 Wi-Fi 参数说明 | conninfra、connfem、wmt_chrdev_wifi |
| `met.ko` | `met_drv/4.14` | 从现有配置读取 MT6885、`CONFIG_MODULES=y`、FTRACE 和 TRACING | 无 |

这些入口及相关平台目录已找到，但未逐项编译，因此不能承诺没有其他源码、头文件或导出符号问题。MET 构建会根据平台文件和内核接口决定功能，应保留日志并检查实际启用项，不能把自动退化的默认模块视为完整复现。

## 准备相同的内核编译环境

补编译需要固定源码、已经记录的构建修补、相同工具链及 `effective.config`。如果重新创建输出目录，应执行 `modules_prepare`，并恢复成功构建产物中的 `Module.symvers`。`modules_prepare` 本身不会生成完整的模块 CRC 表。

应继续保留现有构建脚本为独立输出目录准备的 `OplusKernelEnvConfig.mk` 与 `nativefeatures.mk` 链接；否则相对 `-include` 会静默漏读厂商编译宏。生成的 `kernelrelease` 必须与此次目标内核一致。

多个无线模块硬编码了 `$(TOP)/vendor/mediatek/kernel_modules/connectivity/...` 头文件路径。可在隔离的构建工作目录恢复这个布局，避免逐个修改厂商文件：

```bash
# SRC、OUT、ANDROID_TOP 均为本次隔离构建目录的绝对路径。
MOD="$SRC/drivers/kernel_modules"
mkdir -p "$ANDROID_TOP/vendor/mediatek"
ln -s "$MOD" "$ANDROID_TOP/vendor/mediatek/kernel_modules"
```

上述示例要求目标链接尚不存在；重跑时应先核对已有链接的指向。通用的内核 Kbuild 调用形态为：

```bash
make -C "$SRC" O="$OUT" \
  ARCH=arm64 CC=clang LD=ld.lld NM=llvm-nm OBJCOPY=llvm-objcopy \
  CROSS_COMPILE=aarch64-linux-androidkernel- CLANG_TRIPLE=aarch64-linux-gnu- \
  M="$MOD/connectivity/conninfra" \
  KERNEL_OUT="$OUT" TOP="$ANDROID_TOP" TARGET_BUILD_VARIANT=user \
  modules
```

这只是尚未执行的命令模板，PATH、Clang 的 `lib64`、固定构建环境和 `Module.symvers` 必须提前准备好。不同模块替换 `M=` 并增加表中的参数。

依赖模块应先编译，并通过 `KBUILD_EXTRA_SYMBOLS` 将它们**本次生成**的 `Module.symvers` 传给使用者。例如 Bluetooth 需要 conninfra、connfem 两份符号表；gen4m 还需要 adaptor 的符号表。不得用旧模块中提取的 CRC 填充新符号表来掩盖不匹配。建议顺序为：

1. conninfra、connfem；MET 可独立构建。
2. Bluetooth、GPS、FM、Wi-Fi adaptor。
3. gen4m Wi-Fi 驱动。

FM 目录同时有用于多芯片构建的外层 Makefile 和实际 Kbuild。表中方案是从内核根直接使用 `M=.../fmradio` 进入 Kbuild，并明确指定 connac2x 参数。GPS 应使用 `M=.../connectivity/gps`；改用 `gps/data_link` 的另一入口会生成 `gps_drv_dl_*` 等名称，不能直接当成原厂 `gps_drv.ko` 的复现结果。

## Wi-Fi 参数的证据与待确认项

gen4m 首轮补编译可采用以下有源码及原厂二进制佐证的参数：

```text
MODULE_NAME=wlan_drv_gen4m
WLAN_CHIP_ID=6885
MTK_COMBO_CHIP=CONNAC2X2_SOC3_0
CONFIG_MTK_COMBO_WIFI_HIF=axi
```

备份 `wlan_drv_gen4m.ko` 中包含 `mt6885DmashdlInit`、`rMT6885DmashdlCfg`、`soc3_0_*`、`hifAxiProbe`、`mtk_axi_probe`，并声明依赖 `conninfra,wmt_chrdev_wifi,connfem`。源码中对应的 MT6885 平台代码、SOC3_0 芯片代码与 AXI 接口代码均存在。不能采用 Makefile 在缺参数时回落到的 MT6632 默认值。

官方 `build_wlan_drv.mk` 还传入 `MTK_WLAN_SERVICE=yes`，公开树有相应 `wlan_service/`。但原厂产品的 `MTK_ANDROID_EMI`、`WIFI_IP_SET`、`MTK_ANDROID_WMT`、SMARTGEAR 等选项尚未完整还原。应记录首轮选值及其来源，并在编译后核对符号与功能；上述核心参数不等于已精确复现原厂完整编译命令。

## `fpsgo.ko` 的源码缺口

备份原厂 `fpsgo.ko` 的字符串明确包含：

```text
vendor/mediatek/kernel_modules/fpsgo_cus/src/fpsgo_main.c
```

同时包含 `xgf_ko_init`、`xgf_est_runtime` 等模块算法符号。当前核查的官方固定提交中未找到 `fpsgo_cus` 目录及对应的独立模块构建入口。

公开树中的 `drivers/misc/mediatek/performance/fpsgo_v3` 是另一层内核内建代码：其 Makefile 使用 `obj-y`，父级由 `CONFIG_MTK_FPSGO_V3=y` 接入，本次 `Module.symvers` 也将相关接口记为 `vmlinux` 导出。这不能证明独立 `fpsgo.ko` 的算法已被替代，也不能直接通过普通 `M=... modules` 命令生成等价模块。

因此当前范围是“8 个有外部构建入口的模块可开展补编译调查，另有 1 个独立 FPSGO 模块的匹配源码待补齐”。即使八个全部编译通过，仍不能据此宣称九个原厂模块已复现或整机已经可启动。
