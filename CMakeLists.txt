cmake_minimum_required(VERSION 3.16)
project(demo3 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# ─────────────────────────────────────────────────────────────────────────────
# THIS IS THE ONLY LINE YOU NEED TO ADD to your existing CMakeLists.txt.
#
# It tells CMake to write  build/compile_commands.json  during configuration.
# clang-uml reads that file to discover every source file, include path,
# and compiler flag in your project — no matter how complex the structure.
# ─────────────────────────────────────────────────────────────────────────────
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)


# ── Everything below is your normal CMakeLists.txt — no changes needed ───────

# Example: recursive source collection
file(GLOB_RECURSE SOURCES
    ".*.cpp"
    ".*.cc"
    ".*.cxx"
)

add_executable(${PROJECT_NAME} ${SOURCES})

target_include_directories(${PROJECT_NAME} PRIVATE
    include
    # add your other include dirs here
)