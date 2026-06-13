# Signalbot Add-on (v1.1.0)

Signalbot bundles two services in a single Home Assistant add-on container:

- **signal-cli-rest-api** — the open-source Signal messenger bridge by [@bbernhard](https://github.com/bbernhard/signal-cli-rest-api), which handles all Signal protocol communication.
- **Signalbot Manager** — a lightweight Python web app that provides a setup UI for QR device-linking and chat-partner management.

The companion **Signalbot integration v0.3.0** (installed separately via HACS) is auto-discovered by Home Assistant once this add-on is running, and lets you send and receive Signal messages from automations and scripts.

---

## Prerequisites

- A phone number registered with Signal.
- The Signal mobile app installed on your phone (Android or iOS).

---

## Installation

1. In Home Assistant go to **Settings → Add-ons → Add-on Store**.
2. Click the three-dot menu (top-right) and choose **Repositories**.
3. Add `https://github.com/sahelea1/ha-hacs-integration-signalbot` and click **Add**.
4. Find **Signalbot** in the add-on list and click **Install**.
5. Click **Start** to start the add-on.

Install the HACS integration alongside the add-on (see the main README for those steps).

---

## Opening the Web UI

Open the add-on page and click **Open Web UI** (Benutzeroberfläche öffnen). The Signalbot Manager UI opens via the HA Ingress proxy — no additional port forwarding is required.

---

## First-run: Linking your Signal account (QR code)

Signalbot connects to Signal as a **linked device** (secondary device), so your primary phone number remains the owner.

1. Open the Signalbot Manager UI.
2. A QR code is displayed on the screen.
3. On your phone, open the **Signal app** → **Settings** → **Linked Devices** → **Link New Device** and scan the QR code.
4. Wait a moment for the handshake to complete. Once linked, the UI switches to show **Chat partners** and **Settings**, and the status pill shows "Connected as +49…".

If the QR code expires before you can scan it, refresh the page to get a new one.

---

## Adding chat partners

After linking your account:

1. In the Web UI, go to **Chat partners**.
2. For each contact you want to use from automations, enter:
   - A **friendly name** (used for the `notify.signalbot_<name>` entity and in automation matching).
   - Their **phone number** in E.164 format (e.g. `+4915123456789`), and/or their **Signal username**.
   - If both are provided, use the **prefer** toggle to choose which identifier is used when sending.
3. Save the list.

> **Note:** Incoming-message matching uses the sender's **phone number**. If a contact sends from a number not in your chat-partner list, their message is silently ignored by default (see Known-senders allowlist below).

---

## Auto-discovery of the Signalbot integration

Once the add-on starts, it automatically registers itself with the Home Assistant Supervisor discovery system. Home Assistant will prompt you to set up the **Signalbot** integration under **Settings → Devices & Services**.

If the prompt does not appear, restart Home Assistant (after the add-on is running) or go to **Settings → Devices & Services → Add Integration** and search for **Signalbot**.

---

## Configuration options

| Option | Default | Description |
|--------|---------|-------------|
| `mode` | `native` | signal-cli execution mode. `native` uses a GraalVM binary — fast and low memory (recommended). `normal` uses the JVM — slowest, highest CPU. On 32-bit (`armv7`) hardware `native` automatically falls back to `normal`. |
| `log_level` | `info` | Log verbosity: `trace`, `debug`, `info`, `notice`, `warning`, `error`, `fatal` |

> Do **not** use `json-rpc` or daemon modes — they are not supported by this add-on and will break message receiving.

---

## Modes explained

`native` mode (default and recommended) runs signal-cli as a GraalVM native binary. It starts fast and uses significantly less CPU and RAM than the JVM-based `normal` mode.

`normal` mode runs signal-cli on the JVM. Use it only if `native` misbehaves on your hardware.

Switch modes in the add-on **Configuration** tab and restart the add-on to apply the change.

---

## Data persistence

All signal-cli account data (linked device keys, message history) is stored inside the add-on's private persistent storage managed by Supervisor. It survives add-on restarts and updates.

---

## Entities provided by the integration

Once the companion integration is configured, the following entities appear in Home Assistant:

| Entity | Description |
|--------|-------------|
| `notify.signalbot_<name>` | One per chat partner — use in automations to send a message to that partner. |
| `sensor.signalbot_last_message` | State = text of the last received message. Attributes include `source` (sender phone), `source_name`, `command`, `command_args`, and more. Useful on dashboards or as an alternative to event-based automations. |
| `sensor.signalbot_link_status` | Account link state: `linked`, `unlinked`, or `error`. |

---

## Troubleshooting

**QR code does not appear**
- Refresh the Web UI page. The manager caches QR codes and status to avoid overloading signal-cli — a refresh will trigger a new fetch.
- If it still does not appear, check the **Log** tab for errors, then restart the add-on.

**Integration not auto-discovered**
- Confirm the add-on is running (green status). Restart Home Assistant if needed.
- You can also add the integration manually via **Settings → Devices & Services → Add Integration → Signalbot**.

**Messages not received**
- Ensure the account is fully linked (status pill shows "Connected").
- Check that the sender's phone number is in your chat-partner list, or disable the known-senders allowlist in the Web UI Settings.
- Check the **Log** tab for errors from signal-cli.

**High CPU or RAM usage**
- Confirm `mode` is set to `native` in the **Configuration** tab (it is the default).
- Avoid keeping the Web UI open longer than needed — the UI polls status periodically.
- If you are on 32-bit (`armv7`) hardware, the add-on falls back to `normal` automatically; this is expected and the resource use will be higher.

**Re-linking the Signal account**
- On your phone, open **Signal → Settings → Linked devices**, tap the Home Assistant device and **remove** it.
- Reopen the add-on Web UI: once the account is no longer linked, the add-on automatically shows a fresh QR code to link again.

**Entities missing after adding a chat partner**
- Reload the integration: **Settings → Devices & Services → Signalbot → three-dot menu → Reload**.

**Log access**
- Open the add-on page in Supervisor and click the **Log** tab.
