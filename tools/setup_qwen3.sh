#!/bin/bash
# Builds the Qwen3-TTS library (qwentts.cpp) into qwen3-tts/, for running the Qwen3
# engine from source before the qwen3-runtime workflow has published a download. The
# models download by themselves the first time the engine starts.
#
#   tools/setup_qwen3.sh            Vulkan build: AMD, NVIDIA and Intel GPUs
#   tools/setup_qwen3.sh cuda       NVIDIA only, needs the CUDA toolkit
#   tools/setup_qwen3.sh cpu        no GPU
#
# Needs git, cmake and a C++ compiler; Vulkan also needs its headers, SPIR-V headers and
# shader compiler (Arch: pacman -S cmake vulkan-headers spirv-headers shaderc).
set -e

cd "$(dirname "$0")/.."

BACKEND="${1:-vulkan}"
# The commit the engine's ctypes structs were written against.
QWENTTS_COMMIT=$(sed -n 's/^QWENTTS_COMMIT = "\([0-9a-f]*\)"$/\1/p' src/qwen_engine.py)
REPO_URL=https://github.com/ServeurpersoCom/qwentts.cpp

DEST=qwen3-tts
SRC="$DEST/src/qwentts.cpp"
mkdir -p "$DEST"

if [ ! -d "$SRC/.git" ]; then
    git clone --recurse-submodules "$REPO_URL" "$SRC"
fi
git -C "$SRC" fetch --quiet origin "$QWENTTS_COMMIT" || true
git -C "$SRC" checkout --quiet "$QWENTTS_COMMIT"
git -C "$SRC" submodule update --init --recursive --quiet

case "$BACKEND" in
    vulkan) FLAGS="-DGGML_VULKAN=ON" ;;
    cuda)   FLAGS="-DGGML_CUDA=ON" ;;
    cpu)    FLAGS="" ;;
    *) echo "Unknown backend '$BACKEND': use vulkan, cuda or cpu" >&2; exit 1 ;;
esac

# ggml linked statically, so libqwen is one self-contained file the app can load.
cmake -S "$SRC" -B "$SRC/build-$BACKEND" -DCMAKE_BUILD_TYPE=Release \
    -DQWEN_SHARED=ON -DBUILD_SHARED_LIBS=OFF -DCMAKE_POSITION_INDEPENDENT_CODE=ON $FLAGS
cmake --build "$SRC/build-$BACKEND" --config Release --target qwen -j "$(getconf _NPROCESSORS_ONLN)"
cp "$SRC/build-$BACKEND"/libqwen.so "$DEST/"

echo "Qwen3-TTS library ready in $DEST/."
