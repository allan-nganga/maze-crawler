#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV_SITE="${ROOT}/venv/lib/python*/site-packages/kaggle_environments/envs"
TMP_DIR="$(mktemp -d)"

cleanup() {
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

git clone --depth 1 --filter=blob:none --sparse https://github.com/Kaggle/kaggle-environments.git "${TMP_DIR}/kaggle-environments"
(
  cd "${TMP_DIR}/kaggle-environments"
  git sparse-checkout set kaggle_environments/envs/crawl
)

for envs_dir in ${VENV_SITE}; do
  rm -rf "${envs_dir}/crawl"
  cp -R "${TMP_DIR}/kaggle-environments/kaggle_environments/envs/crawl" "${envs_dir}/"
done

echo "Installed crawl environment into ${VENV_SITE}"
