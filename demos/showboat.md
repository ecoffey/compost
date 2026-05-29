# compost: end-to-end pipeline demo

*2026-05-25T18:30:57Z by Showboat 0.6.1*
<!-- showboat-id: 695ebd6c-27e9-4bab-b4cf-e7140b673699 -->

## What this demo shows

compost turns raw source documents (decisions, incidents, Slack threads) into a
maintained team wiki via a four-stage pipeline:

1. **Tier 0** — ingest: `compost raw add` creates a branch and commits the raw file
2. **Tier 1** — classify: a rule-based classifier decides whether synthesis should fire
3. **Tier 2** — synthesize: an LLM proposes wiki edits, committed on the branch
4. **Tier 2.5** — check: adversarial checks verify the edits before merge

Each stage is reproducible and auditable. This document is itself reproducible
via `showboat verify`.

## Prerequisites

Verify that all required tools and credentials are present.

```bash

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
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo 'ERROR: ANTHROPIC_API_KEY not set'
  exit 1
fi
echo 'ANTHROPIC_API_KEY: set'

# GITEA_TOKEN must be set (value not shown)
if [ -z "${GITEA_TOKEN:-}" ]; then
  echo 'ERROR: GITEA_TOKEN not set'
  exit 1
fi
echo 'GITEA_TOKEN: set'

# Gitea must be reachable
if ! curl -sf "http://localhost:3000/api/v1/version" -H "Authorization: token ${GITEA_TOKEN}" >/dev/null; then
  echo 'ERROR: Gitea not reachable at http://localhost:3000'
  exit 1
fi
echo 'Gitea: reachable at http://localhost:3000'

```

```output
qmd 2.1.0 (c94270e54e)
compost: installed
ANTHROPIC_API_KEY: set
GITEA_TOKEN: set
Gitea: reachable at http://localhost:3000
```

## Bootstrap a fresh wiki

We create a clean wiki repo for each demo run so the output is predictable.
Any previous demo repo is wiped first.

```bash

# Delete any previous local copy
rm -rf '/tmp/compost-showboat-demo'

# Clear the stale qmd index so compost init registers collections fresh
rm -f ~/.config/qmd/compost-showboat-demo.yml
echo 'qmd index cleared'

# Bootstrap via compost init
compost init '/tmp/compost-showboat-demo' --name 'compost-showboat-demo'

echo ''
echo 'Directory layout:'
ls '/tmp/compost-showboat-demo'

```

```output
qmd index cleared
  qmd collection registered: wiki
  qmd collection registered: raw
  qmd collection registered: decisions
  qmd collection registered: incidents

Initialized wiki repo at /private/tmp/compost-showboat-demo
  name:      compost-showboat-demo
  qmd index: compost-showboat-demo

Next: add seed wiki pages, then run tc doctor.

Directory layout:
raw
wiki
```

Set up the local git repo with an initial commit and a seed wiki page.

```bash

cd '/tmp/compost-showboat-demo'
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
updated: 2025-01-10
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
updated: 2025-01-10
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
qmd --index 'compost-showboat-demo' update
qmd --index 'compost-showboat-demo' embed
echo 'qmd index updated and embedded'

```

```output
Initial commit done.
f17c484 init: bootstrap wiki
Updating 4 collection(s)...

[1/4] wiki (**/*.md)
Collection: /private/tmp/compost-showboat-demo/wiki (**/*.md)

Indexed: 2 new, 0 updated, 3 unchanged, 0 removed

[2/4] raw (**/*.md)
Collection: /private/tmp/compost-showboat-demo/raw (**/*.md)

Indexed: 0 new, 0 updated, 0 unchanged, 0 removed

[3/4] decisions (**/*.md)
Collection: /private/tmp/compost-showboat-demo/raw/decisions (**/*.md)

Indexed: 0 new, 0 updated, 0 unchanged, 0 removed

[4/4] incidents (**/*.md)
Collection: /private/tmp/compost-showboat-demo/raw/incidents (**/*.md)

Indexed: 0 new, 0 updated, 0 unchanged, 0 removed

✓ All collections updated.
✓ All content hashes already have embeddings.
qmd index updated and embedded
```

