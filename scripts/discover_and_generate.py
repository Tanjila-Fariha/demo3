#!/usr/bin/env python3
"""
discover_and_generate.py
========================
Zero-configuration clang-uml orchestrator.

Works on ANY C++ project. Drop it in and run — no namespace names,
no method signatures, no source paths to configure.

What this script does
─────────────────────
  1. Reads  compile_commands.json  (written by CMake with
     CMAKE_EXPORT_COMPILE_COMMANDS=ON) to find EVERY .cpp/.cc/.cxx
     file the project actually compiles — handles any directory depth.

  2. Recursively scans those source files with a regex pass to
     detect the project's own C++ namespaces automatically.
     Filters out well-known external namespaces (std, boost, Qt …).

  3. Writes a "probe" clang-uml config that covers the entire
     project source tree and runs:
         clang-uml --print-from
     to get a complete list of every callable method/function that
     clang-uml found after parsing all translation units.

  4. Filters that list to methods belonging to the detected
     project namespaces (or, if no namespaces are found,
     keeps everything that isn't obviously external).

  5. Writes one small .yaml config per method into --cfg-dir and
     writes a manifest.json listing every config path so the
     CI workflow can iterate over them.

Usage (invoked by GitHub Actions):
    python3 discover_and_generate.py \
        --build-dir  build                \
        --out-dir    docs/diagrams/puml   \
        --cfg-dir    .clang-uml-generated

Optional flags:
    --exclude-ns NS   namespace to exclude in addition to the built-in
                      exclusion list (repeat for multiple)
    --min-calls  N    skip methods with fewer than N call sites (default 1)
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path


# ──────────────────────────────────────────────────────────────────────────────
# Built-in external namespace exclusion list.
# Anything that starts with one of these is considered a library dependency
# and will not be diagrammed.
# ──────────────────────────────────────────────────────────────────────────────
EXTERNAL_NAMESPACES = {
    # C++ standard library
    "std", "stdext", "__gnu_cxx", "__cxxabiv1", "__detail",
    "__atomic", "__future", "__regex", "__locale",
    # Common libraries
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

# Matches:  namespace foo {
#           namespace foo::bar {
#           inline namespace detail {
_NS_DECL = re.compile(
    r'^\s*(?:inline\s+)?namespace\s+([\w][\w:]*)\s*(?:\{|$)'
)

# Matches signatures returned by clang-uml --print-from:
#   - function: "myproject::Foo::bar(int, std::string)"
_SIG_LINE = re.compile(r'"([^"]+)"')


# ──────────────────────────────────────────────────────────────────────────────
# Helper: run a subprocess, return CompletedProcess (never raises on non-zero)
# ──────────────────────────────────────────────────────────────────────────────
def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


# ──────────────────────────────────────────────────────────────────────────────
# STEP 1  Read compile_commands.json — finds all compiled source files
#         regardless of how deeply nested the project structure is.
# ──────────────────────────────────────────────────────────────────────────────
def load_source_files(build_dir: Path) -> list[Path]:
    cc_path = build_dir / "compile_commands.json"
    if not cc_path.exists():
        print(f"\n[ERROR] {cc_path} not found.", file=sys.stderr)
        print("  Make sure your CMakeLists.txt contains:", file=sys.stderr)
        print("      set(CMAKE_EXPORT_COMPILE_COMMANDS ON)", file=sys.stderr)
        print("  and that cmake has been run at least once.", file=sys.stderr)
        sys.exit(1)

    with cc_path.open() as f:
        entries = json.load(f)

    files = []
    seen  = set()
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
# STEP 2  Auto-detect the project's own namespaces.
#         Recursively reads every source file listed in compile_commands.json
#         and counts namespace declarations.  External namespaces are excluded.
# ──────────────────────────────────────────────────────────────────────────────
def detect_namespaces(source_files: list[Path],
                      extra_exclude: set[str]) -> list[str]:
    exclude = EXTERNAL_NAMESPACES | extra_exclude
    counts: dict[str, int] = {}

    for src in source_files:
        try:
            text = src.read_text(errors="replace")
        except OSError:
            continue

        for line in text.splitlines():
            m = _NS_DECL.match(line)
            if not m:
                continue
            # Take only the top-level part (e.g. "foo" from "foo::bar")
            top = m.group(1).split("::")[0]
            if top and not top.startswith("_") and top not in exclude:
                counts[top] = counts.get(top, 0) + 1

    # Return namespaces sorted by usage frequency (most common first)
    return sorted(counts, key=lambda n: -counts[n])


# ──────────────────────────────────────────────────────────────────────────────
# STEP 3  Find the common source root.
#         This is the deepest directory that is an ancestor of every compiled
#         source file and still lives inside the project working directory.
#         Works correctly even for projects with sources spread across
#         src/, lib/, modules/, components/, etc.
# ──────────────────────────────────────────────────────────────────────────────
def find_source_root(source_files: list[Path], cwd: Path) -> Path:
    if not source_files:
        return cwd

    # Find the common prefix of all source file paths
    parts_list = [p.parts for p in source_files]
    common_parts = []
    for candidates in zip(*parts_list):
        if len(set(candidates)) == 1:
            common_parts.append(candidates[0])
        else:
            break

    if not common_parts:
        return cwd

    common = Path(*common_parts)
    # Must be a directory and must be inside (or equal to) cwd
    try:
        common.relative_to(cwd)
        if common.is_dir():
            return common
    except ValueError:
        pass

    return cwd


# ──────────────────────────────────────────────────────────────────────────────
# STEP 3b  Also collect ALL unique directories that contain source files.
#          clang-uml's `paths` filter accepts a list of directories, so we
#          can give it every directory in the project to handle non-standard
#          layouts (monorepos, component trees, etc.).
# ──────────────────────────────────────────────────────────────────────────────
def collect_source_dirs(source_files: list[Path], cwd: Path) -> list[Path]:
    """
    Return unique parent directories of all source files that are inside cwd,
    deduplicated so that if both  src/  and  src/core/  are present we only
    keep the shortest prefix (src/).
    """
    raw_dirs = sorted({p.parent for p in source_files
                       if str(p).startswith(str(cwd))})

    # Remove directories that are sub-directories of another already in the set
    pruned: list[Path] = []
    for d in raw_dirs:
        if not any(str(d).startswith(str(other) + os.sep)
                   for other in pruned):
            pruned.append(d)

    return pruned if pruned else [cwd]


# ──────────────────────────────────────────────────────────────────────────────
# STEP 4  Write the "probe" clang-uml config and run --print-from to get
#         the full list of callable methods in the project.
# ──────────────────────────────────────────────────────────────────────────────
PROBE_TEMPLATE = """\
compilation_database_dir: {build_dir}
output_directory: /tmp/_clang_uml_probe_out_
diagrams:
  _probe_:
    type: sequence
    generate_method_arguments: none
    include:
      paths:
{path_lines}
    exclude:
      namespaces:
{excl_lines}
    from:
      - function: ".*"
