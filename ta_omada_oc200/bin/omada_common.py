import logging
import json
from urllib.parse import quote

from solnlib import conf_manager, log

from omada_client import OmadaClient, flatten

ADDON_NAME = "ta_omada_oc200"


def logger_for_input(input_name: str) -> logging.Logger:
    return log.Logs().get_logger(f"{ADDON_NAME.lower()}_{input_name}")


def _get_conf(session_key, conf_name):
    cfm = conf_manager.ConfManager(
        session_key,
        ADDON_NAME,
        realm=f"__REST_CREDENTIAL__#{ADDON_NAME}#configs/conf-{conf_name}",
    )
    return cfm.get_conf(conf_name)


def get_account(session_key, account_name):
    conf = _get_conf(session_key, f"{ADDON_NAME}_account")
    return conf.get(account_name)


def get_log_level(logger, session_key):
    return conf_manager.get_log_level(
        logger=logger,
        session_key=session_key,
        app_name=ADDON_NAME,
        conf_name=f"{ADDON_NAME}_settings",
    )


def get_proxy_settings(session_key):
    try:
        stanza = _get_conf(session_key, f"{ADDON_NAME}_settings").get("proxy")
    except Exception:
        return None
    if not stanza or stanza.get("proxy_enabled") not in ("1", 1, "true", "True"):
        return None
    ptype = stanza.get("proxy_type", "http")
    if ptype == "socks5" and stanza.get("proxy_rdns") in ("1", "true"):
        ptype = "socks5h"
    auth = ""
    if stanza.get("proxy_username"):
        auth = f'{quote(stanza["proxy_username"], safe="")}:{quote(stanza.get("proxy_password", ""), safe="")}@'
    url = f'{ptype}://{auth}{stanza["proxy_url"]}:{stanza["proxy_port"]}'
    return {"http": url, "https": url}


def persist_account_field(session_key, account_name, field, value, logger=None):
    """Write a single field back into the stored account stanza (used to persist a
    rotated refresh token so the next run doesn't retry a stale one). Best-effort -
    logs and swallows failures rather than crashing a poll cycle over a persistence
    hiccup; the current run's in-memory token still works either way."""
    try:
        conf = _get_conf(session_key, f"{ADDON_NAME}_account")
        conf.update(account_name, {field: value})
    except Exception as e:
        if logger:
            logger.warning("could not persist rotated %s for account=%s: %s", field, account_name, e)


def build_client(session_key, account_name, logger):
    account = get_account(session_key, account_name)
    verify_ssl = str(account.get("verify_ssl", "1")) in ("1", "true", "True")
    proxies = get_proxy_settings(session_key)
    auth_type = account.get("auth_type") or "client_credentials"

    on_refresh_token = None
    if auth_type == "authorization_code":
        def on_refresh_token(new_token, _session_key=session_key, _account_name=account_name, _logger=logger):
            persist_account_field(_session_key, _account_name, "refresh_token", new_token, _logger)

    return OmadaClient(
        base_url=account.get("base_url"),
        omadac_id=account.get("omadac_id"),
        client_id=account.get("client_id"),
        client_secret=account.get("client_secret"),
        verify_ssl=verify_ssl,
        proxies=proxies,
        logger=logger,
        auth_type=auth_type,
        authorization_code=account.get("authorization_code") or None,
        redirect_uri=account.get("redirect_uri") or None,
        refresh_token=account.get("refresh_token") or None,
        on_refresh_token=on_refresh_token,
    )


NUMERIC_TYPES = (int, float)
# fields we never want treated as a metric measure even though they're numeric
# (identifiers, ports, enum-style status codes, timestamps)
NON_METRIC_SUFFIXES = ("_id", "_mac", "_port", "_type", "_status", "_time", "_ms", "_ver")


def _to_numeric(value):
    """Coerce a value to int/float if it plausibly is one, including numeric strings -
    several Omada endpoints serialize stats (e.g. cpuUtil, uptime) as strings rather than
    JSON numbers. Returns None if it isn't numeric. Booleans are explicitly excluded even
    though bool is a subclass of int."""
    if isinstance(value, bool):
        return None
    if isinstance(value, NUMERIC_TYPES):
        return value
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            try:
                return float(s)
            except ValueError:
                return None
    return None


def is_metric_field(key, value):
    if any(key.endswith(suf) for suf in NON_METRIC_SUFFIXES):
        return False
    return _to_numeric(value) is not None


