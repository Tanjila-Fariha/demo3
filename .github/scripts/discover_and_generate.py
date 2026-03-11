#!/usr/bin/env python3
"""
discover_and_generate.py
========================
Zero-configuration clang-uml orchestrator.

Works on ANY C++ project — with or without namespaces.

The ONLY filter used is: skip methods whose top-level namespace is a
well-known external library (std, boost, Qt, etc.).
Everything else — namespaced or not — is diagrammed.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────────────
# Known external namespaces — never part of a user's project code.
# These are the ONLY things filtered out. Everything else is kept.
# ─────────────────────────────────────────────────────────────────────────────
EXTERNAL_NAMESPACES = {
    "std", "stdext", "__gnu_cxx", "__cxxabiv1", "__detail",
    "__atomic", "__future", "__regex", "__locale",
    "boost", "folly", "absl", "google", "Poco",
    "Qt", "QT",
    "testing", "gtest", "gmock",
    "nlohmann", "spdlog", "fmt",
    "tbb", "intel", "oneapi",
    "rapidjson", "pugi", "yaml_cpp",
    "crow", "httplib", "pistache",
    "Eigen", "opencv", "cv",
    "grpc", "protobuf",
    "llvm", "clang",
    "asio",
}

_SIG_LINE = re.compile(r'"([^"]+)"')


def run(cmd):
    return subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )


# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — Read every source file from compile_commands.json
# ─────────────────────────────────────────────────────────────────────────────
def load_source_files(build_dir: Path) -> list:
    cc_path = build_dir / "compile_commands.json"
    if not cc_path.exists():
        print(f"[ERROR] {cc_path} not found.", file=sys.stderr)
        print("  Add  set(CMAKE_EXPORT_COMPILE_COMMANDS ON)  to CMakeLists.txt",
              file=sys.stderr)
        sys.exit(1)

    with cc_path.open() as f:
        entries = json.load(f)

    files, seen = [], set()
    for entry in entries:
        raw = entry.get("file", "")
        if not raw:
            continue
        p = Path(raw).resolve()
        if p.suffix.lower() in (".cpp", ".cc", ".cxx", ".c") and p not in seen:
            files.append(p)
            seen.add(p)
    return files


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — Collect unique source directories (pruned)
# ─────────────────────────────────────────────────────────────────────────────
def collect_source_dirs(source_files: list, cwd: Path) -> list:
    raw_dirs = sorted({p.parent for p in source_files
                       if str(p).startswith(str(cwd))})
    pruned = []
    for d in raw_dirs:
        if not any(str(d).startswith(str(other) + os.sep) for other in pruned):
            pruned.append(d)
    return pruned if pruned else [cwd]


# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — Probe clang-uml with --print-from to get all callable methods
#
# NO namespace filter. NO paths filter.
# We only exclude known external namespaces by their prefix.
# ─────────────────────────────────────────────────────────────────────────────
PROBE_TEMPLATE = """\
compilation_database_dir: {build_dir}
output_directory: /tmp/_clang_uml_probe_out_
diagrams:
  _probe_:
    type: sequence
    generate_method_arguments: none
    exclude:
      namespaces:
{excl_lines}
    from:
      - function: ".*"
"""


def write_probe_config(build_dir: Path, extra_exclude: set, cfg_path: Path):
    excl_lines = "\n".join(
        f"        - {ns}"
        for ns in sorted(EXTERNAL_NAMESPACES | extra_exclude)
    )
    cfg_path.write_text(PROBE_TEMPLATE.format(
        build_dir=str(build_dir),
        excl_lines=excl_lines,
    ))


def discover_methods(probe_cfg: Path, extra_exclude: set) -> list:
    result = run([
        "clang-uml",
        "--config", str(probe_cfg),
        "--print-from", "-n", "_probe_",
    ])

    # Always print clang-uml stderr so parse errors are visible in CI logs
    if result.stderr.strip():
        print("      [clang-uml stderr output]:")
        for line in result.stderr.strip().splitlines()[:30]:
            print(f"        {line}")

    exclude = EXTERNAL_NAMESPACES | extra_exclude
    seen, methods = set(), []

    for line in result.stdout.splitlines():
        m = _SIG_LINE.search(line)
        if not m:
            continue
        sig = m.group(1).strip()
        if not sig or sig in seen:
            continue

        # Only skip if the top-level namespace is a known external library
        # If there is no namespace (global function / plain class) — keep it
        top_ns = sig.split("::")[0] if "::" in sig else ""
        if top_ns and top_ns in exclude:
            continue

        seen.add(sig)
        methods.append(sig)

    return methods


# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — Write one clang-uml YAML config per method
# ─────────────────────────────────────────────────────────────────────────────
METHOD_TEMPLATE = """\
compilation_database_dir: {build_dir}
output_directory: {out_dir}

