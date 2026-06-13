# Signalbot Add-on

Signalbot bundles two services in a single Home Assistant add-on container:

- **signal-cli-rest-api** — the open-source Signal messenger bridge by [@bbernhard](https://github.com/bbernhard/signal-cli-rest-api), which handles all Signal protocol communication.
- **Signalbot Manager** — a lightweight Python web app that provides a setup UI for QR device-linking and chat-partner management.

The companion **Signalbot integration** (installed separately via HACS) is auto-discovered by Home Assistant once this add-on is running, and lets you send/receive Signal messages from automations and scripts.

---

## Prerequisites

- A phone number registered with Signal.
- The Signal mobile app installed on your phone (Android or iOS).

---

## Installation

1. Add this repository URL to your Home Assistant add-on store (Supervisor → Add-on Store → three-dot menu → Repositories).
2. Find **Signalbot** in the add-on list and click **Install**.
3. Start the add-on.

---

## Opening the Web UI

Click **Signalbot** in the Home Assistant sidebar (or open the add-on page and click **Open Web UI**). The Signalbot Manager UI opens via the HA Ingress proxy — no additional port forwarding is required.

---

## First-Run: Linking Your Signal Account (QR Code)

Signalbot connects to Signal as a **linked device** (secondary device), so your primary phone number remains the owner.

1. Open the Signalbot Manager UI in the sidebar.
2. Navigate to the **Link Device** tab.
3. A QR code is displayed. It refreshes automatically every 30 seconds — each refresh generates a new, valid code. Do not wait too long before scanning.
4. On your phone, open the **Signal app** → **Settings** → **Linked Devices** → **Link New Device**.
5. Scan the QR code shown in the Manager UI.
6. Wait a moment for the handshake to complete. The UI will show a confirmation once the account is linked.

Once linked, signal-cli-rest-api is ready to send and receive messages.

---

## Adding Chat Partners

After linking:

1. Open the Signalbot Manager UI.
2. Navigate to the **Chat Partners** tab.
3. Enter the phone number (in E.164 format, e.g. `+4917612345678`) of each contact you want to use from automations.
4. Save the list. The integration exposes each partner as a configurable entity.

---

## Auto-Discovery of the Signalbot Integration

Once the add-on starts, it automatically registers itself with the Home Assistant Supervisor discovery system. Home Assistant will prompt you to set up the **Signalbot** integration. Accept the prompt to complete the configuration — no manual host/port entry is needed.

If the prompt does not appear, go to **Settings → Devices & Services → Add Integration** and search for **Signalbot**.

---

## Data Persistence

All signal-cli account data (linked device keys, message history) is stored in `/data/signal-cli` inside the container. This maps to the add-on's private persistent storage managed by Supervisor — it survives add-on restarts and updates.

---

## Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `log_level` | `info` | Log verbosity: `trace`, `debug`, `info`, `notice`, `warning`, `error`, `fatal` |

---

## Modes

The add-on runs signal-cli-rest-api in **`MODE=normal`**. This mode supports both sending and receiving messages, and is required for full two-way messaging functionality. The Signalbot Manager and the signal-cli-rest-api binary run concurrently under supervisord.

---

## Troubleshooting

- **QR code expired before I could scan it** — Click the refresh button or wait for the 30-second auto-refresh to generate a new code.
- **Integration not auto-discovered** — Check that the add-on is running, then restart Home Assistant. You can also add the integration manually via **Settings → Devices & Services**.
- **Messages not received** — Ensure `MODE=normal` is set (default) and the account is fully linked. Check the add-on log for errors from signal-cli.
- **Log access** — Open the add-on page in Supervisor and click the **Log** tab.
