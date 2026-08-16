#!/usr/bin/env bash
set -euo pipefail

# Build a repository-local CUDA llama-server. No system packages are installed.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUNTIME_DIR="${REPO_DIR}/.runtime"
SOURCE_DIR="${RUNTIME_DIR}/llama.cpp-src"
BUILD_DIR="${RUNTIME_DIR}/llama.cpp-build"
LLAMA_TAG="b10451"
LLAMA_COMMIT="10bf611e533d81f739128304991c5e133c6aebd8"
CUDA_NVCC_VERSION="13.3.73"
CUDA_CUBLAS_VERSION="13.6.1.10"
CUDA_CCCL_VERSION="13.3.3.4.1"

mkdir -p "${RUNTIME_DIR}"

if [[ "${VISION_LOCAL_CUDA_WHEEL_BUILD:-0}" != "1" ]] && ! command -v nvcc >/dev/null 2>&1; then
  if ! command -v uv >/dev/null 2>&1; then
    echo "nvcc is unavailable and uv is required for the isolated CUDA build environment." >&2
    exit 1
  fi
  exec uv run --isolated --no-project --python 3.12 \
    --with "nvidia-cuda-nvcc==${CUDA_NVCC_VERSION}" \
    --with "nvidia-cublas==${CUDA_CUBLAS_VERSION}" \
    --with "nvidia-cuda-cccl==${CUDA_CCCL_VERSION}" \
    env VISION_LOCAL_CUDA_WHEEL_BUILD=1 bash "$0" "$@"
fi

if [[ "${VISION_LOCAL_CUDA_WHEEL_BUILD:-0}" == "1" ]]; then
  nvcc_site="$(python -c 'import importlib.metadata as m; print(m.distribution("nvidia-cuda-nvcc").locate_file(""))')"
  cublas_site="$(python -c 'import importlib.metadata as m; print(m.distribution("nvidia-cublas").locate_file(""))')"
  cccl_site="$(python -c 'import importlib.metadata as m; print(m.distribution("nvidia-cuda-cccl").locate_file(""))')"
  nvcc_root="${nvcc_site}/nvidia/cu13"
  cublas_root="${cublas_site}/nvidia/cu13"
  cccl_root="${cccl_site}/nvidia/cu13"
  cuda_stage="$(mktemp -d /tmp/vision-local-cuda.XXXXXX)"
  trap 'rm -rf -- "${cuda_stage}"' EXIT

  mkdir -p "${cuda_stage}/include" "${cuda_stage}/lib64"
  cp -a "${nvcc_root}/include/." "${cuda_stage}/include/"
  cp -a "${cublas_root}/include/." "${cuda_stage}/include/"
  cp -a "${cccl_root}/include/." "${cuda_stage}/include/"
  ln -s "${nvcc_root}/bin" "${cuda_stage}/bin"
  ln -s "${nvcc_root}/nvvm" "${cuda_stage}/nvvm"
  for file in "${nvcc_root}"/lib/* "${cublas_root}"/lib/*; do
    target="${cuda_stage}/lib64/$(basename "${file}")"
    if [[ ! -e "${target}" ]]; then
      ln -s "${file}" "${target}"
    fi
  done
  ln -s "${nvcc_root}/lib/libcudart.so.13" "${cuda_stage}/lib64/libcudart.so"
  ln -s "${cublas_root}/lib/libcublas.so.13" "${cuda_stage}/lib64/libcublas.so"
  ln -s "${cublas_root}/lib/libcublasLt.so.13" "${cuda_stage}/lib64/libcublasLt.so"
  export PATH="${nvcc_root}/bin:${PATH}"
  export CUDACXX="${nvcc_root}/bin/nvcc"
  export CUDAToolkit_ROOT="${cuda_stage}"
  export LD_LIBRARY_PATH="${nvcc_root}/lib:${cublas_root}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
fi

if [[ ! -d "${SOURCE_DIR}/.git" ]]; then
  git clone --depth 1 --branch "${LLAMA_TAG}" \
    https://github.com/ggml-org/llama.cpp.git "${SOURCE_DIR}"
else
  if [[ -n "$(git -C "${SOURCE_DIR}" status --short)" ]]; then
    echo "Refusing to update a dirty llama.cpp runtime source tree." >&2
    exit 1
  fi
  git -C "${SOURCE_DIR}" fetch --depth 1 origin "refs/tags/${LLAMA_TAG}:refs/tags/${LLAMA_TAG}"
  git -C "${SOURCE_DIR}" checkout --detach "${LLAMA_COMMIT}"
fi

actual_commit="$(git -C "${SOURCE_DIR}" rev-parse HEAD)"
if [[ "${actual_commit}" != "${LLAMA_COMMIT}" ]]; then
  echo "Unexpected llama.cpp revision: ${actual_commit}" >&2
  echo "Expected ${LLAMA_COMMIT}; use a clean runtime directory." >&2
  exit 1
fi

runtime_library_dir="${VISION_LOCAL_CUDA_LIBRARY_PATH:-}"
if [[ -z "${runtime_library_dir}" ]]; then
  runtime_library_dir="$(find "${REPO_DIR}/environments/mcp-local-asr/.venv/lib" \
    -path '*/site-packages/nvidia/cu13/lib' -type d -print -quit 2>/dev/null || true)"
fi
build_rpath='$ORIGIN'
if [[ -n "${runtime_library_dir}" ]]; then
  build_rpath="${build_rpath};${runtime_library_dir}"
fi
build_rpath="${build_rpath};/usr/lib/wsl/lib"

cmake --fresh -S "${SOURCE_DIR}" -B "${BUILD_DIR}" \
  -DGGML_CUDA=ON \
  -DCMAKE_CUDA_ARCHITECTURES=89 \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON \
  "-DCMAKE_INSTALL_RPATH=${build_rpath}" \
  -DLLAMA_CURL=OFF \
  -DLLAMA_BUILD_UI=OFF \
  -DLLAMA_USE_PREBUILT_UI=OFF \
  -DGGML_NATIVE=ON

cmake --build "${BUILD_DIR}" --target llama-server --config Release --clean-first -j "$(nproc)"
"${BUILD_DIR}/bin/llama-server" --version
