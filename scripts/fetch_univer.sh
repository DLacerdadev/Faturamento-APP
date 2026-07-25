#!/usr/bin/env sh
# Baixa o bundle self-hosted do Univer usado na planilha de faturamento (spec 009).
# O bundle (~20 MB) NÃO é versionado (gitignored) — rode este script no setup/deploy.
# Enquanto não baixado, a planilha cai num fallback de tabela HTML (backend intacto).
set -e
VER="0.4.2"
DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)/app/static/vendor/univer"
BASE="https://cdn.jsdelivr.net/npm/@univerjs/umd@${VER}/lib"
mkdir -p "$DIR"
echo "Baixando Univer ${VER} para $DIR ..."
curl -sSL "$BASE/univer.full.umd.js" -o "$DIR/univer.full.umd.js"
curl -sSL "$BASE/univer.css"          -o "$DIR/univer.css"
echo "OK:"
ls -lh "$DIR"
