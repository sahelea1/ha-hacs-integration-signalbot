# Signalbot for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

A Home Assistant custom integration that connects your smart home to [Signal](https://signal.org) via the [signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api) HTTP service. Send notifications, receive messages, and build powerful two-way automations — all without leaving Home Assistant.

---

## What it does

Signalbot bridges Home Assistant and Signal by talking to a locally (or remotely) hosted **signal-cli-rest-api** instance. Once set up, you can:

- **Send messages** to any Signal contact or group via `notify` entities or the `signalbot.send_message` service
- **Receive messages** — the integration polls for incoming messages and fires a `signalbot_message_received` event you can use in automations
- **Monitor link status** — a sensor tracks whether the Signal account connection is healthy
- **Manage multiple recipients** — add contacts by phone number (E.164) and/or @username and control which identifier is preferred

---

## Features

- UI-driven config flow — no YAML editing required for initial setup
- Link a Signal account via QR code or register/use an existing number
- Per-recipient `notify.signalbot_<name>` entities
- `signalbot.send_message` service with support for attachments
- `signalbot_message_received` event for incoming message automations
- `sensor.signalbot_last_message` sensor exposing the most recently received message
- Link-status sensor for monitoring account health
- Recipients configurable via the integration's *Configure* (options) dialog

---

## Prerequisites

You need a running **signal-cli-rest-api** instance that Home Assistant can reach over HTTP. This is a separate service that wraps the Signal protocol; see [bbernhard/signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api) for full documentation.

> **Important:** The service must run in `normal` mode (`MODE=normal`) so that message polling works correctly.

### Quick start with Docker

```bash
docker run -d \
  --name signal-api \
  -p 8080:8080 \
  -v /path/to/signal-data:/home/.local/share/signal-cli \
  -e MODE=normal \
  bbernhard/signal-cli-rest-api:latest
```

### Docker Compose example

```yaml
services:
  signal-api:
    image: bbernhard/signal-cli-rest-api:latest
    container_name: signal-api
    restart: unless-stopped
    ports:
      - "8080:8080"
    volumes:
      - ./signal-data:/home/.local/share/signal-cli
    environment:
      - MODE=normal
```

The API is then available at `http://<host>:8080`. You can verify it is running by browsing to `http://<host>:8080/v1/about`.

---

## Installation via HACS

### Option A — HACS custom repository (recommended)

1. Open HACS in Home Assistant.
2. Click the three-dot menu (top right) and select **Custom repositories**.
3. Add `https://github.com/sahelea1/ha-hacs-integration-signalbot` with category **Integration**.
4. Search for "Signalbot" in HACS and click **Download**.
5. Restart Home Assistant.

### Option B — Manual install

1. Copy the `custom_components/signalbot` folder from this repository into your Home Assistant configuration directory so the path is `<config>/custom_components/signalbot/`.
2. Restart Home Assistant.

---

## Setup (config flow)

Navigate to **Settings → Devices & Services → Add Integration** and search for **Signalbot**.

### Step 1 — Enter the signal-cli-rest-api URL

Provide the base URL of your signal-cli-rest-api instance, for example:

```
http://192.168.1.100:8080
```

### Step 2 — Link your Signal account

Choose how to associate a Signal account:

| Method | When to use |
|---|---|
| **Link via QR code** | You already have Signal installed on a phone and want to link HA as a secondary device |
| **Register number** | You have a phone number not yet registered with Signal |
| **Use existing number** | The signal-cli-rest-api instance already has an account registered |

**Linking via QR code:**

1. Select *Link via QR code*.
2. The integration displays a QR code.
3. On your phone, open Signal → **Settings → Linked Devices → Link New Device**.
4. Scan the QR code.
5. Home Assistant waits until linking completes, then saves the configuration.

Once the config flow completes, the integration is ready to use.

---

## Adding chat partners / recipients

After setup, click **Configure** on the Signalbot integration card (Settings → Devices & Services → Signalbot → Configure) to open the options dialog.

Each recipient entry has:

| Field | Description |
|---|---|
| **Friendly name** | Used to name the `notify` entity (`notify.signalbot_<name>`) |
| **Phone number** | E.164 format, e.g. `+49151123456789` (optional if @username is provided) |
| **@username** | Signal username, e.g. `@alice.01` (optional if phone number is provided) |
| **Preferred identifier** | Whether to address this recipient by phone or username when both are set |

After saving, each recipient appears as a `notify.signalbot_<name>` entity.

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

### Automation example — notify on door open

```yaml
automation:
  alias: "Signal alert on front door"
  trigger:
    - platform: state
      entity_id: binary_sensor.front_door
      to: "on"
  action:
    - service: notify.signalbot_alice
      data:
        message: "Front door opened at {{ now().strftime('%H:%M') }}."
```

### Receiving messages

Every time a new message is received, the integration fires a `signalbot_message_received` event with the following data fields:

| Field | Description |
|---|---|
| `sender` | Sender phone number or username |
| `message` | Message text |
| `timestamp` | Unix timestamp of the message |

You can trigger automations on this event:

```yaml
automation:
  alias: "React to Signal message"
  trigger:
    - platform: event
      event_type: signalbot_message_received
      event_data:
        message: "turn on lights"
  action:
    - service: light.turn_on
      target:
        area_id: living_room
```

The most recently received message is also available as **`sensor.signalbot_last_message`**.

### Link-status sensor

**`sensor.signalbot_link_status`** reports the current state of the Signal account connection (`linked`, `unlinked`, or `error`). Use it in dashboards or automations to alert you if the connection drops.

---

## Troubleshooting

**The config flow cannot reach signal-cli-rest-api**
- Confirm the container is running: `docker ps | grep signal-api`
- Ensure Home Assistant can reach the host and port (check firewall rules)
- Try opening `http://<host>:8080/v1/about` in a browser from the same network

**Messages are not being received**
- Verify the service is running with `MODE=normal`; other modes (e.g. `json-rpc`) do not support HTTP polling
- Check the Home Assistant logs under Settings → System → Logs for `signalbot` entries

**QR code linking fails or times out**
- The QR code is valid for a limited time; try restarting the config flow if it expires
- Make sure your phone has a stable internet connection during scanning

**Entity names look wrong**
- Friendly names are normalised (lowercased, spaces replaced with underscores) to form the entity ID. For example, a recipient named "Alice B" becomes `notify.signalbot_alice_b`.

For further help, please open an issue at [github.com/sahelea1/ha-hacs-integration-signalbot/issues](https://github.com/sahelea1/ha-hacs-integration-signalbot/issues).

---

## Disclaimer

Signalbot is an **unofficial, community-developed** integration and is not affiliated with, endorsed by, or supported by Signal Messenger LLC or the Signal Foundation. Use it at your own discretion and in accordance with Signal's [Terms of Service](https://signal.org/legal/).
