"""
Serverless URL Shortener — single Lambda behind a Function URL.

Routes (all on one Function URL, method + path decide the action):
  GET  /                 -> serves the HTML UI
  POST /api/links        -> {"url": "...", "ttl_days": 30?} -> {"code","short_url"}
  GET  /api/links/{code} -> stats: {"code","url","clicks","created_at","expires_at"}
  GET  /{code}           -> 302 redirect to the target URL (atomic click++)

Backed by one DynamoDB table (key: code). Uses an atomic ADD to count clicks and
a TTL attribute (expires_at, epoch seconds) so expired links self-delete for free.
No API Gateway, no S3, no CloudFront — Lambda + DynamoDB only.
"""

import json
import os
import re
import secrets
import string
import time

import boto3
from botocore.exceptions import ClientError

TABLE_NAME = os.environ["TABLE_NAME"]
_table = boto3.resource("dynamodb").Table(TABLE_NAME)

ALPHABET = string.ascii_letters + string.digits
CODE_LEN = 7
URL_RE = re.compile(r"^https?://", re.IGNORECASE)
RESERVED = {"", "api", "favicon.ico", "robots.txt"}


# --------------------------------------------------------------------- helpers
def _json(status, body, extra_headers=None):
    headers = {"content-type": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    return {"statusCode": status, "headers": headers, "body": json.dumps(body)}


def _new_code():
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))


def _method_and_path(event):
    rc = event.get("requestContext", {}).get("http", {})
    return rc.get("method", "GET"), event.get("rawPath", "/") or "/"


def _base_url(event):
    """Reconstruct the public origin from the Function URL request headers."""
    headers = event.get("headers") or {}
    host = headers.get("host") or headers.get("Host") or ""
    proto = headers.get("x-forwarded-proto") or "https"
    return f"{proto}://{host}" if host else ""


# ----------------------------------------------------------------------- routes
def create_link(event):
    try:
        payload = json.loads(event.get("body") or "{}")
    except json.JSONDecodeError:
        return _json(400, {"error": "invalid JSON body"})

    url = (payload.get("url") or "").strip()
    if not URL_RE.match(url):
        return _json(400, {"error": "url must start with http:// or https://"})

    try:
        ttl_days = int(payload.get("ttl_days", 30))
    except (TypeError, ValueError):
        ttl_days = 30
    ttl_days = max(1, min(ttl_days, 365))

    now = int(time.time())
    expires_at = now + ttl_days * 86400

    # Generate a unique code (retry on the rare collision).
    for _ in range(5):
        code = _new_code()
        try:
            _table.put_item(
                Item={
                    "code": code,
                    "url": url,
                    "clicks": 0,
                    "created_at": now,
                    "expires_at": expires_at,
                },
                ConditionExpression="attribute_not_exists(code)",
            )
            break
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
    else:
        return _json(503, {"error": "could not allocate a code, try again"})

    base = _base_url(event)
    short_url = f"{base}/{code}" if base else f"/{code}"
    return _json(201, {"code": code, "short_url": short_url, "url": url})


