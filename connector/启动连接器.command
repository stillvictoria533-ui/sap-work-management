#!/bin/zsh
set -eu
cd -- "${0:A:h}"
# Finder does not inherit the terminal Node path. Use only the user's installed runtime.
if ! command -v node >/dev/null 2>&1; then
  if [[ -f "$HOME/.nvm/nvm.sh" ]]; then
    source "$HOME/.nvm/nvm.sh"
  fi
fi
if ! command -v node >/dev/null 2>&1; then
  print '未找到 Node.js。此 Mac 原型需要当前已配置 sap-eyes 的运行环境。'
  read -r '?按回车关闭。'
  exit 1
fi
node ./connector.mjs || true
read -r '?按回车关闭。'
