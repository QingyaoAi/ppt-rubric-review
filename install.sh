#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="$SCRIPT_DIR/skills/ppt-rubric-review"
SKILL_NAME="ppt-rubric-review"
CLAUDE_DIR="$HOME/.claude/skills/$SKILL_NAME"

DO_CLAUDE=0
DO_ANTIGRAVITY=0
DO_CODEX=0
DO_DEEPSEEK=0

usage() {
  cat <<EOF
Usage: ./install.sh [--claude] [--antigravity] [--codex] [--deepseek] [--all]

Installs the canonical skill to ~/.claude/skills/$SKILL_NAME and optionally
symlinks it into Antigravity, Codex, and DeepSeek global skill directories.
Default (no flags): --claude
EOF
}

if [[ $# -eq 0 ]]; then
  DO_CLAUDE=1
fi

for arg in "$@"; do
  case "$arg" in
    --claude)      DO_CLAUDE=1 ;;
    --antigravity) DO_CLAUDE=1; DO_ANTIGRAVITY=1 ;;
    --codex)       DO_CLAUDE=1; DO_CODEX=1 ;;
    --deepseek)    DO_CLAUDE=1; DO_DEEPSEEK=1 ;;
    --all)         DO_CLAUDE=1; DO_ANTIGRAVITY=1; DO_CODEX=1; DO_DEEPSEEK=1 ;;
    -h|--help)     usage; exit 0 ;;
    *)             echo "Unknown option: $arg" >&2; usage; exit 1 ;;
  esac
done

link_skill() {
  local target_dir="$1"
  local label="$2"
  local link_path="$target_dir/$SKILL_NAME"
  mkdir -p "$target_dir"
  if [[ -L "$link_path" ]]; then
    ln -sfn "$CLAUDE_DIR" "$link_path"
  elif [[ -e "$link_path" ]]; then
    local backup="${link_path}.bak.$(date +%Y%m%d%H%M%S)"
    mv "$link_path" "$backup"
    echo "[$label] Backed up existing directory to $backup"
    ln -s "$CLAUDE_DIR" "$link_path"
  else
    ln -s "$CLAUDE_DIR" "$link_path"
  fi
  echo "[$label] Linked $link_path -> $CLAUDE_DIR"
}

if [[ $DO_CLAUDE -eq 1 ]]; then
  mkdir -p "$HOME/.claude/skills"
  rm -rf "$CLAUDE_DIR"
  cp -R "$SRC" "$CLAUDE_DIR"
  chmod +x "$CLAUDE_DIR/scripts/rubric.py"
  echo "[Claude Code] Installed to $CLAUDE_DIR"
fi

if [[ $DO_ANTIGRAVITY -eq 1 ]]; then
  link_skill "$HOME/.gemini/config/skills" "Antigravity"
fi

if [[ $DO_CODEX -eq 1 ]]; then
  link_skill "$HOME/.agents/skills" "Codex"
fi

if [[ $DO_DEEPSEEK -eq 1 ]]; then
  if [[ -L "$HOME/.dsh/skills" ]] && [[ "$(readlink "$HOME/.dsh/skills")" == "$HOME/.claude/skills" ]]; then
    echo "[DeepSeek] ~/.dsh/skills already symlinks to ~/.claude/skills (active automatically)"
  else
    link_skill "$HOME/.dsh/skills" "DeepSeek"
  fi
fi

echo "Done. Start a new agent session to activate '$SKILL_NAME'."