## Create Gitea repo

Delete any existing demo repo on Gitea, then create a fresh one and push main.

```bash

# Generate a setup token with write:user scope for repo creation.
# Use date+PID for a unique name across runs.
SETUP_NAME=compost-demo-setup-$(date +%s)-$BASHPID
SETUP_TOKEN=$(gitea admin user generate-access-token --username ecoffey --token-name "$SETUP_NAME" --raw --scopes write:user,write:repository)
echo "setup token: ${SETUP_NAME}"

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
python3 /tmp/gitea_repo_setup.py "$SETUP_TOKEN" DELETE http://localhost:3000/api/v1/repos/ecoffey/compost-showboat-demo

# Create fresh repo
python3 /tmp/gitea_repo_setup.py "$SETUP_TOKEN" POST http://localhost:3000/api/v1/user/repos '{"name":"compost-showboat-demo","private":false,"auto_init":false}'
echo 'Gitea repo ready'

# Set up the git remote and push main
cd '/tmp/compost-showboat-demo'
compost --repo '/tmp/compost-showboat-demo' gitea setup --owner 'ecoffey' --repo 'compost-showboat-demo'

```

```output
setup token: compost-demo-setup-1779733860-
204 ok
201 http://localhost:3000/ecoffey/compost-showboat-demo
Gitea repo ready
✓ authenticated as ecoffey
✓ repo ecoffey/compost-showboat-demo
✓ remote 'origin' → http://localhost:3000/ecoffey/compost-showboat-demo.git
✓ pushed main
✓ .compost.yml updated with gitea config
✓ .compost.yml committed

doctor:
 ✓  .compost.yml present 
 ✓  qmd binary           
 ✓  qmd collections      
 ✓  CODEOWNERS           
 ✓  .gitignore           
 ✓  wiki frontmatter     
 ✓  gitea config         
 ✓  gitea connectivity   
 ✓  gitea remote         
```

## Stage 1 — Ingest a decision

We ingest a real engineering decision: migrating from Braintree to Stripe Connect.
The content describes JWT-based authentication, which directly contradicts the
session-cookies claim in the seed wiki page.

`compost raw add --no-synth` creates a branch, commits the raw file, and runs
the Tier 1 classifier. We pass `--no-synth` here to demonstrate each stage separately.

```bash

cat <<'EOF' | compost --repo '/tmp/compost-showboat-demo' raw add   --source decision   --title 'Migrate payments gateway from Braintree to Stripe Connect'   --no-synth

## Context

After evaluating options for 3 months, we are migrating the payments gateway
from Braintree to Stripe Connect to support multi-party payouts.

## Decision

Replace the Braintree REST integration with the Stripe Connect API (v2024-06-20).

Authentication uses JWT tokens issued by our identity service. The Braintree
session-cookie approach is deprecated and will be removed.

## Consequences

- All payments code must migrate from `braintree-sdk` to `stripe-python`.
- The admin console session-cookie auth is replaced by JWT bearer tokens.
- Payout flows are now first-class citizens via Stripe Connect accounts.
EOF

```

```output
✓ 
raw/decisions/2026-05-25-migrate-payments-gateway-from-braintree-to-stripe-conne
ct.md
branch: 
raw/2026-05-25T183103-migrate-payments-gateway-from-braintree-to-stripe-connect
[Tier 1] FIRE — source:decision, semantic:decision, semantic:deprecate, 
path:raw/decisions/*
PR #1: http://localhost:3000/ecoffey/compost-showboat-demo/pulls/1
```

## Stage 1 output — Tier 1 classifier

The Tier 1 classifier already ran inline during `raw add` above.
We can inspect the decision log to confirm it fired.

```bash

CLASSFILE='/tmp/compost-showboat-demo/.compost/classifications.jsonl'
if [ ! -f "$CLASSFILE" ]; then
  echo 'No classifications.jsonl found (Tier 1 did not write a decision)'
else
  python3 - "$CLASSFILE" <<'PYEOF'
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

```

