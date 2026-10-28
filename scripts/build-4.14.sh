#!/usr/bin/env bash
set -euo pipefail
[[ ${BUILD_EXTERNAL_MODULES:-0} == 0 || ${BUILD_EXTERNAL_MODULES:-0} == 1 ]] || {
  printf '%s\n' 'BUILD_EXTERNAL_MODULES must be 0 or 1' >&2
  exit 2
}
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
WORK="$ROOT/work/kernel414"
ART="$ROOT/artifacts/kernel414"
SRC="$WORK/source"
OUT="$WORK/out"
mkdir -p "$WORK/downloads" "$ART" "$OUT"
finish() {
  code=$?
  trap - EXIT
  if [[ -f "$OUT/.config" ]]; then
    cp "$OUT/.config" "$ART/effective.config"
    diff -u "$ART/input.config" "$ART/effective.config" > "$ART/config.diff" || true
  fi
  printf '%s\n' "$code" > "$ART/exit-code.txt"
  exit "$code"
}
trap finish EXIT
export LC_ALL=C
export TZ=UTC
export KBUILD_BUILD_USER=kernel-builder
export KBUILD_BUILD_HOST=github-actions
export KBUILD_BUILD_TIMESTAMP='Tue Oct 31 02:15:05 UTC 2023'
export KBUILD_BUILD_VERSION=1
cp "$ROOT/sources.lock.json" "$ART/sources.lock.json"
cp "$ROOT/configs/rmx2121cn-stock.config" "$ART/input.config"
printf '%s\n' 'COMPILATION CANDIDATE ONLY. No boot.img is produced. Device boot and vendor compatibility are untested.' > "$ART/STATUS.txt"
python3 - "$ROOT/sources.lock.json" > "$WORK/downloads.tsv" <<'PY'
import json, sys
d=json.load(open(sys.argv[1]))
repo=d['kernel']['repository'].removeprefix('https://github.com/')
print('source\t'+f"https://codeload.github.com/{repo}/tar.gz/{d['kernel']['commit']}\t1")
for name in ('clang','gcc_arm64','gcc_arm'):
    item=d[name]
    sub=item.get('subdirectory','')
    suffix=f'/{sub}' if sub else ''
    print(f"{name}\t{item['repository']}/+archive/{item['commit']}{suffix}.tar.gz\t0")
PY
while IFS=$'\t' read -r name url strip; do
  dest="$WORK/$name"
  archive="$WORK/downloads/$name.tar.gz"
  if [[ ! -f "$dest/.download-complete" ]]; then
    mkdir -p "$dest"
    curl --fail --location --retry 4 --connect-timeout 30 --max-time 1200 "$url" -o "$archive"
    gzip -t "$archive"
    tar -xzf "$archive" --strip-components="$strip" -C "$dest"
    touch "$dest/.download-complete"
  fi
  sha256sum "$archive" >> "$ART/downloads.sha256"
done < "$WORK/downloads.tsv"
mkdir -p "$ART/patches"
for source_patch in "$ROOT"/patches/*.patch; do
  [[ -f "$source_patch" ]] || continue
  patch -d "$SRC" -p1 --fuzz=0 < "$source_patch"
  cp "$source_patch" "$ART/patches/"
done
export PATH="$WORK/clang/bin:$WORK/gcc_arm64/bin:$WORK/gcc_arm/bin:$PATH"
export LD_LIBRARY_PATH="$WORK/clang/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
clang --version | tee "$ART/clang-version.txt"
grep -q '6443078' "$ART/clang-version.txt"
ld.lld --version | tee "$ART/linker-version.txt"
aarch64-linux-androidkernel-as --version > "$ART/assembler-version.txt"
test -f "$SRC/nativefeatures.mk"
test -f "$SRC/OplusKernelEnvConfig.mk"
cp "$SRC/nativefeatures.mk" "$SRC/OplusKernelEnvConfig.mk" "$ART/"
# The vendor Makefile includes these by relative path after changing into O=.
ln -sfn "$SRC/nativefeatures.mk" "$OUT/nativefeatures.mk"
ln -sfn "$SRC/OplusKernelEnvConfig.mk" "$OUT/OplusKernelEnvConfig.mk"
cp "$ROOT/configs/rmx2121cn-stock.config" "$OUT/.config"
args=(O="$OUT" ARCH=arm64 CC=clang LD=ld.lld NM=llvm-nm OBJCOPY=llvm-objcopy
  CROSS_COMPILE=aarch64-linux-androidkernel- CLANG_TRIPLE=aarch64-linux-gnu-)
make -C "$SRC" "${args[@]}" olddefconfig
python3 "$ROOT/scripts/validate-config.py" "$OUT/.config"
# Compile the previously failing subsystem first for quicker diagnostics.
make -C "$SRC" "${args[@]}" -j"${JOBS:-4}" drivers/power/
make -C "$SRC" "${args[@]}" -j"${JOBS:-4}" Image.gz-dtb modules
test -s "$OUT/arch/arm64/boot/Image.gz-dtb"
test -s "$OUT/vmlinux"
test -s "$OUT/Module.symvers"
make -s -C "$SRC" "${args[@]}" kernelrelease > "$ART/kernelrelease.txt"
grep -q '^4\.14\.186' "$ART/kernelrelease.txt"
cp "$OUT/arch/arm64/boot/Image" "$OUT/arch/arm64/boot/Image.gz" "$OUT/arch/arm64/boot/Image.gz-dtb" "$ART/"
cp "$OUT/Module.symvers" "$OUT/System.map" "$ART/"
xz -T2 -c "$OUT/vmlinux" > "$ART/vmlinux.xz"
find "$OUT/arch/arm64/boot/dts" -name '*.dtb' -printf '%P\n' > "$ART/dtbs.list"
if [[ -s "$ART/dtbs.list" ]]; then
  tar -C "$OUT/arch/arm64/boot/dts" -cJf "$ART/dtbs.tar.xz" -T "$ART/dtbs.list"
fi
find "$OUT" -name '*.ko' -printf '%P\n' > "$ART/modules.list"
if [[ -s "$ART/modules.list" ]]; then
  tar -C "$OUT" -cJf "$ART/modules.tar.xz" -T "$ART/modules.list"
fi
file "$ART/Image" "$ART/Image.gz-dtb" > "$ART/image-types.txt"
printf '%s\n' 'COMPILED. This proves source compilation only, not stock binary equivalence or phone boot.' > "$ART/STATUS.txt"
(cd "$ART" && sha256sum Image Image.gz Image.gz-dtb Module.symvers System.map vmlinux.xz > SHA256SUMS)
if [[ ${BUILD_EXTERNAL_MODULES:-0} == 1 ]]; then
  # Keep the complete core evidence even if a later vendor module fails.
  # The helper uses the same OUT key; no private key is exported as an artifact.
  source "$ROOT/scripts/build-external-4.14.sh"
  printf '%s\n' 'COMPILED: kernel and 8 vendor modules, same-build signatures and CRC checks. FPSGO excluded; device compatibility remains untested.' > "$ART/STATUS.txt"
fi
