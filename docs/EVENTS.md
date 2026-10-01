# Connect events to your goal

An event is a message about something that happened. You decide where it comes from and what information it carries. OpenDots records the message, checks each agent's subscription, then assesses whether matching information is relevant to that agent's goal when model relevance is enabled.

Nothing in this guide sends a message automatically. Replace the example content with information from your own system.

## See what is connected and what arrived

In the terminal interface:

```text
/listeners
/events
/event EVENT_ID
```

- `/listeners` shows sources, polling health, schedules, subscriptions, and relevance settings currently loaded by the runtime.
- `/events` lists **received messages**, including ones filtered out or judged irrelevant. `/events TEXT` searches their type, source, ID, or content; `/events-next` pages back through older messages.
- `/event EVENT_ID` shows the original payload and each agent's routing/assessment decision. Use the actual ID from the list.
- `/connect` gives a short connection guide. `/status` shows provider usage and source errors.

In the browser, use **What is listening?** and **Received events**. Expand an agent to inspect subscription rules. Search events, page backward, and select **View payload and decisions**. **Latest** returns to live updates. The **Send an event** form supports an event ID and arbitrary JSON payload; **Preview JSON** shows exactly what will be sent.

From your shell, `opendots listeners` and `opendots events --query TEXT` provide the same information as JSON. Add `--config PATH` before the command if you use a non-default config.

**These are different lists:** subscriptions describe what could match in the future; received events are actual persisted messages; tasks are work created for matched agents. One event can create several tasks or none.

## Create an event

The envelope has this shape:

```json
{
  "id": "a-unique-delivery-id-from-your-producer",
  "type": "input.build_failed",
  "source": "local",
  "target_id": "project",
  "payload": {
    "title": "Describe the actual failure",
    "body": "Include the relevant error or evidence"
  }
}
```

`type` and `payload` describe the event. `source` is a routing label, not proof of the sender's identity. `target_id` is optional; omitting it considers all configured agents. It never bypasses subscriptions. `id` is optional, but producers that retry should reuse a stable ID. The same ID and content are deduplicated; reusing an ID with different content is rejected. Optional `priority` is 0–100 and controls queue attention, **not confidence**.

Custom event types are strings you choose. They do not need to be added to a global registry. A new `init` configuration listens to `owner.*` and `input.*` from `local` or `file`, plus `timer.heartbeat` from `timer`. Other names require matching subscriptions.

### From the terminal interface

A normal message sends `owner.request` to the selected agent. Choose another type with:

```text
/send input.build_failed Describe the failure and relevant evidence
```

For full control, use `/emit` followed by a JSON envelope on one line. `/emit` does not automatically attach the selected agent: specify `target_id` if you want it.

### From your shell

```bash
opendots event --type input.build_failed --source local --target project \
  --id your-unique-delivery-id \
  --payload '{"title":"Describe the actual failure","body":"Include relevant evidence"}'
```

This records and routes an event in the configured database. `serve` must be running to assess/process it, or use `drain` to process existing queued work once. `drain` does not poll sources or emit heartbeats.

### From another program using HTTP

```bash
curl --fail-with-body http://127.0.0.1:8765/api/events \
  -H 'Content-Type: application/json' \
  -H 'X-OpenDots-Request: dashboard' \
  -d '{"id":"your-unique-delivery-id","type":"input.build_failed","source":"local","target_id":"project","payload":{"title":"Describe the actual failure","body":"Include relevant evidence"}}'
```

The response includes the recorded event ID and number of candidate tasks queued. **Queued is not a relevance verdict or a completion claim.** Assessment runs asynchronously; inspect `/events` or the browser for the result. Body size is limited to 256,000 bytes. Queue/rate exhaustion returns HTTP 429; retry with the same ID. This is a local endpoint, not a public authenticated ingestion service.

## Connect a listening technology

Edit your configuration and restart `serve` after changes. Add entries to existing arrays; do not replace unrelated sources, subscriptions, or schedules. `opendots listeners` shows the result.

### JSONL: poll a file your tools append to

Add to the top-level `sources` array:

```json
{
  "id": "project-inbox",
  "kind": "jsonl",
  "path": "/absolute/path/outside-your-project/events.jsonl",
  "interval_seconds": 5,
  "batch_size": 100
}
```

Write one complete JSON object followed by a newline per event:

```bash
printf '%s\n' '{"id":"your-unique-delivery-id","type":"input.build_failed","source":"file","payload":{"title":"Describe the actual failure"}}' >> /absolute/path/outside-your-project/events.jsonl
```

Use the same path in both places, creating its parent directory first. JSONL defaults a missing source to `file`. Its cursor survives restarts. Incomplete lines wait for completion; malformed complete records are audited and skipped. Keep inbox/data files outside the source-code directory.

The generated `input.*`/`file` subscription already matches. If you choose different types, add a rule to the target's `subscriptions`:

```json
{"types": ["ci.failed", "feedback.received"], "sources": ["file"]}
```

### GitHub: poll new repository activity

Add to `sources`, replacing the repository:

```json
{
  "id": "repository-activity",
  "kind": "github_poll",
  "repo": "YOUR_OWNER/YOUR_REPOSITORY",
  "interval_seconds": 60,
  "bootstrap": "observe",
  "max_pages": 5,
  "token_env": "GITHUB_TOKEN"
}
```