diagrams:
  {name}:
    type: sequence
    title: "{title}"
    generate_method_arguments: abbreviated
    generate_return_types: true
    generate_return_values: true
    generate_condition_statements: true
    generate_message_comments: true
    exclude:
      namespaces:
{excl_lines}
      access:
        - private
    from:
      - function: "{sig}"
"""


def _safe_id(sig: str) -> str:
    s = re.sub(r"<[^>]*>", "", sig)
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"[^A-Za-z0-9_]", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return ("seq_" + s)[:120]


def _human_title(sig: str) -> str:
    return re.sub(r"\(.*\)$", "()", sig)


def write_method_configs(methods: list, build_dir: Path,
                         extra_exclude: set, out_dir: str,
                         cfg_dir: Path) -> list:
    excl_lines = "\n".join(
        f"        - {ns}"
        for ns in sorted(EXTERNAL_NAMESPACES | extra_exclude)
    )
    cfg_paths   = []
    name_counts = {}

    for sig in methods:
        base        = _safe_id(sig)
        count       = name_counts.get(base, 0)
        name_counts[base] = count + 1
        unique_name = f"{base}_{count:04d}"
        cfg_path    = cfg_dir / f"{unique_name}.yaml"

        cfg_path.write_text(METHOD_TEMPLATE.format(
            build_dir=str(build_dir),
            out_dir=out_dir,
            name=unique_name,
            title=_human_title(sig),
            excl_lines=excl_lines,
            sig=sig.replace('"', '\\"'),
        ))
        cfg_paths.append(str(cfg_path))

    return cfg_paths


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-dir",  default="build")
    ap.add_argument("--out-dir",    default="docs/diagrams/puml")
    ap.add_argument("--cfg-dir",    default=".clang-uml-generated")
    ap.add_argument("--exclude-ns", action="append", default=[], metavar="NS")
    args = ap.parse_args()

    cwd       = Path.cwd()
    build_dir = Path(args.build_dir).resolve()
    out_dir   = str(Path(args.out_dir).resolve())
    cfg_dir   = Path(args.cfg_dir).resolve()
    extra_exc = set(args.exclude_ns)

    cfg_dir.mkdir(parents=True, exist_ok=True)
    Path(args.out_dir).mkdir(parents=True, exist_ok=True)

    # 1. Source files
    print("[1/4] Reading compile_commands.json …")
    source_files = load_source_files(build_dir)
    if not source_files:
        print("      [ERROR] No source files found in compile_commands.json")
        (cfg_dir / "manifest.json").write_text("[]")
        sys.exit(1)
    print(f"      {len(source_files)} source file(s):")
    for f in source_files:
        print(f"        {f}")

    # 2. Source dirs
    print("[2/4] Mapping source directories …")
    source_dirs = collect_source_dirs(source_files, cwd)
    for d in source_dirs:
        print(f"      {d}")

    # 3. Discover all methods
    print("[3/4] Running clang-uml --print-from …")
    print("      Filtering: skip only known external namespaces (std, boost, etc.)")
    print("      Keeping:   ALL project methods — namespaced or not")
    probe_cfg = cfg_dir / "_probe_.yaml"
    write_probe_config(build_dir, extra_exc, probe_cfg)
    methods = discover_methods(probe_cfg, extra_exc)

    if not methods:
        print("")
        print("      [ERROR] clang-uml --print-from returned no methods.")
        print("      This means clang-uml could not parse your project.")
        print("")
        print("      Probe config used:")
        print("      " + "-" * 56)
        for line in probe_cfg.read_text().splitlines():
            print(f"      {line}")
        print("      " + "-" * 56)
        print("")
        print("      Common fixes:")
        print("      1. Ensure the project has no compile errors")
        print("      2. Check CMAKE_CXX_STANDARD matches your code (14/17/20)")
        print("      3. Try adding: apt-get install libstdc++-11-dev")
        (cfg_dir / "manifest.json").write_text("[]")
        sys.exit(1)

    print(f"      {len(methods)} method(s) discovered:")
    for sig in methods:
        print(f"        {sig}")

    # 4. Write per-method configs
    print("[4/4] Writing per-method clang-uml configs …")
    cfg_paths = write_method_configs(
        methods, build_dir, extra_exc, out_dir, cfg_dir
    )

    manifest = cfg_dir / "manifest.json"
    manifest.write_text(json.dumps(cfg_paths, indent=2))

    print(f"\n✓  {len(cfg_paths)} diagram config(s) written → {cfg_dir}")


if __name__ == "__main__":
    main()