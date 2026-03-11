#!/usr/bin/env python3
"""
discover_and_generate.py
========================
Zero-configuration clang-uml orchestrator.
Works on ANY C++ project — no namespaces, paths, or method names to configure.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


# ──────────────────────────────────────────────────────────────────────────────
# Well-known external namespaces — never diagrammed
# ──────────────────────────────────────────────────────────────────────────────
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

_NS_DECL = re.compile(r'^\s*(?:inline\s+)?namespace\s+([\w][\w:]*)\s*(?:\{|$)')
_SIG_LINE = re.compile(r'"([^"]+)"')


def run(cmd: list) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


# ──────────────────────────────────────────────────────────────────────────────
# STEP 1  Read compile_commands.json
# ──────────────────────────────────────────────────────────────────────────────
def load_source_files(build_dir: Path) -> list:
    cc_path = build_dir / "compile_commands.json"
    if not cc_path.exists():
        print(f"[ERROR] {cc_path} not found.", file=sys.stderr)
        print("  Add  set(CMAKE_EXPORT_COMPILE_COMMANDS ON)  to CMakeLists.txt", file=sys.stderr)
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


# ──────────────────────────────────────────────────────────────────────────────
# STEP 2  Detect project namespaces by scanning source files
# ──────────────────────────────────────────────────────────────────────────────
def detect_namespaces(source_files: list, extra_exclude: set) -> list:
    exclude = EXTERNAL_NAMESPACES | extra_exclude
    counts = {}
    for src in source_files:
        try:
            text = src.read_text(errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            m = _NS_DECL.match(line)
            if not m:
                continue
            top = m.group(1).split("::")[0]
            if top and not top.startswith("_") and top not in exclude:
                counts[top] = counts.get(top, 0) + 1
    return sorted(counts, key=lambda n: -counts[n])


# ──────────────────────────────────────────────────────────────────────────────
# STEP 3  Collect source directories (pruned so no dir is a child of another)
# ──────────────────────────────────────────────────────────────────────────────
def collect_source_dirs(source_files: list, cwd: Path) -> list:
    raw_dirs = sorted({p.parent for p in source_files
                       if str(p).startswith(str(cwd))})
    pruned = []
    for d in raw_dirs:
        if not any(str(d).startswith(str(other) + os.sep) for other in pruned):
            pruned.append(d)
    # Fallback: if nothing matched cwd prefix, just use the repo root
    return pruned if pruned else [cwd]


# ──────────────────────────────────────────────────────────────────────────────
# STEP 4  Write probe config + run clang-uml --print-from
#
# KEY FIX: The probe config must NOT have a `paths` filter.
# Using `paths` restricts what clang-uml parses and causes it to find nothing
# when the project has no namespaces or uses simple class structures.
# Instead we rely solely on compile_commands.json to scope the project.
# ──────────────────────────────────────────────────────────────────────────────
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
        f"        - {ns}" for ns in sorted(EXTERNAL_NAMESPACES | extra_exclude)
    )
    cfg_path.write_text(PROBE_TEMPLATE.format(
        build_dir=str(build_dir),
        excl_lines=excl_lines,
    ))


def discover_methods(probe_cfg: Path, project_namespaces: list,
                     source_files: list) -> list:
    result = run(["clang-uml", "--config", str(probe_cfg),
                  "--print-from", "-n", "_probe_"])

    # Always print clang-uml's stderr so failures are visible in CI logs
    if result.stderr.strip():
        for line in result.stderr.strip().splitlines():
            print(f"      [clang-uml] {line}")

    # Build a set of absolute source file paths for fast lookup
    project_files = {str(f) for f in source_files}

    seen, methods = set(), []
    for line in result.stdout.splitlines():
        m = _SIG_LINE.search(line)
        if not m:
            continue
        sig = m.group(1).strip()
        if not sig or sig in seen:
            continue

        # Filter strategy:
        #   A) If we detected namespaces → keep only methods in those namespaces
        #   B) If no namespaces (plain classes / global functions) →
        #      keep everything that isn't in a known external namespace
        if project_namespaces:
            if not any(sig.startswith(ns + "::") for ns in project_namespaces):
                continue
        else:
            if any(sig.startswith(ns + "::") for ns in EXTERNAL_NAMESPACES):
                continue

        seen.add(sig)
        methods.append(sig)

    return methods


# ──────────────────────────────────────────────────────────────────────────────
# STEP 5  Write one clang-uml YAML config per method
#
# KEY FIX: Per-method configs also must NOT use a `paths` filter for the same
# reason — it breaks projects without namespaces. We use the full
# compile_commands.json scope and rely on the `from` entry point instead.
# ──────────────────────────────────────────────────────────────────────────────
METHOD_TEMPLATE = """\
compilation_database_dir: {build_dir}
output_directory: {out_dir}
{using_ns_block}
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