def redirect(code):
    try:
        res = _table.update_item(
            Key={"code": code},
            UpdateExpression="ADD clicks :one",
            ExpressionAttributeValues={":one": 1},
            ConditionExpression="attribute_exists(code)",
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return _json(404, {"error": "unknown or expired link"})
        raise
    url = res["Attributes"]["url"]
    return {"statusCode": 302, "headers": {"location": url}, "body": ""}


def stats(code):
    res = _table.get_item(Key={"code": code})
    item = res.get("Item")
    if not item:
        return _json(404, {"error": "unknown or expired link"})
    return _json(
        200,
        {
            "code": item["code"],
            "url": item["url"],
            "clicks": int(item.get("clicks", 0)),
            "created_at": int(item.get("created_at", 0)),
            "expires_at": int(item.get("expires_at", 0)),
        },
    )


# --------------------------------------------------------------------- handler
def handler(event, _context):
    method, path = _method_and_path(event)

    if path == "/" and method == "GET":
        return {
            "statusCode": 200,
            "headers": {"content-type": "text/html; charset=utf-8"},
            "body": INDEX_HTML,
        }

    if path == "/api/links" and method == "POST":
        return create_link(event)

    if path.startswith("/api/links/") and method == "GET":
        return stats(path.rsplit("/", 1)[-1])

    if method == "GET":
        code = path.lstrip("/")
        if code and code not in RESERVED:
            return redirect(code)

    return _json(404, {"error": "not found"})


# ------------------------------------------------------------------------- UI
# Served inline so we need no S3/CloudFront. The page calls the same origin.
INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Snip · URL Shortener</title>
<style>
  :root { --ink:#0f172a; --accent:#f97316; --panel:#ffffff; --bg:#fff7ed; }
  * { box-sizing: border-box; }
  body { margin:0; font-family: ui-monospace, "SFMono-Regular", Menlo, monospace;
         background: var(--bg); color: var(--ink);
         min-height:100vh; display:grid; place-items:center; padding:24px; }
  .card { background: var(--panel); width:min(560px,100%);
          border:2px solid var(--ink); border-radius:14px; padding:28px 26px;
          box-shadow: 8px 8px 0 var(--ink); }
  h1 { margin:0 0 2px; font-size:34px; letter-spacing:-1px; }
  h1 span { color: var(--accent); }
  p.sub { margin:0 0 22px; color:#64748b; font-size:13px; }
  label { display:block; font-size:12px; text-transform:uppercase;
          letter-spacing:1px; margin:14px 0 6px; }
  input { width:100%; padding:12px 14px; font:inherit; border:2px solid var(--ink);
          border-radius:8px; }
  .row { display:flex; gap:12px; }
  .row > div:first-child { flex:1; }
  .row > div:last-child { width:120px; }
  button { margin-top:20px; width:100%; padding:13px; font:inherit; font-weight:700;
           cursor:pointer; color:#fff; background:var(--ink); border:none;
           border-radius:8px; }
  button:hover { background:var(--accent); }
  .out { margin-top:20px; padding:14px; border:2px dashed var(--ink);
         border-radius:8px; display:none; word-break:break-all; }
  .out.show { display:block; }
  .out a { color:var(--accent); font-weight:700; }
  .err { color:#dc2626; }
  footer { margin-top:22px; font-size:11px; color:#94a3b8; text-align:center; }
</style>
</head>
<body>
  <div class="card">
    <h1>Snip<span>.</span></h1>
    <p class="sub">Paste a long link, get a short one. Clicks are counted.</p>
    <label for="url">Long URL</label>
    <input id="url" placeholder="https://example.com/a/very/long/path" autofocus>
    <div class="row">
      <div>
        <label for="ttl">Expires in (days)</label>
        <input id="ttl" type="number" min="1" max="365" value="30">
      </div>
    </div>
    <button id="go">Shorten it</button>
    <div class="out" id="out"></div>
    <footer>Serverless · Lambda + DynamoDB</footer>
  </div>
<script>
const out = document.getElementById('out');
document.getElementById('go').onclick = async () => {
  const url = document.getElementById('url').value.trim();
  const ttl = parseInt(document.getElementById('ttl').value, 10) || 30;
  out.className = 'out show';
  out.textContent = 'Working…';
  try {
    const r = await fetch('/api/links', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ url, ttl_days: ttl })
    });
    const d = await r.json();
    if (!r.ok) { out.innerHTML = '<span class="err">' + (d.error || 'error') + '</span>'; return; }
    const short = location.origin + '/' + d.code;
    out.innerHTML = 'Short link: <a href="' + short + '" target="_blank">' + short + '</a>';
  } catch (e) {
    out.innerHTML = '<span class="err">network error</span>';
  }
};
</script>
</body>
</html>"""
