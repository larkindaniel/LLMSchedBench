#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
SIM_ROOT="${REPO_ROOT}/third_party/LLMServingSim"
PATCH="${REPO_ROOT}/patches/llmservingsim-custom-routing.patch"

if git -C "${SIM_ROOT}" apply --reverse --check "${PATCH}" 2>/dev/null; then
  echo "LLMServingSim CUSTOM-routing patch is already applied."
elif git -C "${SIM_ROOT}" apply --check "${PATCH}"; then
  git -C "${SIM_ROOT}" apply "${PATCH}"
  echo "Applied LLMServingSim CUSTOM-routing compatibility patch."
else
  echo "Patch cannot be applied cleanly; inspect the submodule worktree." >&2
  exit 1
fi