def build_metrics_line(flat_record, metric_prefix, dimension_keys):
    """Build ONE combined metric event as a JSON object using Splunk's native
    direct-to-metrics-index convention: a field literally named 'metric_name:<metric>'
    (colon included) with a floating-point value. This MUST be emitted as JSON with
    `INDEXED_EXTRACTIONS = json` in props.conf - Splunk's default space-separated
    key=value auto-extraction does not accept a colon inside a field name, so the
    plain-text 'metric_name:<x>=<y> dim=val' form (tried in 0.1.3) never actually
    produces a field named 'metric_name:<x>' for the metrics pipeline to recognize,
    even though it looks right in the raw text. Values are always emitted as JSON
    floats (Splunk's error message is explicit about requiring "floating point
    values" - a bare JSON integer is rejected). Dimension_keys are included as plain
    string fields (no prefix). Returns None if no measures were found."""
    event = {}
    for key, value in sorted(flat_record.items()):
        if any(key.endswith(suf) for suf in NON_METRIC_SUFFIXES):
            continue
        numeric_value = _to_numeric(value)
        if numeric_value is None:
            continue
        event[f"metric_name:{metric_prefix}.{key}"] = float(numeric_value)
    if not event:
        return None
    for dim_key in dimension_keys:
        val = flat_record.get(dim_key)
        if val not in (None, ""):
            event[dim_key] = str(val)
    return json.dumps(event, ensure_ascii=False)


def filter_selected_fields(flat_record, selected_fields_value, required_fields):
    """Apply the input's "Fields" picker: if nothing was selected (empty/unset - the
    default, since the picker is optional), return the record unchanged. Otherwise keep
    only the selected fields plus required_fields (which are always kept regardless of
    selection, matching the "Fields" picker's pre-selected/locked entries)."""
    if not selected_fields_value:
        return flat_record
    if isinstance(selected_fields_value, (list, tuple)):
        selected = {str(s).strip() for s in selected_fields_value if str(s).strip()}
    else:
        selected = {s.strip() for s in str(selected_fields_value).split(",") if s.strip()}
    if not selected:
        return flat_record
    keep = selected | set(required_fields)
    return {k: v for k, v in flat_record.items() if k in keep}


def fetch_sample_record(session_key, account_name, service, logger):
    """Make one live call through the given account to fetch a single sample record for
    the given input service ('devices'/'device_stats'/'clients'/'internet'), and return
    it flattened - or None if unavailable (no account, no sites, empty response, or an
    API error, which is logged but not raised so a field-picker fetch degrades to an
    empty list rather than a 500)."""
    if not account_name or service not in _SAMPLE_FETCHERS:
        return None
    try:
        client = build_client(session_key, account_name, logger)
        sites = client.list_sites()
        for site in sites[:1]:
            site_id = site.get("siteId") or site.get("id")
            if not site_id:
                continue
            result = _SAMPLE_FETCHERS[service](client, site_id)
            flat = None
            if isinstance(result, list) and result:
                flat = flatten(result[0])
            elif isinstance(result, dict) and result:
                flat = flatten(result)
            if flat is not None:
                if service in ("clients", "client_stats"):
                    flat = apply_renames(flat, CLIENT_FIELD_RENAMES)
                return flat
    except Exception as e:
        if logger:
            logger.warning("fields picker: could not fetch %s sample for account=%s: %s",
                            service, account_name, e)
    return None


_SAMPLE_FETCHERS = {
    "devices": lambda client, site_id: client.list_devices(site_id),
    "device_stats": lambda client, site_id: client.list_devices(site_id),
    "clients": lambda client, site_id: client.list_clients(site_id),
    "client_stats": lambda client, site_id: client.list_clients(site_id),
    "internet": lambda client, site_id: client.get_internet_info(site_id),
}

REQUIRED_FIELDS_BY_SERVICE = {
    "devices": {"mac", "name", "type", "status", "site_id"},
    "device_stats": {"mac", "name", "site_id"},
    "clients": {"mac", "name", "ip", "site_id"},
    "client_stats": {"mac", "name", "ip", "site_id"},
    "internet": {"site_id"},
}

# Field renames applied after flatten() for client records, on both the events (clients)
# and metrics (client_stats) inputs, and in the field picker so the displayed/selectable
# names always match what's actually ingested. Source names are the real, confirmed
# flatten() output field names on this firmware.
CLIENT_FIELD_RENAMES = {
    "traffic_down": "RxBps",
    "traffic_up": "TxBps",
}


def apply_renames(flat_record, rename_map):
    if not rename_map:
        return flat_record
    return {rename_map.get(k, k): v for k, v in flat_record.items()}


def resolve_site_ids(client, site_filter, logger):
    """Return the list of site IDs to poll: either the comma-separated filter the user
    configured, or every site the account can see."""
    filter_str = (site_filter or "").strip()
    if filter_str:
        return [s.strip() for s in filter_str.split(",") if s.strip()]
    sites = client.list_sites()
    ids = [s.get("siteId") or s.get("id") for s in sites if s.get("siteId") or s.get("id")]
    logger.info("no site filter configured, discovered %d site(s)", len(ids))
    return ids
