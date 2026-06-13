## Signalbot

Signalbot connects Home Assistant to [Signal](https://signal.org) using two components that work together: a **Home Assistant Add-on** (bundles `signal-cli-rest-api` + a setup Web UI) and a **HACS custom integration** (creates entities and events in HA).

### How it works

1. Install the **Signalbot Add-on** from this repository and start it.
2. Open the add-on **Web UI** — scan the auto-refreshing **QR code** with the Signal app (**Settings → Linked devices → Link new device**) to link HA as a Signal linked device.
3. Add **chat partners** in the Web UI (friendly name + phone number and/or @username).
4. The add-on auto-announces itself via Supervisor discovery — confirm the integration in **Settings → Devices & Services**.

### What you get

- **`notify.signalbot_<name>`** entity per chat partner — send Signal messages from automations.
- **`signalbot.send_message`** service — send to multiple recipients with optional file attachments.
- **`signalbot_message_received`** event — fire automations when a message arrives. Includes `recipient_name`, `source`, `message`, and `timestamp`. Only known senders (configured chat partners) trigger the event — configurable in the add-on UI.
- **`sensor.signalbot_last_message`** — the most recently received message text.
- **`sensor.signalbot_link_status`** — reports `linked`, `unlinked`, or `error`.

### Requirements

- **Home Assistant OS** or **Home Assistant Supervised** (add-ons require Supervisor).
- Signalbot HACS integration installed before or alongside the add-on.

> All configuration (chat partners, known-senders allowlist, poll interval) is managed in the **add-on Web UI** — no YAML required.
