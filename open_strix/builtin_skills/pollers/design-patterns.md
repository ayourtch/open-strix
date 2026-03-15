# Poller Design Patterns

Practical patterns for writing reliable pollers. Read SKILL.md first for the basics.

## State Management

Pollers are stateless processes that run on a cron schedule. Between runs, they need to remember what they've already seen. This is entirely the poller's responsibility — the scheduler doesn't track state for you.

### The Cursor Pattern

Every poller needs a cursor — a marker for "where I left off." Store it in `STATE_DIR`.

```python
STATE_DIR = Path(os.environ.get("STATE_DIR", "."))
CURSOR_FILE = STATE_DIR / "cursor.json"

def load_cursor():
    if CURSOR_FILE.exists():
        return json.loads(CURSOR_FILE.read_text())
    return {}

def save_cursor(cursor):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CURSOR_FILE.write_text(json.dumps(cursor, indent=2))
```

**Use timestamps, not IDs.** URIs and IDs can be deleted, reordered, or non-monotonic. Timestamps (`indexed_at`, `created_at`, `updated_at`) are stable and monotonically increasing.

```python
# Good — timestamp cursor
cursor = load_cursor()
last_seen = cursor.get("last_indexed_at")
for item in items:
    if last_seen and item.indexed_at <= last_seen:
        continue
    # process item...
    cursor["last_indexed_at"] = item.indexed_at
save_cursor(cursor)

# Bad — URI cursor (fragile)
if item.uri == last_uri:
    break  # What if this URI was deleted?
```

### Always Save the Cursor

Save cursor state even when there are no new items. This prevents re-processing if the cursor file was missing.

```python
# Always save, even on empty runs
save_cursor(cursor)
```

### Cursor Recovery

On first run (no cursor file), either:
- **Process nothing** — safest default, avoids flooding the agent with old items
- **Process last N items** — if you want to bootstrap with recent history

```python
cursor = load_cursor()
if not cursor:
    # First run: only process items from the last hour
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    cursor["last_indexed_at"] = cutoff
```

## Filtering

Pollers should be selective. The agent gets one event per stdout line, so noise = wasted LLM calls.

### Filter by Type

Most APIs return mixed notification types. Only emit the ones your agent can act on.

```python
# Good — selective
ACTIONABLE_TYPES = {"reply", "mention", "quote"}
for notif in notifications:
    if notif.reason not in ACTIONABLE_TYPES:
        continue

# Bad — everything including likes, follows, reposts
for notif in notifications:
    emit(format_notification(notif))  # Agent can't do anything with "New like from @user!"
```

### Don't Filter on Read/Seen Status

Many APIs have an `is_read` or `seen` flag. Don't use it — it changes when anyone (or any client) views the resource, which may not be your agent.

```python
# Bad — breaks if you view profile in a browser
if notif.is_read:
    continue

# Good — use your own cursor
if last_seen and notif.indexed_at <= last_seen:
    continue
```

## Notification Noise — Pain Adds Up

Every event you emit costs an LLM call. That's real money and real latency. But the deeper problem is **pain** — not yours, the agent's operator.

A poller that fires on likes, follows, and reposts doesn't just waste tokens. It trains the operator to ignore poller output. After the 50th "someone liked your post" notification they didn't ask for, they stop reading any of them — including the reply that actually needed a response.

This is the same dynamic as alert fatigue in ops. Too many pages and the on-call stops responding. The fix isn't better alert routing, it's fewer alerts.

**Concrete rules:**
- Only emit events the agent can meaningfully act on (reply, investigate, escalate)
- "Someone liked your post" is never actionable — don't emit it
- If you're unsure whether a notification type is actionable, leave it out. You can always add it later; you can't un-annoy the operator.
- Measure: if >50% of your emitted events result in no agent action, your filter is too loose

## Prompt Quality

The `prompt` field is what the agent sees. Make it actionable.

### Include Context for Action

If the agent needs to reply, it needs URIs and CIDs. If it needs to close an issue, it needs the issue number.

