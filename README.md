# Signalbot for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

Send and receive [Signal](https://signal.org) messages directly from Home Assistant automations. This repository ships **two components that work together**:

1. A **Home Assistant Add-on** (`signalbot/`) — bundles `signal-cli-rest-api` and a setup/management web UI. Handles Signal account linking and chat-partner management for you.
2. A **HACS custom integration** (`custom_components/signalbot/`) — auto-discovered once the add-on is running. Creates notify entities, sensors, and events in Home Assistant.

> **Add-on repo for HA:** add `https://github.com/sahelea1/ha-hacs-integration-signalbot` in Settings → Add-ons → Add-on Store → three-dot menu → Repositories.

---

## How it works

```
Signal network
     |
     v
[ signal-cli-rest-api ]  <-- bundled inside the Signalbot Add-on
     |
     v
[ Add-on Web UI ]  -- QR linking, chat-partner management
     |
     | Supervisor discovery (automatic)
     v
[ Signalbot Integration ]  -- notify entities, sensors, events
     |
     v
Home Assistant automations & scripts
```

- The **add-on** runs `signal-cli-rest-api` internally so you do not need to manage it yourself.
- On first run the add-on Web UI presents a **QR code** (auto-refreshes every 30 seconds). Scan it with the Signal app to link HA as a linked device.
- Chat partners (each with a phone number and/or @username) are added one by one in the add-on Web UI.
- The add-on announces itself to HA via **Supervisor discovery** — no manual URL entry is needed.
- The integration creates one `notify.signalbot_<name>` entity per chat partner, two sensors, and fires a `signalbot_message_received` event for incoming messages.
- Only messages from **configured chat partners** trigger the event ("known senders only", togglable in the add-on UI).

---

## Requirements

- **Home Assistant OS** or **Home Assistant Supervised** (required for add-ons).
- The Signalbot HACS integration installed (so the auto-discovered entry has something to load).

> **HA Container / Core users:** Add-ons are not available on these installs. You would need to run `signal-cli-rest-api` yourself and configure the integration manually. The add-on path (HA OS / Supervised) is the fully supported and documented setup.

---

## Installation

### 1. Add-on

1. In Home Assistant go to **Settings → Add-ons → Add-on Store**.
2. Click the three-dot menu (top-right) and choose **Repositories**.
3. Add `https://github.com/sahelea1/ha-hacs-integration-signalbot` and click **Add**.
4. Search for **Signalbot** in the store and click **Install**.
5. Click **Start** to start the add-on.

### 2. Integration (via HACS)

1. Open **HACS → Integrations**.
2. Click the three-dot menu and choose **Custom repositories**.
3. Add `https://github.com/sahelea1/ha-hacs-integration-signalbot` with category **Integration**.
4. Search for **Signalbot** and click **Download**.
5. **Restart Home Assistant.**

Once HA restarts with the add-on running, Supervisor discovery will surface the integration automatically. You may still need to confirm it under **Settings → Devices & Services**.

---

## First-run setup

1. Open the **Signalbot** add-on page and click **Open Web UI** (or use the ingress panel).
2. A QR code is displayed. It auto-refreshes every 30 seconds.
3. On your phone open **Signal → Settings → Linked devices → Link new device** and scan the QR code.
4. Once linked, switch to the **Chat partners** tab in the Web UI.
5. Add each chat partner with a friendly name plus their phone number (E.164, e.g. `+49151123456789`) and/or Signal @username.
6. In Home Assistant, go to **Settings → Devices & Services**. The Signalbot integration should appear as discovered — click **Add** to confirm.

---

## Usage

### Sending a message via a notify entity

```yaml
service: notify.signalbot_alice
data:
  message: "The front door was opened!"
```

### Using the signalbot.send_message service

```yaml
service: signalbot.send_message
data:
  message: "Motion detected in the garage."
  recipients:
    - "+49151123456789"
    - "@alice.01"
  attachments:
    - /config/www/snapshot.jpg
```

### Reacting to incoming messages

The `signalbot_message_received` event is fired for every incoming message from a known sender.

Event data fields:

| Field | Description |
|---|---|
| `recipient_name` | Friendly name of the configured chat partner |
| `source` | Sender phone number or @username |
| `message` | Message text |
| `timestamp` | Unix timestamp of the message |

Only messages from **configured chat partners** fire the event (known-senders allowlist). Toggle this behaviour in the add-on Web UI.

Example automation:

```yaml
automation:
  alias: "Signal command — turn on lights"
  trigger:
    - platform: event
      event_type: signalbot_message_received
      event_data:
        recipient_name: "Alice"
        message: "turn on lights"
  action:
    - service: light.turn_on
      target:
        area_id: living_room
```

### Sensors

| Entity | Description |
|---|---|
| `sensor.signalbot_last_message` | Text of the most recently received message |
| `sensor.signalbot_link_status` | Account link state: `linked`, `unlinked`, or `error` |

---

## Configuration

All runtime configuration — chat partners, the known-senders allowlist, and the receive poll interval — is managed in the **Signalbot add-on Web UI**. No YAML editing is required.

---

## Troubleshooting

**Add-on is not auto-discovered**
- Make sure the add-on is running (green "Running" status on the add-on page).
- Confirm the HACS integration is installed and HA has been restarted after installing it.
- Check Settings → Devices & Services for a pending discovery entry.

**QR code does not appear or times out**
- Open the add-on log tab for error messages.
- Refresh the Web UI — the QR code auto-refreshes every 30 seconds.
- If linking fails, restart the add-on and try again.

**signal-cli is unreachable**
- The add-on bundles signal-cli-rest-api, so it starts automatically. Check the add-on log for startup errors.
- Ensure the add-on has network access (no unusual HA network restrictions).

**Re-linking the Signal account**
- Open the add-on Web UI, navigate to **Account** and choose **Unlink / re-link**. A fresh QR code will be generated.

**Entities missing after adding a chat partner**
- Reload the integration: Settings → Devices & Services → Signalbot → three-dot menu → Reload.

For further help open an issue at [github.com/sahelea1/ha-hacs-integration-signalbot/issues](https://github.com/sahelea1/ha-hacs-integration-signalbot/issues).

---

## Disclaimer

Signalbot is an **unofficial, community-developed** project and is not affiliated with, endorsed by, or supported by Signal Messenger LLC or the Signal Foundation. Use it in accordance with Signal's [Terms of Service](https://signal.org/legal/).