"""


def write_probe_config(build_dir: Path,
                       source_dirs: list[Path],
                       extra_exclude: set[str],
                       cfg_path: Path):
    path_lines = "\n".join(f"        - {d}" for d in source_dirs)
    excl_lines = "\n".join(
        f"        - {ns}"
        for ns in sorted(EXTERNAL_NAMESPACES | extra_exclude)
    )
    cfg_path.write_text(PROBE_TEMPLATE.format(
        build_dir=str(build_dir),
        path_lines=path_lines,
        excl_lines=excl_lines,
    ))


def discover_methods(probe_cfg: Path,
                     project_namespaces: list[str]) -> list[str]:
    result = run(["clang-uml",
                  "--config", str(probe_cfg),
                  "--print-from", "-n", "_probe_"])

    seen: set[str] = set()
    methods: list[str] = []

    for line in result.stdout.splitlines():
        m = _SIG_LINE.search(line)
        if not m:
            continue
        sig = m.group(1).strip()
        if not sig or sig in seen:
            continue

        if project_namespaces:
            # Keep only methods whose FQN starts with a detected project namespace
            if not any(sig.startswith(ns + "::") for ns in project_namespaces):
                continue
        else:
            # Fallback: exclude anything that starts with a known external ns
            if any(sig.startswith(ns + "::") for ns in EXTERNAL_NAMESPACES):
                continue

        seen.add(sig)
        methods.append(sig)

    return methods


# ──────────────────────────────────────────────────────────────────────────────
# STEP 5  Write one clang-uml config per method.
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
    include:
      paths:
{path_lines}
    exclude:
      namespaces:
{excl_lines}
      access:
        - private
    from:
      - function: "{sig}"
"""


