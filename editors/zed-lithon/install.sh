#!/usr/bin/env bash
# Install the Lithon language into Zed by linking this repo's pack into
# ~/.config/zed/languages/lithon/ — grammar, config and highlights track
# this repo, no copies. Restart Zed afterwards (or reload languages).
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DEST="$HOME/.config/zed/languages/lithon"
G="$REPO/editors/zed-lithon/grammars/lithon"
L="$REPO/editors/zed-lithon/languages/lithon"
mkdir -p "$DEST"
for f in grammar.js package.json tree-sitter.json; do
  rm -f "$DEST/$f"
  ln -sfn "$G/$f" "$DEST/$f"
done
for f in config.toml highlights.scm; do
  rm -f "$DEST/$f"
  ln -sfn "$L/$f" "$DEST/$f"
done
rm -rf "$DEST/src"
ln -sfn "$G/src" "$DEST/src"
echo "Lithon linked: $DEST -> $REPO/editors/zed-lithon"
echo "Restart Zed (or run 'zed: reload languages' if available), open a .lio file."
