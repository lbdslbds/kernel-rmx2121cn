#!/usr/bin/env bash
# Experimental allnoconfig-derived ACK core. No device access or boot.img output.
set -euo pipefail
umask 022

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
inputs="$project_dir/bringup/minimal-core"
artifact_root="$project_dir/artifacts/minimal-ack"
diagnostics="$artifact_root/diagnostics"
kernel_outputs="$artifact_root/kernel"
if [[ -e "$diagnostics/build.log" || -e "$kernel_outputs/build-manifest.json" ]]; then
  printf '%s\n' 'Refusing to overwrite an earlier build; use a fresh checkout/output directory.' >&2
  exit 1
fi
mkdir -p "$diagnostics" "$kernel_outputs"
exec > >(tee "$diagnostics/build.log") 2>&1

finish() {
  local result=$?
  trap - EXIT
  printf '%s\n' "$result" > "$diagnostics/exit-code.txt"
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    {
      printf '### Minimal ACK ARM64 execution core\n\n'
      printf 'Build exit code: `%s`. **Not an Android kernel or RMX2121 flash image.**\n\n' "$result"
      printf 'This build does not test phone hardware, DT overlays, shared-memory contracts or persistent-log recovery.\n'
    } >> "$GITHUB_STEP_SUMMARY"
  fi
  exit "$result"
}
trap finish EXIT

cp "$inputs/minimal.config" "$diagnostics/requested.config"
cp "$inputs/target.json" "$diagnostics/target.json"
git -C "$project_dir" rev-parse HEAD > "$diagnostics/build-definitions-commit.txt"
uname -a > "$diagnostics/host.txt"
df -h >> "$diagnostics/host.txt"
dpkg-query -W -f='${Package}\t${Version}\n' > "$diagnostics/packages.txt"
for tool in clang ld.lld llvm-ar llvm-nm llvm-objcopy llvm-objdump llvm-readelf make; do
  command -v "$tool"
  "$tool" --version
  sha256sum "$(command -v "$tool")"
done > "$diagnostics/toolchain.txt"

source_revision="$(python3 - "$inputs/target.json" <<'PY'
import json, sys
t = json.load(open(sys.argv[1], encoding='utf-8'))
assert t['source_url'] == 'https://android.googlesource.com/kernel/common'
assert t['source_commit'] == '846dd6f9b6c61b6aa95e141455ce8f0ea44b29ef'
assert t['not_for_flashing'] and not t['phone_test_allowed_by_this_workflow']
assert t['architecture'] == 'arm64' and t['page_size_bytes'] == 4096
print(t['source_commit'])
PY
)"
task_temp="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/minimal-ack.XXXXXXXX")"
source_dir="$task_temp/common"
build_dir="$task_temp/out"
mkdir -p "$source_dir" "$build_dir"
git -C "$source_dir" init
git -C "$source_dir" remote add origin https://android.googlesource.com/kernel/common
git -C "$source_dir" -c protocol.version=2 fetch --no-tags --depth=1 origin "$source_revision"
git -C "$source_dir" checkout --detach FETCH_HEAD
[[ "$(git -C "$source_dir" rev-parse HEAD)" == "$source_revision" ]]
git -C "$source_dir" show -s --format=fuller HEAD > "$diagnostics/source-commit.txt"
git -C "$source_dir" rev-parse HEAD^{tree} > "$diagnostics/source-tree.txt"
git -C "$source_dir" status --porcelain > "$diagnostics/source-status-before.txt"
[[ ! -s "$diagnostics/source-status-before.txt" ]]
cp "$source_dir/Makefile" "$diagnostics/kernel-Makefile"
cp "$source_dir/scripts/min-tool-version.sh" "$diagnostics/min-tool-version.sh"
python3 - "$source_dir/Makefile" <<'PY'
import pathlib, re, sys
text = pathlib.Path(sys.argv[1]).read_text()
version = '.'.join(re.search(r'^' + key + r'\s*=\s*(\d+)\s*$', text, re.M).group(1)
                   for key in ('VERSION', 'PATCHLEVEL', 'SUBLEVEL'))
assert version == '6.18.33', version
PY

export ARCH=arm64 LLVM=1 LLVM_IAS=1
export KBUILD_BUILD_USER=kernel-lab KBUILD_BUILD_HOST=github-actions KBUILD_BUILD_VERSION=1
export SOURCE_DATE_EPOCH="$(git -C "$source_dir" show -s --format=%ct HEAD)"
export KBUILD_BUILD_TIMESTAMP="$(git -C "$source_dir" show -s --format=%cD HEAD)"
# Explicit empty make-variable suppresses an unrequested SCM '+' suffix while
# retaining CONFIG_LOCALVERSION from the input fragment.
export LOCALVERSION=''
make_args=(-C "$source_dir" O="$build_dir" ARCH=arm64 LLVM=1 LLVM_IAS=1)
make "${make_args[@]}" defconfig
cp "$build_dir/.config" "$diagnostics/cpu-baseline-defconfig.config"
python3 "$inputs/preserve-cpu-defaults.py" --source "$source_dir" \
  --baseline "$diagnostics/cpu-baseline-defconfig.config" --requested "$inputs/minimal.config" \
  --output-config "$diagnostics/effective-requested.config" --output-report "$diagnostics/cpu-default-policy.json"
