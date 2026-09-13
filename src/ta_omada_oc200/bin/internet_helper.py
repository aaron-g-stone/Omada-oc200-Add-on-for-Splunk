import json
import time

import import_declare_test  # noqa: F401
from splunklib import modularinput as smi

from omada_client import OmadaApiError, OmadaAuthError, flatten
from omada_common import build_client, logger_for_input, get_log_level, resolve_site_ids, filter_selected_fields

# Switched from a metrics input to a regular events input: WAN/internet-uplink status
# data is largely non-numeric (connection state, ISP name, IP/gateway/DNS) rather than
# pure measurements, and if a site has no Omada-managed gateway device this endpoint
# legitimately returns nothing at all - both fit an events index better than forcing it
# through the metrics-only pipeline the way device_stats/client_stats do.
SOURCETYPE = "omada:internet"
REQUIRED_FIELDS = {"site_id"}


def validate_input(definition: smi.ValidationDefinition):
    return


def stream_events(inputs: smi.InputDefinition, event_writer: smi.EventWriter):
    for input_name, input_item in inputs.inputs.items():
        normalized_input_name = input_name.split("/")[-1]
        logger = logger_for_input(normalized_input_name)
        try:
            session_key = inputs.metadata["session_key"]
            logger.setLevel(get_log_level(logger, session_key))
            logger.info("internet input start name=%s", normalized_input_name)

            client = build_client(session_key, input_item.get("account"), logger)
            index = input_item.get("index")
            site_ids = resolve_site_ids(client, input_item.get("site_filter"), logger)

            now = time.time()
            total = 0
            for site_id in site_ids:
                try:
                    info = client.get_internet_info(site_id)
                except (OmadaAuthError, OmadaApiError) as e:
                    logger.error("failed to get internet info for site=%s: %s", site_id, e)
                    continue
                if not info:
                    # A common, legitimate cause: this site has no Omada-managed gateway
                    # device, so there's genuinely no WAN/uplink data to report - not
                    # necessarily a bug.
                    logger.warning(
                        "internet: empty/null response for site=%s (endpoint reachable, "
                        "no data returned - if this site's internet gateway isn't an "
                        "Omada-managed device, this is expected, not an error)", site_id,
                    )
                    continue
                # response may be a single object (one WAN) or a list (multi-WAN/load-balance)
                records = info if isinstance(info, list) else [info]
                for rec in records:
                    flat = flatten(rec)
                    flat["site_id"] = site_id
                    flat = filter_selected_fields(flat, input_item.get("fields"), REQUIRED_FIELDS)
                    event_writer.write_event(
                        smi.Event(
                            data=json.dumps(flat, ensure_ascii=False, default=str),
                            index=index,
                            sourcetype=SOURCETYPE,
                            source=normalized_input_name,
                            time=now,
                        )
                    )
                    total += 1

            logger.info("internet input end name=%s events=%d sites=%d", normalized_input_name, total, len(site_ids))
        except (OmadaAuthError, OmadaApiError) as e:
            logger.error("Omada API error in internet input %s: %s", normalized_input_name, e)
        except Exception as e:
            logger.exception("unhandled exception in internet input %s: %s", normalized_input_name, e)
