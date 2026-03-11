cmake_minimum_required(VERSION 3.16)
project(YourRepoName LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Required for clang-uml diagram generation
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# ── Scan recursively from repo root through ALL folders ───────────────────────
# Finds every .cpp/.cc/.cxx regardless of folder name or depth.
file(GLOB_RECURSE SOURCES
    "*.cpp"
    "*.cc"
    "*.cxx"
)

# Remove anything inside build/ so generated files are never picked up
list(FILTER SOURCES EXCLUDE REGEX ".*/build/.*")

# Safety check — tells you clearly if no .cpp files were found
if(NOT SOURCES)
    message(FATAL_ERROR
        "No .cpp files found anywhere under ${CMAKE_SOURCE_DIR}.\n"
        "Check that your source files exist outside the build/ directory."
    )
endif()

add_executable(${PROJECT_NAME} ${SOURCES})

# Auto-detect all header directories recursively
file(GLOB_RECURSE ALL_HEADERS "*.hpp" "*.h")
set(INCLUDE_DIRS "")
foreach(HEADER ${ALL_HEADERS})
    get_filename_component(DIR ${HEADER} DIRECTORY)
    list(APPEND INCLUDE_DIRS ${DIR})
endforeach()
list(REMOVE_DUPLICATES INCLUDE_DIRS)
target_include_directories(${PROJECT_NAME} PRIVATE ${INCLUDE_DIRS})