Add a separate target subscription:

```json
{
  "types": ["github.issue.*", "github.comment.*", "github.pr.*"],
  "sources": ["github"],
  "repos": ["YOUR_OWNER/YOUR_REPOSITORY"]
}
```

Supply `GITHUB_TOKEN` in the runtime environment if needed; never put the token value in the config. An existing CLI login does not supply that variable automatically. `observe` establishes a baseline and waits for new activity. `replay` considers available recent events. Polling is bounded, respects GitHub's polling interval, and is not a full issue backlog scan or lossless stream. `/listeners` exposes polling failures and gap warnings.

Do not add a repository restriction to your owner/timer rules: those messages often do not carry a repository field. GitHub events do not fetch new source code into the agent's workspace.

### GitHub webhooks: receive signed deliveries

Set `OPENDOTS_GITHUB_WEBHOOK_SECRET` in the runtime environment. The endpoint is `/api/webhooks/github`; it verifies `X-Hub-Signature-256` against the exact request bytes and requires the GitHub event and delivery headers. GitHub messages normalize to the same event types as polling, so use the same subscriptions.

GitHub cannot call localhost directly. Use an external receiver that preserves the signed body and forwards locally with an accepted local Host header. Production ingress is separate setup; do not expose the entire unauthenticated dashboard. Polling is the directly usable local option.

### Heartbeat: schedule a new event

Add to top-level `schedules`:

```json
{
  "id": "project-heartbeat",
  "target_id": "project",
  "type": "timer.heartbeat",
  "interval_seconds": 1800,
  "payload": {"title": "Reassess progress toward the saved goal"}
}
```

Pair it with `{"types":["timer.heartbeat"],"sources":["timer"]}` in subscriptions; normal `init` already includes that rule. `init --heartbeat 1800` creates both for new setups.

A fresh schedule emits its current tick at startup. Intervals are clock-aligned, not measured from task completion. Restarting in the same tick does not duplicate it; missed intervals collapse to the current tick. Paused agents still receive queued events. Queue and model-call limits apply.

### Kafka, Redis, file watchers, Slack, or email

These are not native adapters. Run a consumer, webhook receiver, or file-watching process for your chosen system and have it produce the HTTP or JSONL envelope above. Use the upstream message/delivery ID as the event ID, map the message into `payload`, and subscribe to its chosen type/source. A custom source label such as `kafka` needs a matching `sources: ["kafka"]` rule.

For a broker, acknowledge only after successful ingestion (HTTP 202); retry failures and HTTP 429. Ingestion acknowledges durable receipt, not completed work. A rejected relevance decision is still a successfully received message. Idempotent delivery IDs prevent retries creating duplicate tasks. Producer authentication and transport are the bridge's responsibility.

Trusted Python plugins can register polling adapters through `api.sources.register` or persistent connections through `api.sources.register_listener`. The [plugin guide](PLUGINS.md) covers installation, lifecycle, cursor persistence and upstream acknowledgments. `/plugins` shows capability ownership; `/listeners` shows each configured connection's mode and health. Native Slack/Discord adapters are not bundled.

## How goal relevance and confidence work

For each target:

1. **Rule filter:** match event type, source, repository, explicit target, and minimum priority. Unmatched events remain visible with a reason. They do not spend a model call and have no confidence score.
2. **Goal assessment:** if enabled, ask the target's Claude/Codex provider to compare the event with the saved goal. This happens before creating a task workspace or executing task actions. It receives the goal, desired state, and event—not a repository snapshot or tools for modifying it.
3. **Record the result:** persist `decision` (`relevant`, `irrelevant`, or `uncertain`), estimated `confidence` (0–1), reason, goal, provider, threshold, and assessment time.
4. **Apply the threshold:** relevant + sufficient confidence proceeds to planning. Confidently irrelevant work is marked `ignored`. Uncertain or low-confidence results become `blocked`; errors and exhausted budgets also block. Nothing silently falls through to execution.

New normal configurations enable:

```json
"relevance": {"mode": "model", "minimum_confidence": 0.7}
```

Add this object inside an existing target to enable it there. Older configurations without it retain rule-only routing and display **semantic relevance disabled**, with no invented score. `{"mode":"off"}` explicitly selects rule-only routing. Custom agent providers must implement `assess_relevance(target, event)` to use model mode; unsupported providers block.

Confidence is the model's estimate of certainty in its chosen label, **not a calibrated probability and not the event priority**. A 95% confident *irrelevant* label means the model believes it does not fit the goal. Low-confidence relevant labels remain blocked. Thresholds are configurable per agent and do not replace approval policies.

Assessments consume the same daily provider-call budget as planning and share the provider timeout. They run in bounded workers, so the UI can show `pending` while earlier work or approvals wait. Every target assesses independently. Duplicate deliveries reuse stored decisions; resuming an approved action does not re-charge the same assessment. Source text is untrusted and never grants permissions.

For blocked or ignored work, inspect `/event ID` and `/work TASK_ID`. Correct the input or configuration, then submit a new event. To deliberately reassess the old task after inspection, use `/retry TASK_ID inspected`. This creates a new event and does not override the gate.

History from before this feature has no retroactively manufactured confidence. Model protocol and routing tests are not evidence of a successful live-provider task; validate with your own authenticated provider.
