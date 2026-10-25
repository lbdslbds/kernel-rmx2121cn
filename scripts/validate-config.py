#!/usr/bin/env python3
"""Reject changes to essential hardware and ABI options before compilation."""
from pathlib import Path
import sys

config = Path(sys.argv[1]).read_text().splitlines()
required = {
    'CONFIG_ARM64=y', 'CONFIG_MACH_MT6885=y',
    'CONFIG_MTK_PLATFORM="mt6885"',
    'CONFIG_ARCH_MTK_PROJECT="k6889v1_64"',
    'CONFIG_ARM64_4K_PAGES=y', 'CONFIG_COMPAT=y',
    'CONFIG_MODULES=y', 'CONFIG_MODVERSIONS=y',
    'CONFIG_SHADOW_CALL_STACK=y', 'CONFIG_LTO_NONE=y',
    'CONFIG_TRUSTONIC_TEE_SUPPORT=y', 'CONFIG_ION=y',
    'CONFIG_BUILD_ARM64_APPENDED_DTB_IMAGE=y',
    'CONFIG_BUILD_ARM64_APPENDED_DTB_IMAGE_NAMES="mediatek/mt6885"',
}
missing = sorted(required - set(config))
if missing:
    raise SystemExit('Essential configuration changed:\n' + '\n'.join(missing))
print('Verified MT6885/k6889v1_64, 4K pages, compat, module ABI, TEE, ION and DTB configuration.')
