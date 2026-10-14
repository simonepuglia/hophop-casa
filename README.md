# HopHop Casa

![HopHop Casa icon](custom_components/hophop_casa/brand/icon.png)

Access Home Assistant remotely through an outbound HopHop Casa tunnel. A separate HopHop Casa subscription and token are required for each device. This is a custom integration, not an official Home Assistant integration.

## Install through HACS

1. If needed, install and configure [HACS](https://www.hacs.xyz/docs/use/download/download/).
2. HACS → menu → **Custom repositories**: enter `https://github.com/simonepuglia/hophop-casa` and select **Integration**.
3. Find **HopHop Casa**, download it, and fully restart Home Assistant.
4. Settings → Devices & services → Add integration → **HopHop Casa**.
5. Paste your device token. Keep the local Home Assistant address at `http://127.0.0.1:8123` for a normal Home Assistant OS installation, or enter the local address appropriate for your installation. The integration connects to `wss://hophop.casa/ws` automatically; there is no broker URL field.
6. When your device is Online on [HopHop Casa](https://hophop.casa), open its private link and use your normal HA login.

Create and verify your account, add your device, save its token, and activate its own subscription in the HopHop Casa web app. See the [setup guide](https://hophop.casa/setup.php). Additional devices need separate subscriptions.

## Manual installation

Copy `custom_components/hophop_casa` into the HA configuration directory (normally `/config/custom_components/hophop_casa`). Fully restart HA, then configure the integration as above.

## Updates and reloads

HACS downloads updates; fully restart HA afterwards to load changed Python files. Existing hophop_casa settings and tokens are retained on later updates. Migrating from remote_ha_client requires removing/re-adding the HA entry; reuse your existing server device/token. Reload the integration through your LOCAL HA address, because reloading the tunnel while accessing HA through it interrupts the connection you are using.

Version 1.0.1 fixes reload cancellation when the reload request passes through this integration’s own tunnel. The first public HACS release was 1.0.0. It includes bounded shutdown, recovery from DNS/502/disconnect failures, isolated HTTP/WebSocket workers, and sanitized diagnostics. See [CHANGELOG.md](CHANGELOG.md). Client version numbers are independent of server versions; this client works with server 1.4.0.

To change your device token or local Home Assistant address, use the integration's Configure menu. The broker address is managed automatically. Existing entries with a previously saved broker address retain it when you update their settings.

## Troubleshooting

If Offline, check that this device’s subscription is active, the token is correct, and DNS/server connectivity works. If Online but HA cannot open, check the local HA URL and port. Configure lets you update the token and local address. Download diagnostics from the **HopHop Casa** integration menu. Confirm `integration_domain` is `hophop_casa` and `version` is `1.0.1`; files from `remote_ha_client` describe the retired integration. Diagnostics also include entry_state, state, task_running, task_cancelled, stop_requested, shutdown_stage and sanitized errors. For additional logging, use:

```yaml
action: logger.set_level
data:
  custom_components.hophop_casa: debug
```

A reload over the remote link briefly disconnects that link and can show a network/502 error; reconnect afterwards. Version 1.0.1 allows Home Assistant to finish the reload even if that request disconnects. If unloading was already stuck under an older version, copy the updated files and perform a full HA restart once. If unloading still fails, collect the logs and diagnostics. Never post tokens or private access links in issues.

## Privacy

HopHop Casa uses encrypted HTTPS and WebSocket connections to provide remote access to Home Assistant. Service policy prohibits inspecting users’ forwarded content. Keep Home Assistant authentication enabled and treat device tokens and private links as passwords. See our Privacy Notice for details.

## License
The HopHop Casa client integration is licensed under Apache License 2.0. Access to the hosted HopHop Casa service requires a separate subscription and is governed by its Terms of Service.

## Reload investigation

See [RELOAD_INVESTIGATION.md](RELOAD_INVESTIGATION.md) for the reproduced failure, backend isolation audit, update steps and diagnostic commands.

## Retire the previous integration

If Remote HA Client is still configured, use your local HA address to disable/remove its old entry and remove its custom_components/remote_ha_client folder afterwards. Keep the paid device on the website and keep the HopHop Casa entry/token. A legacy entry receiving HTTP 401 is retrying an unauthorized token; it does not diagnose the new integration. Do not run both integrations for the same device token. Each separate installation needs a different device token and active subscription.
