#!/usr/bin/env bash
# Generic ACK toolchain/build baseline. This script does not port MT6885/RMX2121,
# generate an X7 Pro boot image, flash a device, or publish a release.
set -euo pipefail
umask 022

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
target_file="$project_dir/targets/android17.json"
diagnostics="$project_dir/artifacts/android17/diagnostics"
baseline="$project_dir/artifacts/android17/generic-not-for-flashing"
mkdir -p "$diagnostics" "$baseline"
exec > >(tee "$diagnostics/build.log") 2>&1

finish() {
  local result=$?
  trap - EXIT
  printf '%s\n' "$result" > "$diagnostics/exit-code.txt"
  if [[ -n "${GITHUB_STEP_SUMMARY:-}" ]]; then
    {
      printf '### Generic Android 17 ACK 6.18 build baseline\n\n'
      printf 'Exit code: `%s`. This is **not an RMX2121/X7 Pro kernel port**.\n\n' "$result"
      printf 'Do not flash these artifacts. No device support or phone boot test is claimed.\n\n'
      printf 'The diagnostics artifact contains the exact resolved source manifest when synchronization completed.\n'
    } >> "$GITHUB_STEP_SUMMARY"
  fi
  exit "$result"
}
trap finish EXIT

printf '%s\n' 'GENERIC ACK BASELINE ONLY: NOT FOR FLASHING ON REALME X7 PRO.'
cp "$target_file" "$diagnostics/target.json"
uname -a > "$diagnostics/host.txt"
df -h >> "$diagnostics/host.txt"
git -C "$project_dir" rev-parse HEAD > "$diagnostics/build-definitions-commit.txt"

settings_text="$(python3 - "$target_file" <<'PY'
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    target = json.load(stream)
assert target["schema_version"] == 1
assert target["flashable_on_rmx2121"] is False
assert target["kernel"]["page_size"] == "4k"
assert target["kernel"]["version_prefix"] == "6.18"
assert target["build"]["target"] == "//common:kernel_aarch64_dist"
assert target["manifest"]["url"] == "https://android.googlesource.com/kernel/manifest"
assert target["repo"]["url"] == "https://gerrit.googlesource.com/git-repo"
for key in ("kernel", "manifest", "build", "repo"):
    assert re.fullmatch(r"[0-9a-f]{40}", target[key]["revision"]), key
for value in (
    target["kernel"]["revision"], target["manifest"]["revision"],
    target["build"]["revision"], target["repo"]["revision"],
):
    print(value)