make "${make_args[@]}" KCONFIG_ALLCONFIG="$diagnostics/effective-requested.config" allnoconfig
cp "$build_dir/.config" "$diagnostics/allnoconfig.config"
make "${make_args[@]}" olddefconfig
cp "$build_dir/.config" "$diagnostics/final.config"
python3 "$inputs/verify-config.py" --source "$source_dir" \
  --requested "$diagnostics/effective-requested.config" --config "$build_dir/.config" \
  --cpu-policy "$diagnostics/cpu-default-policy.json" --cpu-baseline "$diagnostics/cpu-baseline-defconfig.config" \
  --output "$diagnostics/config-verification.json"
diff -u "$diagnostics/allnoconfig.config" "$diagnostics/final.config" > "$diagnostics/olddefconfig.diff" || [[ $? -eq 1 ]]

make "${make_args[@]}" -j"${JOBS:-4}" Image Image.gz
# Make may refresh configuration; enforce the same requirements after compilation.
cmp "$build_dir/.config" "$diagnostics/final.config"
python3 "$inputs/verify-config.py" --source "$source_dir" \
  --requested "$diagnostics/effective-requested.config" --config "$build_dir/.config" \
  --cpu-policy "$diagnostics/cpu-default-policy.json" --cpu-baseline "$diagnostics/cpu-baseline-defconfig.config" \
  --output "$diagnostics/config-verification-after-build.json"
cp "$build_dir/arch/arm64/boot/Image" "$kernel_outputs/Image"
cp "$build_dir/arch/arm64/boot/Image.gz" "$kernel_outputs/Image.gz"
cp "$build_dir/System.map" "$kernel_outputs/System.map"
cp "$build_dir/.config" "$kernel_outputs/final.config"
cp "$build_dir/include/config/kernel.release" "$kernel_outputs/kernelrelease.txt"
git -C "$source_dir" status --porcelain > "$diagnostics/source-status-after.txt"
[[ ! -s "$diagnostics/source-status-after.txt" ]]
cp "$inputs/target.json" "$kernel_outputs/target.json"
cat > "$kernel_outputs/DO-NOT-FLASH.txt" <<'EOF'
Experimental minimal ACK ARM64 core for CI/QEMU only.
NOT an Android-compatible kernel. NOT a phone boot image. DO NOT FLASH.
No block, network, USB, MMC/UFS or loadable-module support is intended.
Generic architecture/OF/reserved-memory frameworks remain active.
No phone testing is performed or authorized by this workflow.
EOF
python3 - "$kernel_outputs" "$diagnostics" <<'PY'
import gzip, hashlib, json, pathlib, re, struct, sys
out, diag = map(pathlib.Path, sys.argv[1:])
target = json.loads((out / 'target.json').read_text())
release = (out / 'kernelrelease.txt').read_text().strip()
assert release == target['expected_kernel_release'], release
image = (out / 'Image').read_bytes()
assert len(image) >= 64 and image[56:60] == b'ARMd'
flags = struct.unpack_from('<Q', image, 24)[0]
assert not (flags & 1) and ((flags >> 1) & 3) == 1
assert gzip.decompress((out / 'Image.gz').read_bytes()) == image
assert re.search(rb'Linux version ' + re.escape(release.encode()) + rb'\s', image)
files = {p.name: {'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
         for p in sorted(out.iterdir()) if p.is_file()}
manifest = {'schema_version': 1, 'not_for_flashing': True, 'phone_boot_tested': False,
            'artifact_kind': 'experimental_minimal_ack_core', 'source_commit': target['source_commit'],
            'build_definitions_commit': (diag / 'build-definitions-commit.txt').read_text().strip(),
            'kernel_release': release, 'page_size_bytes': 4096,
            'final_config_sha256': files['final.config']['sha256'], 'files': files,
            'gzip_matches_Image': True, 'runtime_tested_by_builder': False,
            'toolchain_policy': target['toolchain_policy']}
(out / 'build-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
with (out / 'SHA256SUMS').open('w') as stream:
    for path in sorted(out.iterdir()):
        if path.is_file() and path.name != 'SHA256SUMS':
            stream.write(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name + '\n')
print(json.dumps({'status': 'minimal_core_built', 'release': release, 'image': files['Image'],
                  'phone_boot_tested': False, 'not_for_flashing': True}))
PY
