cmake_minimum_required(VERSION 3.19)
project(t2m_engine VERSION 1.0.0 LANGUAGES CXX C)

# Vulkan is the only backend. There is no CUDA language, no HIP language, no
# CPU-only configuration to fall out of: if ggml-vulkan is not built, this
# configure step fails instead of quietly producing a binary that runs on the
# CPU for twenty minutes.

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

set(T2M_ENGINE_VERSION "${PROJECT_VERSION}")
string(TIMESTAMP T2M_BUILD_TIMESTAMP_UTC "%Y-%m-%dT%H:%M:%SZ" UTC)

# --- ggml ------------------------------------------------------------------
# DiT and sparse tensor names run past the default 64-char limit.
set(GGML_DIR ${CMAKE_SOURCE_DIR}/thirdparty/ggml)
add_compile_definitions(GGML_MAX_NAME=128)
set(GGML_VULKAN ON  CACHE BOOL "" FORCE)
set(GGML_CUDA   OFF CACHE BOOL "" FORCE)
set(GGML_HIP    OFF CACHE BOOL "" FORCE)
set(GGML_METAL  OFF CACHE BOOL "" FORCE)
set(GGML_OPENCL OFF CACHE BOOL "" FORCE)
set(GGML_SYCL   OFF CACHE BOOL "" FORCE)
set(GGML_BUILD_TESTS    OFF CACHE BOOL "" FORCE)
set(GGML_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR})
set(CMAKE_LIBRARY_OUTPUT_DIRECTORY ${CMAKE_BINARY_DIR})
add_subdirectory(thirdparty/ggml)
set(CMAKE_INSTALL_RPATH "$ORIGIN")

if(NOT TARGET ggml-vulkan)
  message(FATAL_ERROR
    "ggml-vulkan was not built. This engine is Vulkan-only; there is no CPU or "
    "CUDA fallback. Install the Vulkan SDK headers (libvulkan-dev, glslc, "
    "spirv-headers) and reconfigure.")
endif()
find_package(Vulkan REQUIRED COMPONENTS glslc)

# --- compute shaders -> SPIR-V C arrays ------------------------------------
# Both shaders need 64-bit atomics, so vulkan1.2 is the floor.
function(t2m_compile_shader NAME)
  set(OUT ${CMAKE_CURRENT_BINARY_DIR}/${NAME}_comp.h)
  add_custom_command(
    OUTPUT ${OUT}
    COMMAND ${Vulkan_GLSLC_EXECUTABLE} -O -mfmt=c --target-env=vulkan1.2
            -o ${OUT} ${CMAKE_CURRENT_SOURCE_DIR}/src/${NAME}.comp
    DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/src/${NAME}.comp
    COMMENT "glslc ${NAME}.comp -> SPIR-V")
  add_custom_target(t2m_${NAME}_spv DEPENDS ${OUT})
endfunction()
t2m_compile_shader(deform_conv)
t2m_compile_shader(decimate_qem)

# --- core ------------------------------------------------------------------
add_library(t2m_core STATIC
  src/trellis_model.cpp
  src/dit.cpp
  src/flow_runner.cpp
  src/ss_decoder.cpp
  src/mesh_glb.cpp
  src/dinov3.cpp
  src/sparse.cpp
  src/shape_decoder.cpp
  src/dual_grid.cpp
  src/preprocess.cpp
  src/uv_bake.cpp
  src/birefnet.cpp
  src/tri_bvh.cpp
  src/remesh_dc.cpp
  src/decimate_qem.cpp
  src/decimate_qem_vk.cpp
  src/deform_conv_cpu.cpp
  src/deform_conv_vk.cpp
  thirdparty/xatlas/xatlas.cpp
  thirdparty/meshoptimizer/simplifier.cpp
)
target_include_directories(t2m_core PUBLIC
  include
  ${GGML_DIR}/include
  ${CMAKE_CURRENT_SOURCE_DIR}/thirdparty/stb
  ${CMAKE_CURRENT_SOURCE_DIR}/thirdparty/xatlas
  ${CMAKE_CURRENT_SOURCE_DIR}/thirdparty/meshoptimizer
  ${CMAKE_CURRENT_SOURCE_DIR}/thirdparty/fqms)
