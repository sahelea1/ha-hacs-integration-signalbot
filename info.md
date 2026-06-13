## Signalbot

Signalbot integrates Home Assistant with [Signal](https://signal.org) via the [signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api) HTTP service, letting you send and receive Signal messages directly from your automations and scripts.

### Features

- **UI config flow** — link Home Assistant to a Signal account by scanning a QR code with the Signal app, or register/use an existing number
- **Chat partners / recipients** — manage recipients by friendly name, phone number (E.164), and/or @username through the integration's *Configure* dialog
- **Per-recipient notify entities** — each recipient becomes a `notify.signalbot_<name>` entity usable in automations
- **`signalbot.send_message` service** — send messages with optional file attachments to one or more recipients
- **Receive messages** — fires a `signalbot_message_received` event and exposes a `sensor.signalbot_last_message` sensor
- **Link-status sensor** — monitor whether the Signal account connection is healthy

> **Prerequisite:** A running [signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api) instance (local or remote) is required. See the [README](https://github.com/sahelea1/ha-hacs-integration-signalbot) for setup instructions.
