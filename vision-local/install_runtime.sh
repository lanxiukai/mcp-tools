#!/usr/bin/env bash
set -euo pipefail

# Build a repository-local CUDA llama-server. No system packages are installed.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUNTIME_DIR="${REPO_DIR}/.runtime"
SOURCE_DIR="${RUNTIME_DIR}/llama.cpp-src"
BUILD_DIR="${RUNTIME_DIR}/llama.cpp-build"
LLAMA_TAG="b9637"
LLAMA_COMMIT="aedb2a5e9ca3d4064148bbb919e0ddc0c1b70ab3"

mkdir -p "${RUNTIME_DIR}"

if [[ ! -d "${SOURCE_DIR}/.git" ]]; then
  git clone --depth 1 --branch "${LLAMA_TAG}" \
    https://github.com/ggml-org/llama.cpp.git "${SOURCE_DIR}"
fi

actual_commit="$(git -C "${SOURCE_DIR}" rev-parse HEAD)"
if [[ "${actual_commit}" != "${LLAMA_COMMIT}" ]]; then
  echo "Unexpected llama.cpp revision: ${actual_commit}" >&2
  echo "Expected ${LLAMA_COMMIT}; use a clean runtime directory." >&2
  exit 1
fi

cmake -S "${SOURCE_DIR}" -B "${BUILD_DIR}" \
  -DGGML_CUDA=ON \
  -DCMAKE_CUDA_ARCHITECTURES=89 \
  -DCMAKE_BUILD_TYPE=Release \
  -DLLAMA_CURL=OFF \
  -DGGML_NATIVE=ON

cmake --build "${BUILD_DIR}" --target llama-server --config Release -j "$(nproc)"
"${BUILD_DIR}/bin/llama-server" --version