PY
)"
mapfile -t settings <<< "$settings_text"
[[ ${#settings[@]} -eq 4 ]]
kernel_revision="${settings[0]}"
manifest_revision="${settings[1]}"
build_revision="${settings[2]}"
repo_revision="${settings[3]}"

task_temp="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/android17-ack.XXXXXXXX")"
workspace="$task_temp/workspace"
dist="$task_temp/dist"
mkdir -p "$workspace" "$dist" "$task_temp/bin"

# Keep Git configuration local to this build's temporary directory.
export GIT_CONFIG_GLOBAL="$task_temp/gitconfig"
git config --file "$GIT_CONFIG_GLOBAL" user.name 'Kernel baseline builder'
git config --file "$GIT_CONFIG_GLOBAL" user.email 'kernel-build@example.invalid'

# Download the launcher from an immutable official git-repo revision. Pin the
# full repo implementation to that same revision during repo init as well.
curl --fail --show-error --silent --location --retry 3 \
  "https://gerrit.googlesource.com/git-repo/+/$repo_revision/repo?format=TEXT" \
  -o "$task_temp/repo.base64"
base64 --decode "$task_temp/repo.base64" > "$task_temp/bin/repo"
chmod 0755 "$task_temp/bin/repo"
sha256sum "$task_temp/bin/repo" > "$diagnostics/repo-launcher-sha256.txt"
repo_command="$task_temp/bin/repo"

cd "$workspace"
"$repo_command" init \
  -u https://android.googlesource.com/kernel/manifest \
  -b "$manifest_revision" \
  --depth=1 --no-clone-bundle --no-use-superproject \
  --repo-url=https://gerrit.googlesource.com/git-repo \
  --repo-rev="$repo_revision"

[[ "$(git -C .repo/manifests rev-parse HEAD)" == "$manifest_revision" ]]
[[ "$(git -C .repo/repo rev-parse HEAD)" == "$repo_revision" ]]
"$repo_command" version > "$diagnostics/repo-version.txt"
cp .repo/manifests/default.xml "$diagnostics/upstream-manifest.xml"
mkdir -p .repo/local_manifests
cat > .repo/local_manifests/android17-pins.xml <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<manifest>
  <extend-project name="kernel/common" path="common" revision="$kernel_revision" upstream="refs/heads/android17-6.18" />
  <extend-project name="kernel/build" path="build/kernel" revision="$build_revision" upstream="refs/heads/main-kernel-2026" />
</manifest>
EOF
cp .repo/local_manifests/android17-pins.xml "$diagnostics/local-pins.xml"

# Other projects in the official manifest still name branches. The resolved
# manifest below is the complete source/toolchain lock for this specific run.
"$repo_command" sync -c -j2 --no-tags --no-clone-bundle --fail-fast
[[ "$(git -C common rev-parse HEAD)" == "$kernel_revision" ]]
[[ "$(git -C build/kernel rev-parse HEAD)" == "$build_revision" ]]
"$repo_command" manifest -r -o "$diagnostics/resolved-manifest.xml"
"$repo_command" forall -c 'printf "%s %s\n" "$REPO_PATH" "$(git rev-parse HEAD)"' \
  > "$diagnostics/project-commits.txt"
git -C common show -s --format=fuller HEAD > "$diagnostics/kernel-commit.txt"
git -C build/kernel show -s --format=fuller HEAD > "$diagnostics/kleaf-commit.txt"
cp common/Makefile "$diagnostics/kernel-Makefile"
cp common/BUILD.bazel "$diagnostics/kernel-BUILD.bazel"
python3 - common/Makefile <<'PY'
import re
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    content = stream.read()
version = re.search(r"^VERSION\s*=\s*(\d+)", content, re.M)
patchlevel = re.search(r"^PATCHLEVEL\s*=\s*(\d+)", content, re.M)
assert version and patchlevel
assert (version.group(1), patchlevel.group(1)) == ("6", "18")
PY

# Resolve the real target and option against the fetched Kleaf implementation.
tools/bazel help kleaf > "$diagnostics/kleaf-help.txt"
tools/bazel query //common:kernel_aarch64_dist > "$diagnostics/build-target.txt"
tools/bazel run --page_size=4k //common:kernel_aarch64_dist -- --destdir="$dist"

# The pinned Kleaf common_kernel keeps the final generated configuration as
# kernel_aarch64_dot_config (keep_dot_config=True), not as .config. Preserve
# support for a distribution that also uses the original filename.
final_config="$dist/kernel_aarch64_dot_config"
if [[ ! -f "$final_config" && -f "$dist/.config" ]]; then
  final_config="$dist/.config"
fi
# If the distribution omits it, ask Bazel for the files of the same configured
# target and retain the output list and discovered configurations for diagnosis.
if [[ ! -f "$final_config" ]]; then
  tools/bazel cquery --page_size=4k --output=files //common:kernel_aarch64 \
    > "$diagnostics/kernel-output-files.txt"
  final_config=''
  config_candidates=0
  while IFS= read -r output_file; do
    if [[ ( "$output_file" == */kernel_aarch64_dot_config || "$output_file" == */.config ) && -f "$output_file" ]]; then
      config_candidates=$((config_candidates + 1))
      cp "$output_file" "$diagnostics/config-candidate-$config_candidates.config"
      if [[ -n "$final_config" ]]; then
        printf '%s\n' 'Multiple final configuration outputs found; refusing an ambiguous result.' >&2
        exit 1
      fi
      final_config="$output_file"
    fi
  done < "$diagnostics/kernel-output-files.txt"
fi
[[ -n "$final_config" && -f "$final_config" ]]
cp "$final_config" "$diagnostics/final.config"
grep -qx 'CONFIG_ARM64_4K_PAGES=y' "$final_config"
if grep -Eq '^CONFIG_ARM64_(16K|64K)_PAGES=y$' "$final_config"; then
  printf '%s\n' 'Unexpected page size in the generated configuration.' >&2
  exit 1
fi
[[ -s "$dist/Image" ]]

# Deliberately export only generic kernel outputs; never export boot.img or a
# flash ZIP that could be mistaken for a device port.
cp "$final_config" "$baseline/final.config"
for artifact in Image Image.gz Image.lz4 Image.lzma Image.zst System.map Module.symvers; do
  if [[ -f "$dist/$artifact" ]]; then
    cp "$dist/$artifact" "$baseline/$artifact"
  fi
done
# keep_module_symvers=True in the pinned Kleaf exports a renamed output.
# Preserve it under the conventional name for later module ABI investigation.
if [[ ! -f "$baseline/Module.symvers" && -f "$dist/kernel_aarch64_Module.symvers" ]]; then
  cp "$dist/kernel_aarch64_Module.symvers" "$baseline/Module.symvers"
fi
cat > "$baseline/DO-NOT-FLASH.txt" <<EOF
Generic Android 17 ACK 6.18 / 4 KiB build baseline.
NOT a realme X7 Pro / RMX2121 port. DO NOT FLASH ON THE PHONE.

This build validates the public ACK source and build environment only.
MT6885/RMX2121 device trees, drivers, vendor UAPI compatibility and boot image
integration have not been implemented or tested by this workflow.

Kernel commit: $kernel_revision
Kleaf commit: $build_revision
Manifest commit: $manifest_revision
Complete dependency snapshot: resolved-manifest.xml in the diagnostics artifact.
EOF
python3 - "$baseline" <<'PY'
import hashlib
import pathlib
import sys

directory = pathlib.Path(sys.argv[1])
with (directory / "SHA256SUMS").open("w", encoding="utf-8") as checksums:
    for artifact in sorted(directory.iterdir()):
        if not artifact.is_file() or artifact.name == "SHA256SUMS":
            continue
        digest = hashlib.sha256()
        with artifact.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        checksums.write(f"{digest.hexdigest()}  {artifact.name}\n")
PY
printf '%s\n' 'Generic ACK baseline compiled and final 4 KiB configuration verified.'
printf '%s\n' 'No RMX2121 bootability or hardware functionality has been demonstrated.'
