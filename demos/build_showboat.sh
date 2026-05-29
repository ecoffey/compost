#!/usr/bin/env bash
# Build demos/showboat.md using uvx showboat.
# Run from the repo root: bash demos/build_showboat.sh
set -euo pipefail

DEMO_FILE="demos/showboat.md"
DEMO_REPO="/tmp/compost-showboat-demo"
DEMO_NAME="compost-showboat-demo"
GITEA_URL="http://localhost:3000"
GITEA_OWNER="ecoffey"

SB="uvx showboat"

# ── helpers ───────────────────────────────────────────────────────────────────

note() { $SB note "$DEMO_FILE" "$1"; }
exec_step() { $SB exec "$DEMO_FILE" bash "$1"; }

# ── init ─────────────────────────────────────────────────────────────────────

rm -f "$DEMO_FILE"
$SB init "$DEMO_FILE" "compost: end-to-end pipeline demo"

note "## What this demo shows

compost turns raw source documents (decisions, incidents, Slack threads) into a
maintained team wiki via a four-stage pipeline:

1. **Tier 0** — ingest: \`compost raw add\` creates a branch and commits the raw file
2. **Tier 1** — classify: a rule-based classifier decides whether synthesis should fire
3. **Tier 2** — synthesize: an LLM proposes wiki edits, committed on the branch
4. **Tier 2.5** — check: adversarial checks verify the edits before merge

Each stage is reproducible and auditable. This document is itself reproducible
via \`showboat verify\`."

# ── prerequisites ─────────────────────────────────────────────────────────────

note "## Prerequisites"

note "Verify that all required tools and credentials are present."

exec_step "
# qmd must be on PATH
if ! command -v qmd &>/dev/null; then
  echo 'ERROR: qmd not found on PATH'
  exit 1
fi
qmd --version

# compost must be installed
if ! command -v compost &>/dev/null; then
  echo 'ERROR: compost not found on PATH'
  exit 1
fi
echo 'compost: installed'

# ANTHROPIC_API_KEY must be set (value not shown)
if [ -z \"\${ANTHROPIC_API_KEY:-}\" ]; then
  echo 'ERROR: ANTHROPIC_API_KEY not set'
  exit 1
fi
echo 'ANTHROPIC_API_KEY: set'

# GITEA_TOKEN must be set (value not shown)
if [ -z \"\${GITEA_TOKEN:-}\" ]; then
  echo 'ERROR: GITEA_TOKEN not set'
  exit 1
fi
echo 'GITEA_TOKEN: set'

# Gitea must be reachable
if ! curl -sf \"${GITEA_URL}/api/v1/version\" -H \"Authorization: token \${GITEA_TOKEN}\" >/dev/null; then
  echo 'ERROR: Gitea not reachable at ${GITEA_URL}'
  exit 1
fi
echo 'Gitea: reachable at ${GITEA_URL}'
"

# ── bootstrap fresh wiki ──────────────────────────────────────────────────────

note "## Bootstrap a fresh wiki

We create a clean wiki repo for each demo run so the output is predictable.
Any previous demo repo is wiped first."

exec_step "
# Delete any previous local copy
rm -rf '${DEMO_REPO}'

# Clear the stale qmd index so compost init registers collections fresh
rm -f ~/.config/qmd/${DEMO_NAME}.yml
echo 'qmd index cleared'

# Bootstrap via compost init
compost init '${DEMO_REPO}' --name '${DEMO_NAME}'

echo ''
echo 'Directory layout:'
ls '${DEMO_REPO}'
"

# ── git init and seed ─────────────────────────────────────────────────────────

note "Set up the local git repo with an initial commit and a seed wiki page."

exec_step "
cd '${DEMO_REPO}'
git init -q
git config user.email 'demo@compost'
git config user.name 'Demo'

# Seed wiki page: payments service referencing Braintree
# This gives the checks a surface to find a contradiction against.
mkdir -p wiki/services
mkdir -p wiki/runbooks
cat > wiki/runbooks/payments-admin.md << 'EOF'
---
title: Payments Admin Console Runbook
name: payments-admin
type: runbook
owners:
  - payments-team
status: active
updated: "2025-01-10"
sources:
  - raw/decisions/2025-01-10-payments-arch.md
confidence: high
---

# Payments Admin Console Runbook

## Prerequisites

You must have an active session cookie issued by the payments admin portal.
Session cookies expire after 8 hours.

## Access

1. Navigate to the Braintree admin console at https://sandbox.braintreegateway.com
2. Log in with your team credentials — the portal uses session cookie authentication
3. Your session is tied to the cookie; do not share it

## Troubleshooting

If you see 'Session expired', refresh your session cookie by logging in again.
EOF

cat > wiki/services/payments.md << 'EOF'
---
title: Payments Service
name: payments
type: service
owners:
  - payments-team
status: active
updated: "2025-01-10"
sources:
  - raw/decisions/2025-01-10-payments-arch.md
confidence: high
---

# Payments Service

The payments service processes all customer transactions. It uses
Braintree as the payment gateway via a REST integration.

Auth with the gateway uses session cookies for the admin console.
EOF

# Disable human-edit guard for the demo (no prior human edits to protect)
printf 'checks:\n  human_edit_days: 0\n' >> .compost.yml

git add -A
git commit -q -m 'init: bootstrap wiki'

echo 'Initial commit done.'
git log --oneline

# Index wiki content into qmd so synthesis can find context
qmd --index '${DEMO_NAME}' update
qmd --index '${DEMO_NAME}' embed
echo 'qmd index updated and embedded'
"

# ── gitea repo ────────────────────────────────────────────────────────────────

note "## Create Gitea repo

Delete any existing demo repo on Gitea, then create a fresh one and push main."

exec_step "
# Generate a setup token with write:user scope for repo creation.
# Use date+PID for a unique name across runs.
SETUP_NAME=compost-demo-setup-\$(date +%s)-\$BASHPID
SETUP_TOKEN=\$(gitea admin user generate-access-token --username ${GITEA_OWNER} --token-name \"\$SETUP_NAME\" --raw --scopes write:user,write:repository)
echo \"setup token: \${SETUP_NAME}\"

# Write python helper to a temp file to avoid heredoc quoting issues
cat > /tmp/gitea_repo_setup.py << 'PYEOF'
import json, sys, urllib.request, urllib.error
token, method, url, body_json = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else ''
data = json.dumps(json.loads(body_json)).encode() if body_json else None
req = urllib.request.Request(url, data=data, method=method, headers={
    'Authorization': f'token {token}',
    'Content-Type': 'application/json',
})
try:
    with urllib.request.urlopen(req) as r:
        body = r.read()
        d = json.loads(body) if body else {}
        print(r.status, d.get('html_url', 'ok'))
except urllib.error.HTTPError as e:
    print(e.code, e.read().decode()[:200])
    if method == 'POST': sys.exit(1)
PYEOF

# Delete existing repo (404 is fine)
python3 /tmp/gitea_repo_setup.py \"\$SETUP_TOKEN\" DELETE ${GITEA_URL}/api/v1/repos/${GITEA_OWNER}/${DEMO_NAME}

# Create fresh repo
python3 /tmp/gitea_repo_setup.py \"\$SETUP_TOKEN\" POST ${GITEA_URL}/api/v1/user/repos '{\"name\":\"${DEMO_NAME}\",\"private\":false,\"auto_init\":false}'
echo 'Gitea repo ready'

# Set up the git remote and push main
cd '${DEMO_REPO}'
compost --repo '${DEMO_REPO}' gitea setup --owner '${GITEA_OWNER}' --repo '${DEMO_NAME}'
"

# ── raw add ───────────────────────────────────────────────────────────────────

note "## Stage 1 — Ingest a decision

We ingest a real engineering decision: migrating from Braintree to Stripe Connect.
The content describes JWT-based authentication, which directly contradicts the
session-cookies claim in the seed wiki page.

\`compost raw add --no-synth\` creates a branch, commits the raw file, and runs
the Tier 1 classifier. We pass \`--no-synth\` here to demonstrate each stage separately."

exec_step "
cat <<'EOF' | compost --repo '${DEMO_REPO}' raw add \
  --source decision \
  --title 'Migrate payments gateway from Braintree to Stripe Connect' \
  --no-synth

## Context

After evaluating options for 3 months, we are migrating the payments gateway
from Braintree to Stripe Connect to support multi-party payouts.

## Decision

Replace the Braintree REST integration with the Stripe Connect API (v2024-06-20).

Authentication uses JWT tokens issued by our identity service. The Braintree
session-cookie approach is deprecated and will be removed.

## Consequences

- All payments code must migrate from \`braintree-sdk\` to \`stripe-python\`.
- The admin console session-cookie auth is replaced by JWT bearer tokens.
- Payout flows are now first-class citizens via Stripe Connect accounts.
EOF
"

# ── classify ──────────────────────────────────────────────────────────────────

note "## Stage 1 output — Tier 1 classifier

The Tier 1 classifier already ran inline during \`raw add\` above.
We can inspect the decision log to confirm it fired."

exec_step "
CLASSFILE='${DEMO_REPO}/.compost/classifications.jsonl'
if [ ! -f \"\$CLASSFILE\" ]; then
  echo 'No classifications.jsonl found (Tier 1 did not write a decision)'
else
  python3 - \"\$CLASSFILE\" <<'PYEOF'
import sys, json
with open(sys.argv[1]) as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        print('fired:', r.get('fired'))
        for t in r.get('triggers', []):
            print(' trigger:', t)
PYEOF
fi
"

# ── synth ─────────────────────────────────────────────────────────────────────

note "## Stage 2 — Synthesis (Tier 2)

Now we run synthesis explicitly on the raw file.
The LLM reads the decision and proposes wiki edits.
These edits are committed on the current branch."

exec_step "
# Capture the branch name from the current git state
cd '${DEMO_REPO}'
BRANCH=\$(git rev-parse --abbrev-ref HEAD)
echo \"current branch: \$BRANCH\"

# Find the raw file
RAW_FILE=\$(git diff --name-only main...\$BRANCH | grep '^raw/' | head -1)
echo \"raw file: \$RAW_FILE\"

compost --repo '${DEMO_REPO}' synth run --raw \"\$RAW_FILE\"
"

note "After synthesis, new or updated wiki pages are committed on the branch."

exec_step "
cd '${DEMO_REPO}'
BRANCH=\$(git rev-parse --abbrev-ref HEAD)
echo 'Wiki changes on branch:'
git diff --name-only main...\$BRANCH | grep '^wiki/'
"

# ── checks ────────────────────────────────────────────────────────────────────

note "## Stage 2.5 — Adversarial checks

\`compost checks run\` runs seven checks against the branch.
The contradiction scan will compare the wiki edits against existing pages
and flag the JWT vs session-cookies conflict."

exec_step "
cd '${DEMO_REPO}'
BRANCH=\$(git rev-parse --abbrev-ref HEAD)
compost --repo '${DEMO_REPO}' checks run --branch \"\$BRANCH\" || true
"

# ── claims suggest ────────────────────────────────────────────────────────────

note "## Stage 3 — Extract contested claims

\`compost claims suggest\` reads the contradiction findings from the JSONL sidecar
and generates \`@Contested TheoryOf\` Kotlin stubs for human review."

exec_step "
cd '${DEMO_REPO}'
BRANCH=\$(git rev-parse --abbrev-ref HEAD)
compost --repo '${DEMO_REPO}' claims suggest --branch \"\$BRANCH\" --dry-run
"

# ── summary ───────────────────────────────────────────────────────────────────

note "## Summary

The demo above showed the full compost pipeline:

| Stage | Command | Output |
|-------|---------|--------|
| Ingest | \`compost raw add\` | raw file committed on branch, Tier 1 fires |
| Synthesize | \`compost synth run\` | wiki pages updated, committed on branch |
| Check | \`compost checks run\` | contradiction detected: JWT vs session cookies |
| Claims | \`compost claims suggest\` | \`@Contested TheoryOf\` stub for human review |

The full pipeline is reproducible: \`showboat verify demos/showboat.md\` re-runs
every code block and diffs the output."

echo ""
echo "Built: $DEMO_FILE"