target_include_directories(t2m_core PRIVATE ${CMAKE_CURRENT_BINARY_DIR})
target_link_libraries(t2m_core PUBLIC ggml ggml-base ggml-cpu ggml-vulkan Vulkan::Vulkan)
add_dependencies(t2m_core t2m_deform_conv_spv t2m_decimate_qem_spv)

# deform_conv_cpu.cpp and decimate_qem.cpp are dispatchers: these definitions
# switch them onto the Vulkan implementations and keep the CPU code as the
# fallback the dispatcher takes only when device init fails.
target_compile_definitions(t2m_core PRIVATE
  TRELLIS_HAVE_VK_DECIMATE
  TRELLIS_BUILD_BACKEND="vulkan"
  TRELLIS_GIT_COMMIT="${T2M_ENGINE_VERSION}"
  TRELLIS_GIT_DIRTY=0
  TRELLIS_BUILD_TIMESTAMP="${T2M_BUILD_TIMESTAMP_UTC}"
  TRELLIS_APP_VERSION="v${T2M_ENGINE_VERSION}")
set_source_files_properties(src/deform_conv_cpu.cpp PROPERTIES
  COMPILE_DEFINITIONS TRELLIS_DEFORM_VULKAN)
set_source_files_properties(src/deform_conv_vk.cpp PROPERTIES
  COMPILE_DEFINITIONS "TRELLIS_DEFORM_SPV_HEADER=\"deform_conv_comp.h\"")
set_source_files_properties(src/decimate_qem_vk.cpp PROPERTIES
  COMPILE_DEFINITIONS "TRELLIS_DECIMATE_SPV_HEADER=\"decimate_qem_comp.h\"")

# --- WebP textures ---------------------------------------------------------
# GLB textures ship as lossy WebP via EXT_texture_webp, which three.js reads
# through its GLTFLoader when the browser supports WebP (all current ones do).
option(T2M_WEBP "Encode GLB textures as WebP" ON)
if(T2M_WEBP)
  include(FetchContent)
  foreach(opt ANIM_UTILS CWEBP DWEBP GIF2WEBP IMG2WEBP VWEBP WEBPINFO WEBPMUX EXTRAS LIBWEBPMUX)
    set(WEBP_BUILD_${opt} OFF CACHE BOOL "" FORCE)
  endforeach()
  set(BUILD_SHARED_LIBS_SAVED ${BUILD_SHARED_LIBS})
  set(BUILD_SHARED_LIBS OFF)
  FetchContent_Declare(libwebp
    URL https://github.com/webmproject/libwebp/archive/refs/tags/v1.5.0.tar.gz
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE)
  FetchContent_MakeAvailable(libwebp)
  set(BUILD_SHARED_LIBS ${BUILD_SHARED_LIBS_SAVED})
  target_compile_definitions(t2m_core PRIVATE TRELLIS_HAVE_WEBP)
  target_link_libraries(t2m_core PUBLIC webp)
endif()

# --- executables -----------------------------------------------------------
find_package(Threads REQUIRED)

add_executable(t2m-cli src/trellis_cli_main.cpp src/trellis_cli.cpp src/trellis_args.cpp)
target_link_libraries(t2m-cli PRIVATE t2m_core Threads::Threads)
set_target_properties(t2m-cli PROPERTIES BUILD_RPATH "$ORIGIN")

# Resident server. Model load is a large fixed cost (tens of GB off disk into
# GTT); keeping one process alive across requests is the single biggest win for
# anything that generates more than one asset.
add_executable(t2m-server src/trellis-server.cpp src/trellis_cli.cpp src/trellis_args.cpp)
target_include_directories(t2m-server PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/thirdparty/cpp-httplib)
target_link_libraries(t2m-server PRIVATE t2m_core Threads::Threads)
set_target_properties(t2m-server PROPERTIES BUILD_RPATH "$ORIGIN")

message(STATUS "t2m engine ${T2M_ENGINE_VERSION}: Vulkan-only, targets t2m-cli t2m-server")
