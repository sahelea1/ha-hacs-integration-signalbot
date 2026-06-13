# Changelog

## 1.1.0

- **`native` mode is now the default** — signal-cli runs as a GraalVM native binary out of the box, delivering much lower CPU and RAM usage. On 32-bit (`armv7`) hardware the add-on automatically falls back to `normal`.
- **`mode` configuration option** — choose between `native` (default, recommended) and `normal` (JVM, higher resource use) in the add-on Configuration tab.
- **Fixed blank ingress QR / Web UI** — corrected the ingress base path so the QR code and manager UI load correctly when accessed through the HA Ingress proxy.
- **Status and QR caching with request throttling** — the manager now caches status and QR-code responses to prevent bursts of requests from overloading signal-cli and causing high CPU/RAM spikes.
- **Serialized signal-cli calls + realistic timeouts** — all signal-cli-backed calls (`/v1/accounts`, `/v1/qrcodelink`) now run one-at-a-time behind a single lock, eliminating the account config-file lock contention that made request latency spiral to over a minute (and pinned the CPU). The account-status timeout was also raised so the linked account is detected reliably even on slower hardware.
- **`/command` parsing in incoming-message events** — messages starting with `/` are automatically split into a `command` field (first token, lowercased) and a `command_args` field (remainder). This makes it straightforward to build command-dispatching automations without string manipulation in templates.

## 1.0.0

### Initial release

- Bundled `signal-cli-rest-api` — no external Signal backend needed; everything runs inside the add-on container.
- **QR-link setup UI** — opens automatically on first run via the add-on ingress (Web UI). The QR code auto-refreshes every 30 seconds. Scan it with the Signal app (Settings → Linked devices → Link new device) to register Home Assistant as a linked Signal device.
- **Chat-partner management** — add, edit, and remove chat partners in the Web UI. Each partner can be identified by a phone number (E.164), a Signal @username, or both.
- **Known-senders allowlist** — incoming messages from unconfigured senders are silently ignored by default. Toggle this behaviour per installation in the Web UI.
- **Configurable receive poll interval** — set how frequently the add-on polls `signal-cli-rest-api` for new messages.
- **Supervisor discovery** — the add-on announces itself to the Home Assistant Supervisor so the Signalbot HACS integration is auto-discovered without any manual URL entry.
