## Signalbot (add-on v1.2.0 · integration v0.4.0)

Signalbot connects Home Assistant to [Signal](https://signal.org) using two components that work together: a **Home Assistant Add-on** (bundles `signal-cli-rest-api` + a setup Web UI) and a **HACS custom integration** (creates entities and events in HA).

### How to get started

1. Install the **Signalbot Add-on** from this repository and start it.
2. Open the add-on **Web UI** ("Open Web UI") — scan the **QR code** with the Signal app (**Settings → Linked devices → Link new device**) to link HA as a Signal linked device.
3. Add **chat partners** in the Web UI — either click **"Add as chat partner"** next to a recent sender in the **Recent messages** card (recommended), or enter a friendly name and E.164 number manually.
4. The add-on auto-announces itself via Supervisor discovery — confirm the integration under **Settings → Devices & Services** when prompted.

### Adding chat partners the easy way

After someone sends you a Signal message, their number appears in the **Recent messages** card in the Web UI. Click **"Add as chat partner"** to whitelist them instantly. This captures their exact number — eliminating the most common reason senders are silently ignored — and automatically creates their `notify.signalbot_<name>` entity and enables the `signalbot_message_received` event for them.

> By default, incoming messages from senders not in your chat-partner list are silently ignored (known-senders allowlist). The number must exactly match the sender's — clicking to whitelist guarantees this.

### What you get

- **`notify.signalbot_<name>`** entity per chat partner — send Signal messages from automations.
- **`signalbot.send_message`** service — send to multiple recipients (by name, phone number, username, or group) with optional file attachments.
- **`signalbot_message_received`** event — trigger automations when a message arrives. Includes `source`, `source_name`, `message`, `timestamp`, `command`, `command_args`, and more. Only known senders (configured chat partners) trigger the event by default — configurable in the add-on UI.
- **`sensor.signalbot_last_message`** — the most recently received message text (with sender attributes; handy for dashboards).
- **`sensor.signalbot_link_status`** — reports `linked`, `unlinked`, or `error`.

### Performance

The add-on runs signal-cli in **`native` mode** (GraalVM binary) by default — fast startup and low CPU/RAM use. Change the mode in the add-on **Configuration** tab if needed.

### Requirements

- **Home Assistant OS** or **Home Assistant Supervised** (add-ons require Supervisor).
- Signalbot HACS integration installed alongside the add-on.

> All configuration (chat partners, known-senders allowlist, poll interval) is managed in the **add-on Web UI** — no YAML required.
