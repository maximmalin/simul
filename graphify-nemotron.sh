#!/usr/bin/env bash
# Graphify with Nemotron Lightning via OpenAI-compatible Bridge
# Bridges Graphify to your desktop bridge at ai-infra

# ==========================================
# Configuration (from your desktop bridge .env)
# ==========================================

BASE_URL="${OPENAI_BASE_URL:-http://127.0.0.1:5000/v1}"
API_KEY="${OPENAI_API_KEY:-voltagent-bridge-2026}"
MODEL="${OPENAI_MODEL:-opencode/nemotron-3-5-lightning-free}"
OUTPUT_DIR="${GRAPH_OUTPUT_DIR:-graphify-out}"

# ==========================================
# Verify bridge is running
# ==========================================

verify_bridge() {
  echo "🔍 Verifying bridge at ${BASE_URL}..."
  if curl -s "${BASE_URL}/v1/models" -H "Authorization: ${API_KEY}" > /dev/null 2>&1; then
    echo "✅ Bridge operational - Nemotron model ready"
  else
    echo "❌ Bridge unreachable at ${BASE_URL}"
    echo "   Start it: node /home/mirrage/Desktop/ai-infra/bridge/bridge.js"
    exit 1
  fi
}

# ==========================================
# Ensure graphify + deps
# ==========================================

ensure_graphify() {
  if ! command -v graphify &> /dev/null; then
    echo "📦 Installing graphify with openai support..."
    uv tool install "graphifyy[openai]" --force 2>&1 | tail -1
  fi
}

# ==========================================
# Run graphify with env vars exported
# ==========================================

run() {
  echo "============================================"
  echo "Graphify + Nemotron Lightning"
  echo "============================================"
  echo "Bridge:  ${BASE_URL}"
  echo "Model:   ${MODEL}"
  echo "Output:  ${OUTPUT_DIR}/graph.json"
  echo "============================================"

  verify_bridge
  ensure_graphify

  # Run graphify with env vars exported
  OPENAI_BASE_URL="${BASE_URL}" \
  OPENAI_API_KEY="${API_KEY}" \
  OPENAI_MODEL="${MODEL}" \
  graphify "$@"
}

# ==========================================
# Dispatch
# ==========================================

if [ $# -lt 1 ]; then
  echo "Usage: $0 <extract|tree|html|label|query|god-nodes|affected|prs> [path/args...]"
  echo ""
  echo "Commands:"
  echo "  extract [path]    Extract code graph with Nemotron semantic analysis"
  echo "  tree              Generate D3 collapsible tree HTML visualization"
  echo "  html              Export interactive graph.html (browser-local, no server)"
  echo "  label             (re)name communities with LLM"
  echo "  query <q>         BFS traversal question answer"
  echo "  god-nodes         List architectural hubs (top connected nodes)"
  echo "  affected          Find nodes impacted by changes"
  echo "  prs               PR dashboard CI status"
  echo ""
  echo "Examples:"
  echo "  $0 extract          # Extract code graph from ."
  echo "  $0 extract . --force  # Force re-extract"
  echo "  $0 tree             # Open tree visualization"
  echo "  $0 html             # Export interactive HTML"
  exit 1
fi

run "$@"