def _safe_id(sig: str) -> str:
    s = re.sub(r"<[^>]*>", "",  sig)    # strip template args
    s = re.sub(r"\([^)]*\)", "", s)     # strip parameter list
    s = re.sub(r"[^A-Za-z0-9_]", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return ("seq_" + s)[:120]


def _human_title(sig: str, project_ns: list[str]) -> str:
    t = sig
    # Strip the project namespace prefix to keep the title short
    for ns in sorted(project_ns, key=len, reverse=True):
        if t.startswith(ns + "::"):
            t = t[len(ns) + 2:]
            break
    # Simplify argument list to ()
    t = re.sub(r"\(.*\)$", "()", t)
    return t


def write_method_configs(methods: list[str],
                         build_dir: Path,
                         source_dirs: list[Path],
                         project_ns: list[str],
                         extra_exclude: set[str],
                         out_dir: str,
                         cfg_dir: Path) -> list[str]:
    path_lines = "\n".join(f"        - {d}" for d in source_dirs)
    excl_lines = "\n".join(
        f"        - {ns}"
        for ns in sorted(EXTERNAL_NAMESPACES | extra_exclude)
    )
    using_ns_block = (
        "using_namespace:\n" +
        "\n".join(f"  - {ns}" for ns in project_ns)
        if project_ns else ""
    )

    cfg_paths: list[str] = []
    name_counts: dict[str, int] = {}

    for sig in methods:
        base = _safe_id(sig)
        # Disambiguate overloads by appending a counter
        count         = name_counts.get(base, 0)
        name_counts[base] = count + 1
        unique_name   = f"{base}_{count:04d}"
        cfg_path      = cfg_dir / f"{unique_name}.yaml"

        cfg_path.write_text(METHOD_TEMPLATE.format(
            build_dir=str(build_dir),
            out_dir=out_dir,
            using_ns_block=using_ns_block,
            name=unique_name,
            title=_human_title(sig, project_ns),
            path_lines=path_lines,
            excl_lines=excl_lines,
            sig=sig.replace('"', '\\"'),
        ))
        cfg_paths.append(str(cfg_path))

    return cfg_paths


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(
        description="Zero-config clang-uml orchestrator for any C++ project"
    )
    ap.add_argument("--build-dir",   default="build",
                    help="Directory containing compile_commands.json  (default: build)")
    ap.add_argument("--out-dir",     default="docs/diagrams/puml",
                    help="Where clang-uml writes .puml files")
    ap.add_argument("--cfg-dir",     default=".clang-uml-generated",
                    help="Where this script writes per-method YAML configs")
    ap.add_argument("--exclude-ns",  action="append", default=[], metavar="NS",
                    help="Extra namespace to exclude (repeat for multiple)")
    ap.add_argument("--min-calls",   type=int, default=1,
                    help="Skip methods with fewer than N call sites  (default: 1)")
    args = ap.parse_args()

    cwd         = Path.cwd()
    build_dir   = Path(args.build_dir).resolve()
    out_dir     = str(Path(args.out_dir).resolve())
    cfg_dir     = Path(args.cfg_dir).resolve()
    extra_exc   = set(args.exclude_ns)

    cfg_dir.mkdir(parents=True, exist_ok=True)
    Path(args.out_dir).mkdir(parents=True, exist_ok=True)

    # ── 1. Load source files from compile_commands.json ───────────────────────
    print("[1/5] Reading compile_commands.json …")
    source_files = load_source_files(build_dir)
    if not source_files:
        print("      [WARN] No source files found in compile_commands.json")
        sys.exit(0)
    print(f"      {len(source_files)} source file(s) found across all directories")

    # ── 2. Detect project namespaces ──────────────────────────────────────────
    print("[2/5] Detecting project namespaces (recursive source scan) …")
    project_ns = detect_namespaces(source_files, extra_exc)
    if project_ns:
        print(f"      Detected: {', '.join(project_ns)}")
    else:
        print("      No namespaces detected — will include all non-external methods")

    # ── 3. Determine source directories ───────────────────────────────────────
    print("[3/5] Mapping source directory tree …")
    source_dirs = collect_source_dirs(source_files, cwd)
    for d in source_dirs:
        print(f"      {d}")

    # ── 4. Probe clang-uml for all callable methods ───────────────────────────
    print("[4/5] Running clang-uml --print-from to discover all methods …")
    probe_cfg = cfg_dir / "_probe_.yaml"
    write_probe_config(build_dir, source_dirs, extra_exc, probe_cfg)
    methods = discover_methods(probe_cfg, project_ns)

    if not methods:
        print("      [WARN] No methods found after filtering.")
        print("             Ensure the project compiles cleanly and clang-uml")
        print("             can parse it (check build/compile_commands.json).")
        sys.exit(0)
    print(f"      {len(methods)} method(s) to diagram")

    # ── 5. Write one config per method ────────────────────────────────────────
    print("[5/5] Writing per-method clang-uml configs …")
    cfg_paths = write_method_configs(
        methods, build_dir, source_dirs,
        project_ns, extra_exc, out_dir, cfg_dir,
    )

    manifest = cfg_dir / "manifest.json"
    manifest.write_text(json.dumps(cfg_paths, indent=2))

    print(f"\n✓  {len(cfg_paths)} config(s) written → {cfg_dir}")
    print(f"   Manifest  → {manifest}")
    print(f"   Namespaces → {project_ns or ['(all non-external)']}")


if __name__ == "__main__":
    main()
