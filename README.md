# Omada OC200 Add-on for Splunk

A Splunk Technology Add-on (built with the [UCC framework](https://splunk.github.io/addonfactory-ucc-generator/)) that collects device inventory, health metrics, connected-client activity, and WAN/internet status from a TP-Link Omada SDN Controller via the **Omada Open API**.

Built and tested against an **OC200 hardware controller** running firmware 6.2.x.

## Features

- **Five modular inputs** covering devices, per-device metrics, clients, per-client metrics, and WAN/internet status — see [Inputs](#inputs) below.
- **Two authentication modes**: Client Credentials (headless app-to-app) or Authorization Code (delegated to a specific Omada operator, with automatic refresh-token rotation and persistence).
- **Live field picker** on every input — select which fields to ingest from a list fetched from your actual controller, not a hardcoded guess. A small set of identifying fields is always kept regardless of selection, so records stay correlatable.
- **Connection validated on save** — a bad URL or credential is rejected immediately with a clear error, not hours later when the first poll fails.
- Proxy support (`http`/`socks4`/`socks5`) and standard UCC logging controls.
- Portable, dependency-pinned build (Python 3.9+, Splunk Enterprise/Cloud 9.x and 10.x).

## Inputs

| Input | Data | Type | Sourcetype |
|---|---|---|---|
| `devices` | Managed device inventory & status (APs, switches, gateway) | Events | `omada:device` |
| `device_stats` | Per-device numeric health fields (CPU/mem/traffic/uptime, whatever the controller exposes) | Metrics | `omada:device_stats:metrics` |
| `clients` | Connected client list & activity | Events | `omada:client` |
| `client_stats` | Per-client metrics (`RxBps`/`TxBps`/`active`/...), dimensioned by IP | Metrics | `omada:client_stats:metrics` |
| `internet` | WAN/internet uplink status | Events | `omada:internet` |

Every input shares the same field order: **Name → Account → Index → Interval → Fields → Site ID filter (optional)**.

Metrics inputs write Splunk's native `metric_name:<name>` JSON format directly into a metrics index — no HEC token or extra index-time configuration required.

## Prerequisites

1. On the OC200 controller: **Settings → Platform Integration → Open API** → create an application, grant it read access to the site(s) you want to monitor, and record:
   - **Omada ID** (`omadacId`) — shown via the eye icon on that page. This is a long hex string, *not* the application's display name.
   - **Client ID** and **Client Secret** (secret is shown only once).
   - For **Authorization Code** mode, also note the app's **Redirect URI** and obtain a one-time authorization code (see [Authentication modes](#authentication-modes)).
2. The controller's local management URL, e.g. `https://<controller-ip>:8043`.
3. A regular events index for `devices`/`clients`/`internet`, and a **metrics** index for `device_stats`/`client_stats` (**Settings → Indexes → New Index → Index Data Type: Metrics**).

## Installation

1. Install the `.tar.gz` package via **Apps → Manage Apps → Install app from file**, or `splunk install app ta_omada_oc200-<version>.tar.gz`, then restart Splunk if prompted.
2. Go to **Configuration → Account** and add an account (see below).
3. Go to **Inputs → Create New Input**, pick an input type, and configure it — see [Inputs](#inputs).

## Authentication modes

- **Client Credentials** (default) — headless app-to-app, matches a "Client" mode Open API application. Just Client ID + Client Secret.
- **Authorization Code** — delegated to a specific Omada operator account. Provide the one-time Authorization Code and the app's Redirect URI; the TA exchanges it for tokens on save and discards the code. The resulting refresh token is stored and renewed automatically from then on.

Saving an account makes a live connection check (`listSites`) — bad credentials or an unreachable controller are rejected immediately.

## Selecting which fields to ingest

Each input has a **Fields** picker: once you choose an **Account**, it fetches a live sample record from the controller through that account and lists whatever fields are actually present. Leave it empty to ingest every field. A small set of identifying fields (e.g. `mac`/`name`/`site_id`, per input) is always included in what's ingested regardless of this selection.

## Metrics naming

`device_stats` and `client_stats` metrics are namespaced so Splunk's Metrics Workspace displays them as a browsable tree:

- `Omada.Devices.<field>` — e.g. `Omada.Devices.cpu_util`, `Omada.Devices.mem_util`
- `Omada.Clients.<field>` — e.g. `Omada.Clients.RxBps`, `Omada.Clients.TxBps`, `Omada.Clients.active`, dimensioned by `site_id`/`mac`/`name`/`ip` so you can split or filter by client IP.

## Known limitations

- **Daily API call quota.** The local Open API appears to enforce an undocumented daily (not just burst) call-rate limit even though it's a LAN-only endpoint. Running all five inputs across every site at a short interval can exhaust it — watch the TA log for repeated 429/quota errors and raise the interval if you see them.
- **`internet` requires an Omada-managed gateway.** WAN/uplink data is gateway-specific; if your site's internet gateway isn't an Omada-managed device, this input will correctly return nothing.
- **AppInspect: 2 accepted `future_failure`s.** `python.required` is set to `3.9` to match this add-on's Splunk 9.x/10.x compatibility target, rather than the `3.13` AppInspect's forward-looking check now suggests — setting it to `3.13` would break compatibility with the stated target Splunk versions.

## Architecture

```
ta_omada_oc200/
├── globalConfig.json              # UCC config: account/proxy/logging tabs, 5 input services,
│                                   #   custom REST field-discovery endpoints (options.restHandlers)
├── additional_packaging.py        # Post-build hook: python.required fix for AppInspect
└── package/
    ├── bin/
    │   ├── omada_client.py            # Shared API client: OAuth2 (both modes), retries,
    │   │                               #   pagination, JSON flattening
    │   ├── omada_common.py            # Account/proxy config reading, field-picker data
    │   │                               #   fetching, metrics-line building, field renames
    │   ├── {devices,device_stats,clients,client_stats,internet}_helper.py
    │   │                               # One modular input each
    │   ├── ta_omada_oc200_rh_account.py           # Custom account REST handler (save-time
    │   │                                            #   connection validation)
    │   └── ta_omada_oc200_rh_fields_<service>.py  # Live field-discovery endpoints (one per
    │                                                #   input), raw splunk.admin.MConfigHandler
    └── default/
        └── props.conf              # Sourcetypes for the events inputs; index-time JSON
                                     #   extraction for the metrics inputs
```

## Building from source

Requires [`splunk-add-on-ucc-framework`](https://splunk.github.io/addonfactory-ucc-generator/) and `splunk-appinspect`.

```bash
pip install splunk-add-on-ucc-framework splunk-appinspect

# Build
ucc-gen build --source ta_omada_oc200/package --config ta_omada_oc200/globalConfig.json --ta-version <X.Y.Z>

# Strip non-portable compiled binaries (see verify_portability.py in the splunk-ta-builder skill)
python3 verify_portability.py output/ta_omada_oc200 --target-python 3.9 --fix

# Package
ucc-gen package --path output/ta_omada_oc200

# Validate
splunk-appinspect inspect ta_omada_oc200-<X.Y.Z>.tar.gz --mode precert --included-tags cloud
```

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the full version history.

## License

No license has been specified yet for this repository — add one (e.g. `LICENSE`) before publishing if you intend for others to use or contribute to it.
