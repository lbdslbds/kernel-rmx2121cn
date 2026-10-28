#!/usr/bin/env bash
# Source this as a standalone command after build-4.14.sh has completed its
# full kernel build. The subshell preserves the caller's variables and EXIT trap.
(
set -euo pipefail
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  printf '%s\n' 'Source this file from build-4.14.sh after the full build.' >&2
  exit 2
fi
for name in ROOT WORK ART SRC OUT; do
  if [[ -z ${!name:-} || ${!name} != /* ]]; then
    printf 'Missing absolute build path: %s\n' "$name" >&2
    exit 2
  fi
done
if [[ $(declare -p args 2>/dev/null) != 'declare -a '* ]]; then
  printf '%s\n' 'The caller must supply the existing indexed args array.' >&2
  exit 2
fi

EXT="$ART/external-modules"
MOD="$SRC/drivers/kernel_modules"
TOP="$WORK/external-android-top"
stage=prepare
current_module=none
completed=0
# Avoid mixing a failed attempt, stale binaries, or a different signing key.
if [[ -e "$EXT" || -L "$EXT" ]]; then
  printf 'Refusing to overwrite existing external-module evidence: %s\n' "$EXT" >&2
  exit 2
fi
mkdir -p "$EXT" || exit "$?"

finish_external() {
  local code=$?
  trap - EXIT
  set +e
  if [[ -f "$EXT/kernel.Module.symvers" ]] &&
     ! cmp -s "$EXT/kernel.Module.symvers" "$OUT/Module.symvers"; then
    printf '%s\n' 'ERROR: the kernel Module.symvers changed during external compilation.' >&2
    printf '%s\n' 'Kernel symbol table changed; do not use these outputs.' > "$EXT/INTEGRITY-ERROR.txt"
    [[ $code -ne 0 ]] || code=86
  fi
  printf 'exit_code=%s\nstage=%s\nmodule=%s\ncompleted=%s/8\n' \
    "$code" "$stage" "$current_module" "$completed" > "$EXT/result.txt" || code=87
  if [[ $code -eq 0 ]]; then
    printf '%s\n' 'COMPILED AND SIGNED: 8 modules. FPSGO is excluded. No device load, boot, or runtime validation.' > "$EXT/STATUS.txt" || code=87
  else
    printf '%s\n' 'FAILED: see result.txt and per-module logs. Only completed.list identifies completed candidates; none are device-validated.' > "$EXT/STATUS.txt" || code=87
  fi
  if ! (cd "$EXT" && find . -type f ! -name SHA256SUMS ! -name SHA256SUMS.tmp -print0 |
    sort -z | xargs -0 -r sha256sum > SHA256SUMS.tmp && mv SHA256SUMS.tmp SHA256SUMS); then
    code=87
    printf '%s\n' 'ERROR: evidence hashing failed; no complete SHA256SUMS was published.' >&2
    printf '%s\n' 'Evidence hashing failed. SHA256SUMS.tmp is incomplete.' > "$EXT/EVIDENCE-ERROR.txt"
    printf 'exit_code=%s\nstage=%s\nmodule=%s\ncompleted=%s/8\n' \
      "$code" "$stage" "$current_module" "$completed" > "$EXT/result.txt"
    printf '%s\n' 'FAILED: evidence hashing failed. See EVIDENCE-ERROR.txt and per-module logs.' > "$EXT/STATUS.txt"
  fi
  exit "$code"
}
trap finish_external EXIT

fail() { printf 'External modules: %s\n' "$*" >&2; exit 1; }
require_file() { [[ -s "$1" ]] || fail "missing or empty required file: $1"; }
log_command() {
  local destination=$1
  shift
  { printf '%q ' "$@"; printf '\n'; } >> "$destination" || exit "$?"
}
run_logged() {
  local log=$1
  shift
  # pipefail preserves the command failure even when tee succeeds. Callers
  # explicitly propagate errors, including when their caller uses `if source`.
  "$@" 2>&1 | tee -a "$log" || return "$?"
}
check_kernel_symvers() {
  cmp -s "$EXT/kernel.Module.symvers" "$OUT/Module.symvers" ||
    fail 'kernel Module.symvers changed; refusing to continue'
}

[[ ${JOBS:-4} =~ ^[1-9][0-9]*$ ]] || fail 'JOBS must be a positive integer'
# KBUILD_EXTRA_SYMBOLS is a make whitespace-separated list, not a shell array.
for path in "$MOD" "$OUT" "$TOP"; do
  [[ ! $path =~ [[:space:]] ]] || fail "make module paths cannot contain whitespace: $path"
done
for file in "$OUT/vmlinux" "$OUT/Module.symvers" "$OUT/.config" \
  "$OUT/include/generated/autoconf.h" "$OUT/include/config/kernel.release" \
  "$OUT/certs/signing_key.pem" "$OUT/certs/signing_key.x509" \
  "$ROOT/scripts/compare-module-crcs.py"; do
  require_file "$file"
done
[[ -x "$OUT/scripts/sign-file" ]] || fail 'the full build has no executable scripts/sign-file'
for setting in CONFIG_MODULE_SIG=y CONFIG_MODULE_SIG_FORCE=y CONFIG_MODULE_SIG_ALL=y \
  CONFIG_MODULE_SIG_SHA512=y CONFIG_MODVERSIONS=y CONFIG_MTK_COMBO_CHIP_CONSYS_6885=y; do
  grep -qxF "$setting" "$OUT/.config" || fail "required configuration missing: $setting"
done
for makefile in nativefeatures.mk OplusKernelEnvConfig.mk; do
  cmp -s "$SRC/$makefile" "$OUT/$makefile" || fail "missing/mismatched OUT/$makefile"
done
cp "$OUT/Module.symvers" "$EXT/kernel.Module.symvers" || exit "$?"
cp "$OUT/include/config/kernel.release" "$EXT/kernelrelease.txt" || exit "$?"
cp "$OUT/certs/signing_key.x509" "$EXT/signing-certificate.der" || exit "$?"
# Archive only the public certificate; never copy the .pem private-key file.
openssl x509 -inform DER -in "$EXT/signing-certificate.der" -noout \
  -subject -issuer -serial -fingerprint -sha256 > "$EXT/signing-certificate.txt" || exit "$?"
printf 'module\tsource_path\tdependency_modules\n' > "$EXT/plan.tsv" || exit "$?"
: > "$EXT/completed.list" || exit "$?"
cat > "$EXT/OPTIONS.txt" <<'EOF' || exit "$?"
Source/build provenance: ../sources.lock.json, ../effective.config and ../patches/.
All modules use the current full build, its generated headers, Module.symvers,
Clang/binutils and signing key; no kernel configuration or old module CRC is changed.
Every actual make invocation is recorded in its module's commands.txt and make-argv.json.

Known platform selections: MT6885, user build, BT 6885, FM connac2x/mt6635,
GPS root entry (not GPS_CHIP_ID=common), Wi-Fi adaptor CONNAC_VER=2_0,
gen4m CONNAC2X2_SOC3_0 + WLAN_CHIP_ID=6885 + AXI + MTK_WLAN_SERVICE=yes.
MTK_WLAN_SERVICE=yes follows the official build_wlan_drv.mk.
The exact factory values of MTK_ANDROID_EMI, MTK_ANDROID_WMT, WIFI_IP_SET,
OPLUS_FEATURE_WIFI_SMARTGEAR and MTK_WLAN_SERVICE_PATH have not been recovered.
They are not overridden here; source Makefiles/features and the recorded incoming
environment determine them. This is a compile candidate, not a factory-equivalent build.
MET can select reduced functionality from available platform headers; inspect its log.
fpsgo.ko is excluded: the matching vendor/mediatek/kernel_modules/fpsgo_cus source
is absent. In-tree fpsgo_v3 is not an equivalent replacement for that module.
Neither successful compilation, matching CRCs nor signing proves device compatibility.
EOF
for name in MTK_ANDROID_EMI MTK_ANDROID_WMT WIFI_IP_SET OPLUS_FEATURE_WIFI_SMARTGEAR \
  MTK_WLAN_SERVICE_PATH; do
  printf '%s=%q\n' "$name" "${!name-<unset>}" >> "$EXT/incoming-options.txt" || exit "$?"
done
mkdir -p "$TOP/vendor/mediatek" || exit "$?"
link="$TOP/vendor/mediatek/kernel_modules"
if [[ -e "$link" || -L "$link" ]]; then
  [[ -L "$link" && $(readlink -f "$link") == $(readlink -f "$MOD") ]] ||
    fail "existing Android layout has an unexpected target: $link"
else
  ln -s "$MOD" "$link" || exit "$?"
fi

declare -A module_symvers=()
build_external() {
  local name=$1 relative=$2 dependencies=$3
  shift 3
  local directory="$MOD/$relative" evidence="$EXT/$name" dependency symbols=''
  local -a dependency_tables=() command=()
  current_module=$name
  stage=prepare-module
  mkdir -p "$evidence" || exit "$?"
  printf '%s\t%s\t%s\n' "$name" "$relative" "$dependencies" >> "$EXT/plan.tsv" || exit "$?"
  [[ -f "$directory/Kbuild" || -f "$directory/Makefile" ]] || fail "no build entry: $directory"
  for dependency in $dependencies; do
    [[ -v module_symvers[$dependency] ]] || fail "dependency not completed: $dependency"
    require_file "${module_symvers[$dependency]}"
    dependency_tables+=("${module_symvers[$dependency]}")
  done
  if [[ ${#dependency_tables[@]} -gt 0 ]]; then
    printf -v symbols '%s ' "${dependency_tables[@]}"
    symbols=${symbols% }
  fi
  command=(make -C "$SRC" "${args[@]}" -j"${JOBS:-4}"
    M="$directory" KERNEL_OUT="$OUT" TOP="$TOP" TARGET_BUILD_VARIANT=user
    KBUILD_MODPOST_FAIL_ON_WARNINGS=y KBUILD_MODPOST_WARN= KBUILD_MODPOST_NOFINAL=
    KBUILD_EXTRA_SYMBOLS="$symbols" "$@" modules)
  log_command "$evidence/commands.txt" "${command[@]}"
  python3 -c 'import json,sys; print(json.dumps(sys.argv[1:], indent=2))' \
    "${command[@]}" > "$evidence/make-argv.json" || exit "$?"
  stage=compile
  run_logged "$evidence/build.log" "${command[@]}" || exit "$?"
  check_kernel_symvers
  require_file "$directory/$name.ko"
  [[ -f "$directory/Module.symvers" ]] || fail "module symbol table not produced: $name"
  cp "$directory/Module.symvers" "$evidence/Module.symvers" || exit "$?"

  stage=check-crcs
  # Check the actual linked ELF against the kernel and this module's declared
  # build dependencies. Do not rewrite CRCs or merge anything into the kernel table.
  python3 - "$ROOT/scripts/compare-module-crcs.py" "$directory/$name.ko" \
    "$EXT/kernelrelease.txt" "$evidence/crc-check.json" \
    "$EXT/kernel.Module.symvers" "${dependency_tables[@]}" <<'PY' 2>&1 | tee "$evidence/crc-check.log" || exit "$?"
import importlib.util, json, pathlib, sys
spec = importlib.util.spec_from_file_location("module_crc_audit", sys.argv[1])
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
module_file, release_file, report_file = map(pathlib.Path, sys.argv[2:5])
symbols = {}
for filename in sys.argv[5:]:
    text = pathlib.Path(filename).read_text(encoding="utf-8")
    for name, entry in audit.parse_symvers(text).items():
        if name in symbols and symbols[name] != entry:
            raise SystemExit(f"Conflicting providers/CRCs for {name}")
        symbols[name] = entry
module = audit.parse_module(module_file.read_bytes())
missing, mismatched = [], []
for name, crc in sorted(module["versions"].items()):
    if name not in symbols:
        missing.append(name)
    elif symbols[name]["crc"] != crc:
        mismatched.append({"symbol": name, "module_crc": f"0x{crc:08x}",
                           "target_crc": f"0x{symbols[name]['crc']:08x}"})
release = release_file.read_text().strip()
metadata_ok = (module["module_name"] == module_file.stem and
               (module["vermagic"] or "").split(" ")[0] == release)
report = {"module": module_file.name, "module_name": module["module_name"],
          "vermagic": module["vermagic"], "kernelrelease": release,
          "declared_dependencies": module["declared_dependencies"],
          "versioned_imports": len(module["versions"]), "missing": missing,
          "mismatched": mismatched, "metadata_matches": metadata_ok,
          "device_load_tested": False}
report_file.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
if missing or mismatched or not metadata_ok:
    raise SystemExit("Module CRC/metadata validation failed; see " + str(report_file))
print(f"{module_file.name}: {len(module['versions'])} versioned imports match")
PY

  stage=sign
  command=("$OUT/scripts/sign-file" sha512 "$OUT/certs/signing_key.pem"
    "$OUT/certs/signing_key.x509" "$directory/$name.ko")
  log_command "$evidence/commands.txt" "${command[@]}"
  run_logged "$evidence/sign.log" "${command[@]}" || exit "$?"
  # sign-file's exit status is mandatory; this additional check detects a missing
  # trailer. No stripping or other ELF mutation is allowed after signing.
  python3 - "$directory/$name.ko" <<'PY' 2>&1 | tee -a "$evidence/sign.log" || exit "$?"
import pathlib, struct, sys
data = pathlib.Path(sys.argv[1]).read_bytes()
marker = b"~Module signature appended~\n"
if not data.endswith(marker) or len(data) < len(marker) + 12:
    raise SystemExit("Missing module signature trailer")
trailer = data[-len(marker)-12:-len(marker)]
signature_size = struct.unpack(">I", trailer[8:])[0]
if trailer[2] != 2 or not 0 < signature_size < len(data) - len(marker) - 12:
    raise SystemExit("Invalid PKCS#7 module signature trailer")
PY
  stage=archive
  cp "$directory/$name.ko" "$evidence/$name.ko" || exit "$?"
  check_kernel_symvers
  printf '%s\n' 'COMPILED, CRC-CHECKED AND SIGNED. Device load/boot/runtime untested.' > "$evidence/STATUS.txt" || exit "$?"
  printf '%s/%s.ko\n' "$name" "$name" >> "$EXT/completed.list" || exit "$?"
  module_symvers[$name]="$evidence/Module.symvers"
  completed=$((completed + 1))
}

build_external conninfra connectivity/conninfra ''
build_external connfem connectivity/connfem ''
build_external bt_drv_6885 connectivity/bt/mt66xx/btif 'conninfra connfem' BT_PLATFORM=6885
build_external fmradio_drv_connac2x connectivity/fmradio conninfra \
  CFG_FM_PLAT=connac2x CFG_BUILD_CONNAC2=true CFG_FM_CHIP=mt6635 CONFIG_FM_USER_LOAD=1
build_external gps_drv connectivity/gps conninfra GPS_CHIP_ID=
build_external wmt_chrdev_wifi connectivity/wlan/adaptor conninfra \
  MODULE_NAME=wmt_chrdev_wifi CONNAC_VER=2_0
build_external wlan_drv_gen4m connectivity/wlan/core/gen4m 'conninfra connfem wmt_chrdev_wifi' \
  MODULE_NAME=wlan_drv_gen4m WLAN_CHIP_ID=6885 MTK_COMBO_CHIP=CONNAC2X2_SOC3_0 \
  CONFIG_MTK_COMBO_WIFI_HIF=axi MTK_WLAN_SERVICE=yes
build_external met met_drv/4.14 ''
stage=complete
current_module=none
[[ $completed -eq 8 ]] || fail 'not all eight targets completed'
)
