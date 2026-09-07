#!/usr/bin/env bash
# Put `linkctl` on your PATH without copying the project anywhere.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
target="${HOME}/.local/bin"
mkdir -p "$target"
ln -sf "${here}/linkctl-run" "${target}/linkctl"
echo "linked ${target}/linkctl -> ${here}/linkctl-run"
case ":${PATH}:" in
  *":${target}:"*) ;;
  *) echo "note: ${target} is not on your PATH; add it to ~/.zshrc" ;;
esac
