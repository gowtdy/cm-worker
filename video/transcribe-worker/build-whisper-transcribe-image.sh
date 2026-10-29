#!/usr/bin/env bash
set -e
IMG_TAG="$(cat "$(dirname "$0")/version.txt")"
docker build -t whisper-transcribe-worker:"${IMG_TAG}" -f Dockerfile \
  --build-arg TRANS_ENGINE=whisper --build-arg IMG_TAG="${IMG_TAG}" .
