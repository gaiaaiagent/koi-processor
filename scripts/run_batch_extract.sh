#!/bin/bash
# Batch / one-off deep-extraction with an ALTERNATE transport — NOT the daily process.
#
# The daily launchd job (com.personal-koi.substack-deep-extract etc.) runs on the
# default 'claude_p' transport and must stay that way. This wrapper lets you run a
# manual batch on a different model (e.g. a fast/free OpenAI-compatible endpoint) for a
# backfill, without touching the daily job or committing any endpoint/key.
#
# It sources config/personal.env, then — if present — an optional gitignored transport
# override at config/extract-batch.env (put your DOC_EXTRACTOR_* / DOC_EXTRACTOR_OPENAI_*
# there; see config/extract-batch.env.example), then execs the extraction command you
# pass. A .py first arg is run under the koi venv.
#
#   ./scripts/run_batch_extract.sh scripts/deep_extract_substack_corpus.sh
#   ./scripts/run_batch_extract.sh scripts/extract_deep_documents.py \
#       --document-rid <rid> --tier thorough --source-sensor <s>
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1
# Homebrew (psql) + ~/.local/bin (claude, if a batch still uses claude_p) on PATH.
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$PATH"
set -a
# shellcheck disable=SC1091
source config/personal.env
# shellcheck disable=SC1091
[ -f config/extract-batch.env ] && source config/extract-batch.env
set +a

if [ "$#" -eq 0 ]; then
  echo "usage: $0 <script.(sh|py)> [args...]   (transport from config/extract-batch.env)" >&2
  exit 2
fi
echo "[run_batch_extract] transport=${DOC_EXTRACTOR_TRANSPORT:-claude_p} model=${DOC_EXTRACTOR_OPENAI_MODEL:-${DOC_EXTRACTOR_MODEL:-default}}" >&2

first="$1"; shift
case "$first" in
  *.py) exec "${KOI_VENV:-/Users/darrenzal/venvs/koi-server}/bin/python" "$first" "$@" ;;
  *)    exec /bin/bash "$first" "$@" ;;
esac