```python
# Good — agent has everything it needs to act
prompt = f'@{handle} replied to your post: "{text}"'
prompt += f"\nReply URI: {notif.uri} | CID: {notif.cid}"
prompt += f"\nOriginal post URI: {notif.reason_subject}"

# Bad — agent knows something happened but can't do anything
prompt = f"@{handle} replied: {text}"
```

### Don't Truncate

The urge to truncate comes from traditional apps with noisy neighbor problems — one user's data shouldn't crowd out another's. Pollers don't have this problem. Your context window is large and the data you're emitting is the signal the agent needs to act on.

Truncated text loses context that an LLM would use. A 300-character snippet of a reply thread strips the setup that makes the reply make sense. The agent doesn't degrade gracefully — it confidently misinterprets what's left.

```python
# Bad — solving a problem you don't have
text = (record.text[:300] + "...") if len(record.text) > 300 else record.text

# Good — just include the text
prompt = f'@{handle} replied: "{record.text}"'
```

If context size is genuinely a concern, the right fix is filtering at the source (emit fewer events), not trimming the content of each event. Dropping entire low-value notifications preserves full context on the ones that matter.

## Error Handling

Pollers run unattended. The key rule: **never emit on error.** A malformed event wastes an LLM call.

**Don't wrap everything in try/except.** Let Python crash naturally. The scheduler captures stderr and logs non-zero exits as `poller_nonzero_exit`. An unhandled exception gives you the full traceback — line numbers, call stack, variable context. A `print(f"API call failed: {e}")` gives you almost nothing.

```python
def main():
    client = create_client()  # Let it crash — traceback tells you why
    response = client.fetch_notifications()

    for item in process(response):
        emit(item)
```

If you do need to catch an exception (e.g., to save cursor state before exiting), **always include the traceback:**

```python
import traceback

try:
    response = client.fetch_notifications()
except Exception:
    traceback.print_exc()  # Full traceback to stderr, not just the message
    sys.exit(1)
```

## Local Event Log

Optionally write events to a local JSONL file for debugging and history. This is separate from stdout (which the scheduler reads).

```python
EVENTS_FILE = STATE_DIR / "events.jsonl"

def emit(prompt):
    event = {"poller": POLLER_NAME, "prompt": prompt}
    # Stdout — scheduler picks this up
    print(json.dumps(event), flush=True)
    # Local log — stays for debugging
    with open(EVENTS_FILE, "a") as f:
        event["timestamp"] = datetime.now(timezone.utc).isoformat()
        f.write(json.dumps(event) + "\n")
```

## File Layout

```
skills/my-monitor/
├── SKILL.md           ← skill metadata + docs
├── pollers.json       ← declares pollers (scheduler reads this)
├── poller.py          ← the script
├── cursor.json        ← cursor state (written by poller)
├── events.jsonl       ← optional local event log
└── requirements.txt   ← if the poller has Python dependencies
```

Keep all poller state in `STATE_DIR`. Don't write to random locations — it makes debugging hard and breaks if the skill is moved.

## Anti-Patterns

| Anti-Pattern | Problem | Fix |
|---|---|---|
| URI-based cursors | URIs can be deleted or reordered | Use timestamps |
| Filtering on `is_read` | Changes when any client views the resource | Use your own cursor |
| Emitting likes/follows | Agent can't act on these, trains operator to ignore output | Filter to actionable types |
| Truncating event text | LLM confidently misinterprets partial context | Include full text, filter at the source instead |
| Missing URI/CID in prompts | Agent can't reply or take action | Include identifiers |
| Wrapping everything in try/except | Swallows tracebacks, makes debugging impossible | Let it crash — stderr has the traceback |
| `except Exception as e: print(e)` | Loses line numbers, call stack, context | Use `traceback.print_exc()` if you must catch |
| Hardcoded paths | Breaks when moved or run by scheduler | Use STATE_DIR env var |
| Writing state outside STATE_DIR | Hard to debug, breaks portability | Keep everything in STATE_DIR |
| Forgetting `reload_pollers` | Poller exists but scheduler doesn't know about it | Always call after creating/updating pollers.json |
