#!/bin/zsh
set -u

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec /bin/zsh "$PROJECT_DIR/.launch_bom_assistant.sh"
