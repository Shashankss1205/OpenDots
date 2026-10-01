# Get notified when OpenDots needs you

Notifications send selected runtime updates to destinations you configure. They
do not read messages from Slack/Discord and do not approve agent actions.
Nothing is enabled by default. No historical notifications are sent when a new
destination is first started.

## Start with a local file

Add this to your existing configuration, using an absolute path outside your
project directory:

```json
"notifications": [
  {
    "id": "local-alerts",
    "kind": "jsonl",
    "path": "/absolute/path/to/runtime/notifications.jsonl",
    "events": ["approval_requested", "work_completed", "work_failed", "work_blocked"]
  }
]
```

Restart `opendots serve`, then continue your real work. Each subsequent matching
runtime event appends a JSON line. Inspect the destination and delivery status:

```bash
opendots notifications
```

Use `/notifications` in the TUI or **Notifications** in the web interface.
`GET /api/notifications` provides the same information. Inspection commands do
not deliver messages. The dispatcher runs only in `serve`; `drain` does not send
notifications. If a destination was already initialized, its saved audit cursor
lets it collect work recorded while the service was stopped when it next starts.

## Connect a webhook, Slack or Discord

Create the destination webhook through your service's normal setup. For Slack,
create a Slack app, enable Incoming Webhooks, and authorize its destination
channel. For Discord, create a webhook in your selected channel's Integrations
settings. Store the URL in an environment variable available to the runtime.
The URL is a credential; do not put its value in repository configuration.

Add a destination such as:

```json
{
  "id": "team-alerts",
  "kind": "webhook",
  "url_env": "OPENDOTS_ALERT_WEBHOOK",
  "format": "slack",
  "events": ["approval_requested", "work_failed", "work_blocked"],
  "targets": ["project"],
  "timeout_seconds": 10,
  "max_attempts": 5,
  "max_pending": 1000
}
```

Use your actual target ID. Omit `targets` to include all targets and global source
events. Choose `format: "discord"` for Discord, or `format: "json"` (the default)
for a generic JSON receiver. A generic receiver can use optional `token_env` for
a Bearer token. Endpoints require HTTPS, with HTTP permitted on loopback for local
receivers. Redirects are rejected. No endpoint or token values appear in the
notification inventory.

Format references: [Slack incoming webhooks](https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks/)
and [Discord execute webhook](https://docs.discord.com/developers/resources/webhook#execute-webhook).

Alerts contain only a delivery ID, event name, target ID, work ID and timestamp.
They omit source code, prompts, event bodies, summaries, error bodies, diffs and
approval tokens. Review the work in OpenDots. Discord mentions are disabled in
the outgoing payload. A successful HTTP response records delivery to the endpoint;
it does not prove that a person saw it.

## Choose what to receive

Supported event names:

- `approval_requested`, `approval_rejected`
- `work_completed`, `work_failed`, `work_blocked`, `work_drafted`
- `work_interrupted`, `work_cancelled`
- `source_failed`, `source_gap_detected`

The default selection is approval requests plus completed, failed and blocked
work. Events without a target (such as source errors) do not match a destination
with a target filter. Repeated source failures can produce repeated alerts; choose
those events deliberately. No notification-about-notification loop is generated.

## Delivery, retry and restart behavior

The dispatcher scans the durable audit log and transactionally saves both its
cursor and outbox rows. A slow destination does not block agent work. Each route
has a pending limit; reaching it leaves its audit cursor in place to catch up
later, rather than discarding alerts. The sender uses a separate worker and sends
bounded batches. Destinations share that worker, so a slow endpoint can delay
other notifications, but cannot hold the agent worker pool.

Failures retry with delays from 2 seconds up to 5 minutes, then become `failed`
after `max_attempts` (default 5). Counts, attempts and recent delivery status are
visible in the interfaces. Correct the transient problem, then explicitly retry
a failed delivery to the same destination:

```bash
opendots notifications --retry DELIVERY_ID
```

`serve` must be running to send the retry. Error records exclude remote response
bodies and exception messages that might expose secrets. Review destination
credentials, permissions, URL, service availability and network configuration
when diagnosing a failure.

Delivery is **at least once within the configured retry limit**: a crash after
remote acceptance but before local confirmation can cause a duplicate. The JSON
payload and `Idempotency-Key` header retain the same delivery ID across retries;
the receiving system must implement deduplication if required. Slack and Discord
webhooks should not be assumed to honor that header.

Changing a route's configuration or its referenced credential value starts a new
baseline and cancels previously queued deliveries for the old destination.
Disabling/removing a destination also cancels queued deliveries. They cannot be
manually redirected to a new destination. Re-enable and restart to receive future
events. Keep route IDs stable across ordinary restarts.

Shutdown waits for an in-flight bounded delivery before releasing scheduler
ownership. The built-in webhook timeout is configurable from 1 to 60 seconds.
Custom sinks must bound their own I/O. Outbox history is retained in the runtime
database and included by the existing database backup procedure.

## Add a notification adapter in a plugin

API 2 plugins can now register notifications alongside providers, tools and
sources:

```python
def register(api):
    api.notifications.register(
        "my_destination",
        MyNotificationSink,
        validate_config=validate_destination,
    )
```

The factory receives one destination configuration. Its object implements
`send(message)`, returning on success or raising on failure. It must honor finite
I/O timeouts, preserve the delivery ID and avoid logging credentials. Configuration
validation must be local and raise `ValueError` for invalid settings. It must not
send messages during registration, construction or validation. Plugin code is
trusted; registered notification delivery runs under the owner's explicit
destination configuration rather than a model-generated tool action.

See [the plugin package guide](PLUGINS.md) for installation and manifests. Native
Slack/Discord message listeners remain separate integrations.
