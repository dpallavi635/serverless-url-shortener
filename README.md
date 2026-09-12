```
 ___       _
/ __| _ _ (_) _ __
\__ \| ' \| || '_ \
|___/|_||_|_|| .__/   Snip · a serverless URL shortener
             |_|
```

Paste a long link → get a short one → clicks get counted. That's the whole app.

Built to be **as small and cheap as an AWS app can be**: exactly **two services**,
no API Gateway, no S3, no CloudFront. One Lambda serves the HTML page, mints codes,
redirects visitors, and reports stats. One DynamoDB table stores the links and
expires them for free.

---

## Architecture

```
        ┌──────────────────────────────┐
 you ──▶ │  Lambda Function URL (free)  │
        │  serverless-url-shortener-app│
        │  • GET  /            → UI     │
        │  • POST /api/links   → create │
        │  • GET  /{code}      → 302    │──▶ target site
        │  • GET  /api/links/{code}→stats│
        └──────────────┬───────────────┘
                       │  ADD clicks :1 (atomic)
                       ▼
        ┌──────────────────────────────────┐
        │ DynamoDB serverless-url-shortener-│
        │          links                    │
        │ key: code   TTL: expires_at       │
        └──────────────────────────────────┘
```

Two services. That's it.

## Why this shape (cost)

| Choice | Instead of | Why |
|---|---|---|
| Lambda **Function URL** | API Gateway | Function URLs are free; API GW bills per request |
| Lambda **serves the HTML** | S3 + CloudFront | no bucket, no distribution, no OAC to manage |
| DynamoDB **on-demand** | provisioned | pay per request; idle cost ≈ $0 |
| DynamoDB **TTL** | scheduled cleanup Lambda | expiry deletes rows for free |
| **arm64**, 128 MB | x86 / bigger | cheapest Lambda config |
| Log retention **7 days** | never expire | no unbounded log-storage bill |

At low traffic this sits comfortably in the Free Tier.

## Stand it up — step by step (AWS CLI)

Run these from **this project folder** (`serverless-url-shortener/`). Region is
**us-east-1** throughout.

**Step 1 — prereqs.** AWS CLI + AWS SAM CLI installed, creds set for us-east-1:
```bash
aws configure                    # or: aws sso login --profile <name>
aws sts get-caller-identity      # confirm you're authenticated
```

**Step 2 — build.**
```bash
sam build
```

**Step 3 — deploy.** First time, use the guided flow (accept the defaults; set
region `us-east-1`, stack name `serverless-url-shortener`):
```bash
sam deploy --guided \
  --stack-name serverless-url-shortener \
  --region us-east-1 \
  --capabilities CAPABILITY_IAM
```
Later deploys are just `sam deploy`.

**Step 4 — grab the URL.** Read the `SiteUrl` output:
```bash
aws cloudformation describe-stacks \
  --stack-name serverless-url-shortener --region us-east-1 \
  --query "Stacks[0].Outputs[?OutputKey=='SiteUrl'].OutputValue" --output text
# → https://abc123.lambda-url.us-east-1.on.aws/
```

**Step 5 — use it.** Open that URL in a browser → paste a link → Shorten. Or API:
```bash
BASE="https://<your-function-url>"

# create
curl -s -X POST "$BASE/api/links" \
  -H 'content-type: application/json' \
  -d '{"url":"https://example.com/long/path","ttl_days":30}'
# → {"code":"Ab3xK9z","short_url":".../Ab3xK9z","url":"..."}

# visit (redirects, counts a click)
curl -sI "$BASE/Ab3xK9z"        # → 302 location: https://example.com/...

# stats
curl -s "$BASE/api/links/Ab3xK9z"
# → {"code":"Ab3xK9z","url":"...","clicks":1,"created_at":...,"expires_at":...}
```

## Tear it down — step by step (AWS CLI)

**Step 1 — delete the stack:**
```bash
aws cloudformation delete-stack \
  --stack-name serverless-url-shortener --region us-east-1
```

**Step 2 — wait for it to finish:**
```bash
aws cloudformation wait stack-delete-complete \
  --stack-name serverless-url-shortener --region us-east-1
```

**Step 3 — verify nothing survived** (all should error / return empty):
```bash
aws cloudformation describe-stacks --stack-name serverless-url-shortener --region us-east-1   # expect: does not exist
aws dynamodb describe-table --table-name serverless-url-shortener-links --region us-east-1    # expect: ResourceNotFound
aws logs describe-log-groups \
  --log-group-name-prefix /aws/lambda/serverless-url-shortener-app --region us-east-1 \
  --query "length(logGroups)"                                                                 # expect: 0
```

If deletion stalls on a resource, delete it in its own service console
(DynamoDB / Lambda / CloudWatch Logs / IAM), then re-run Step 1.

## Files

```
serverless-url-shortener/
├── template.yaml        # SAM: Lambda (Function URL) + DynamoDB + log group
├── src/app.py           # the whole app: UI + create + redirect + stats
└── README.md
```

Deploy and tear down with the AWS CLI steps above — no extra scripts needed.

—
Region: **us-east-1** · Stack: **serverless-url-shortener**