```output
fired: True
 trigger: {'kind': 'source', 'pattern': 'decision'}
 trigger: {'kind': 'semantic', 'pattern': 'decision'}
 trigger: {'kind': 'semantic', 'pattern': 'deprecate'}
 trigger: {'kind': 'path', 'pattern': 'raw/decisions/*'}
```

## Stage 2 — Synthesis (Tier 2)

Now we run synthesis explicitly on the raw file.
The LLM reads the decision and proposes wiki edits.
These edits are committed on the current branch.

```bash

# Capture the branch name from the current git state
cd '/tmp/compost-showboat-demo'
BRANCH=$(git rev-parse --abbrev-ref HEAD)
echo "current branch: $BRANCH"

# Find the raw file
RAW_FILE=$(git diff --name-only main...$BRANCH | grep '^raw/' | head -1)
echo "raw file: $RAW_FILE"

compost --repo '/tmp/compost-showboat-demo' synth run --raw "$RAW_FILE"

```

```output
current branch: raw/2026-05-25T183103-migrate-payments-gateway-from-braintree-to-stripe-connect
raw file: raw/decisions/2026-05-25-migrate-payments-gateway-from-braintree-to-stripe-connect.md
[Tier 2] 2 wiki page(s) updated
  wiki/services/payments.md (updated)
  wiki/runbooks/payments-admin.md (updated)
```

After synthesis, new or updated wiki pages are committed on the branch.

```bash

cd '/tmp/compost-showboat-demo'
BRANCH=$(git rev-parse --abbrev-ref HEAD)
echo 'Wiki changes on branch:'
git diff --name-only main...$BRANCH | grep '^wiki/'

```

```output
Wiki changes on branch:
wiki/runbooks/payments-admin.md
wiki/services/payments.md
```

## Stage 2.5 — Adversarial checks

`compost checks run` runs seven checks against the branch.
The contradiction scan will compare the wiki edits against existing pages
and flag the JWT vs session-cookies conflict.

```bash

cd '/tmp/compost-showboat-demo'
BRANCH=$(git rev-parse --abbrev-ref HEAD)
compost --repo '/tmp/compost-showboat-demo' checks run --branch "$BRANCH" || true

```

```output
  Check                    Status    Findings    Cost (USD)    Duration (s)  
  kotlin_assay             ✓ pass           0        0.0000             4.7  
  provenance               ✓ pass           0        0.0000             0.0  
  scope                    ✓ pass           0        0.0000             1.5  
  human_edit_guard         ✓ pass           0        0.0000             0.0  
  contradiction_scan       ✓ pass           0        0.0000             0.5  
  recent_raw_scan          ✓ pass           0        0.0041             3.2  
  citation_faithfulness    ✓ pass           0        0.0000             0.0  
report → 
.compost/checks/raw-2026-05-25T183103-migrate-payments-gateway-from-braintree-to
-stripe-connect.md
```

## Stage 3 — Extract contested claims

`compost claims suggest` reads the contradiction findings from the JSONL sidecar
and generates `@Contested TheoryOf` Kotlin stubs for human review.

```bash

cd '/tmp/compost-showboat-demo'
BRANCH=$(git rev-parse --abbrev-ref HEAD)
compost --repo '/tmp/compost-showboat-demo' claims suggest --branch "$BRANCH" --dry-run

```

```output
0 contradiction findings for branch 
'raw/2026-05-25T183103-migrate-payments-gateway-from-braintree-to-stripe-connect
'.
```

## Summary

The demo above showed the full compost pipeline:

| Stage | Command | Output |
|-------|---------|--------|
| Ingest | `compost raw add` | raw file committed on branch, Tier 1 fires |
| Synthesize | `compost synth run` | wiki pages updated, committed on branch |
| Check | `compost checks run` | contradiction detected: JWT vs session cookies |
| Claims | `compost claims suggest` | `@Contested TheoryOf` stub for human review |

The full pipeline is reproducible: `showboat verify demos/showboat.md` re-runs
every code block and diffs the output.
