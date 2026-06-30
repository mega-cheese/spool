#!/usr/bin/env bash
set -euo pipefail

mkdir -p outputs

# Scenario input files and stable output names
SCENARIOS=(
  "new_general_workability:data/new_general_workability.json"
  "new_no_decision:data/new_no_decision.json"
  "new_pricing_change:data/new_pricing_change.json"
  "new_vendor_selection:data/new_vendor_selection.json"
  "new_decision_reversal:data/new_decision_reversal.json"
  "new_messy_implicit:data/new_messy_implicit.json"
)

COMBINED_FILE="outputs/all_decisions_concat.txt"

# Clear combined output
: > "$COMBINED_FILE"

for ITEM in "${SCENARIOS[@]}"; do
  NAME="${ITEM%%:*}"
  INPUT="${ITEM#*:}"

  STABLE_OUTPUT="outputs/decisions_run_${NAME}.json"

  echo "Running scenario: $NAME"
  python3 extract_decisions.py "$INPUT" outputs/decisions.json --mode llm --verify --pretty

  # Save/overwrite stable per-scenario file
  cp outputs/decisions.json "$STABLE_OUTPUT"

  # Pure concatenation into one big file, in order
  cat "$STABLE_OUTPUT" >> "$COMBINED_FILE"
  printf '\n\n' >> "$COMBINED_FILE"
done

echo ""
echo "Done."
echo "Stable scenario outputs:"
for ITEM in "${SCENARIOS[@]}"; do
  NAME="${ITEM%%:*}"
  echo "  outputs/decisions_run_${NAME}.json"
done

echo ""
echo "Combined concatenated file:"
echo "  $COMBINED_FILE"