def _human_title(sig: str, project_ns: list) -> str:
    t = sig
    for ns in sorted(project_ns, key=len, reverse=True):
        if t.startswith(ns + "::"):
            t = t[len(ns) + 2:]
            break
    return re.sub(r"\(.*\)$", "()", t)


def write_method_configs(methods: list, build_dir: Path,
                         project_ns: list, extra_exclude: set,
                         out_dir: str, cfg_dir: Path) -> list:
    excl_lines = "\n".join(
        f"        - {ns}" for ns in sorted(EXTERNAL_NAMESPACES | extra_exclude)
    )
    using_ns_block = (
        "using_namespace:\n" + "\n".join(f"  - {ns}" for ns in project_ns)
        if project_ns else ""
    )

    cfg_paths = []
    name_counts = {}
    for sig in methods:
        base  = _safe_id(sig)
        count = name_counts.get(base, 0)
        name_counts[base] = count + 1
        unique_name = f"{base}_{count:04d}"
        cfg_path    = cfg_dir / f"{unique_name}.yaml"

        cfg_path.write_text(METHOD_TEMPLATE.format(
            build_dir=str(build_dir),
            out_dir=out_dir,
            using_ns_block=using_ns_block,
            name=unique_name,
            title=_human_title(sig, project_ns),
            excl_lines=excl_lines,
            sig=sig.replace('"', '\\"'),
        ))
        cfg_paths.append(str(cfg_path))
        print(f"      {unique_name}  ←  {sig}")

    return cfg_paths


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Zero-config clang-uml orchestrator for any C++ project"
    )
    ap.add_argument("--build-dir",  default="build")
    ap.add_argument("--out-dir",    default="docs/diagrams/puml")
    ap.add_argument("--cfg-dir",    default=".clang-uml-generated")
    ap.add_argument("--exclude-ns", action="append", default=[], metavar="NS")
    ap.add_argument("--min-calls",  type=int, default=1)
    args = ap.parse_args()

    cwd       = Path.cwd()
    build_dir = Path(args.build_dir).resolve()
    out_dir   = str(Path(args.out_dir).resolve())
    cfg_dir   = Path(args.cfg_dir).resolve()
    extra_exc = set(args.exclude_ns)

    cfg_dir.mkdir(parents=True, exist_ok=True)
    Path(args.out_dir).mkdir(parents=True, exist_ok=True)

    # 1. Source files
    print("[1/5] Reading compile_commands.json …")
    source_files = load_source_files(build_dir)
    if not source_files:
        print("      [WARN] No source files in compile_commands.json — writing empty manifest")
        (cfg_dir / "manifest.json").write_text("[]")
        sys.exit(0)
    print(f"      {len(source_files)} source file(s) found")

    # 2. Namespace detection
    print("[2/5] Detecting project namespaces …")
    project_ns = detect_namespaces(source_files, extra_exc)
    if project_ns:
        print(f"      Detected: {', '.join(project_ns)}")
    else:
        print("      No namespaces — will diagram all non-external methods")

    # 3. Source dirs
    print("[3/5] Mapping source directory tree …")
    source_dirs = collect_source_dirs(source_files, cwd)
    for d in source_dirs:
        print(f"      {d}")

    # 4. Discover methods via --print-from
    print("[4/5] Running clang-uml --print-from …")
    probe_cfg = cfg_dir / "_probe_.yaml"
    write_probe_config(build_dir, extra_exc, probe_cfg)
    methods = discover_methods(probe_cfg, project_ns, source_files)

    if not methods:
        print("      [WARN] No methods discovered.")
        print("      Possible causes:")
        print("        • Project has compile errors (clang-uml cannot parse it)")
        print("        • All methods are in external/excluded namespaces")
        print("        • clang-uml version mismatch with the installed clang")
        print("      Writing empty manifest so the workflow continues gracefully.")
        # KEY FIX: always write manifest.json even when empty
        # so the workflow never crashes with FileNotFoundError
        (cfg_dir / "manifest.json").write_text("[]")
        sys.exit(0)
    print(f"      {len(methods)} method(s) found")

    # 5. Write per-method configs
    print("[5/5] Writing per-method clang-uml configs …")
    cfg_paths = write_method_configs(
        methods, build_dir, project_ns, extra_exc, out_dir, cfg_dir
    )

    manifest = cfg_dir / "manifest.json"
    manifest.write_text(json.dumps(cfg_paths, indent=2))

    print(f"\n✓  {len(cfg_paths)} config(s) written → {cfg_dir}")
    print(f"   Namespaces: {project_ns or ['(all non-external)']}")


if __name__ == "__main__":
    main()
