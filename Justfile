default: test

pkg := "tools/compost"

# Install all dependencies (including dev extras)
sync:
    uv sync --project {{pkg}} --extra dev

# Run the full test suite
test:
    uv run --project {{pkg}} pytest {{pkg}}/tests/ -q

# Run tests with verbose output
test-verbose:
    uv run --project {{pkg}} pytest {{pkg}}/tests/ -v

# Run a specific test file or pattern
# Usage: just test-file tests/test_synth.py
test-file path:
    uv run --project {{pkg}} pytest {{path}} -v

# Run the compost CLI
run *args:
    uv run --project {{pkg}} compost {{args}}
