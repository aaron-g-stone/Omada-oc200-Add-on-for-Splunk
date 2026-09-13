import json
import time

import import_declare_test  # noqa: F401
from splunklib import modularinput as smi

from omada_client import OmadaApiError, OmadaAuthError, flatten
from omada_common import (
    build_client, logger_for_input, get_log_level, resolve_site_ids,
    filter_selected_fields, apply_renames, CLIENT_FIELD_RENAMES,
)

SOURCETYPE = "omada:client"
REQUIRED_FIELDS = {"mac", "name", "ip", "site_id"}


def validate_input(definition: smi.ValidationDefinition):
    return


def stream_events(inputs: smi.InputDefinition, event_writer: smi.EventWriter):
    for input_name, input_item in inputs.inputs.items():
        normalized_input_name = input_name.split("/")[-1]
        logger = logger_for_input(normalized_input_name)
        try:
            session_key = inputs.metadata["session_key"]
            logger.setLevel(get_log_level(logger, session_key))
            logger.info("clients input start name=%s", normalized_input_name)

            client = build_client(session_key, input_item.get("account"), logger)
            index = input_item.get("index")
            site_ids = resolve_site_ids(client, input_item.get("site_filter"), logger)

            now = time.time()
            total = 0
            for site_id in site_ids:
                try:
                    clients = client.list_clients(site_id)
                except (OmadaAuthError, OmadaApiError) as e:
                    logger.error("failed to list clients for site=%s: %s", site_id, e)
                    continue
                for c in clients:
                    record = flatten(c)
                    record = apply_renames(record, CLIENT_FIELD_RENAMES)
                    record["site_id"] = site_id
                    # Omada client records commonly carry a lastSeen/activity epoch-ms field;
                    # flatten() snake_cases it to last_seen / last_activity depending on
                    # firmware. Prefer the record's own time when present so activity events
                    # aren't all stamped with poll time; fall back to now otherwise.
                    event_time = now
                    for ts_field in ("last_seen", "last_activity", "active_time"):
                        raw_ts = record.get(ts_field)
                        if isinstance(raw_ts, (int, float)) and raw_ts > 0:
                            event_time = raw_ts / 1000.0 if raw_ts > 1e12 else raw_ts
                            break
                    record = filter_selected_fields(record, input_item.get("fields"), REQUIRED_FIELDS)
                    event_writer.write_event(
                        smi.Event(
                            data=json.dumps(record, ensure_ascii=False, default=str),
                            index=index,
                            sourcetype=SOURCETYPE,
                            source=normalized_input_name,
                            time=event_time,
                        )
                    )
                    total += 1

            logger.info("clients input end name=%s events=%d sites=%d", normalized_input_name, total, len(site_ids))
        except (OmadaAuthError, OmadaApiError) as e:
            logger.error("Omada API error in clients input %s: %s", normalized_input_name, e)
        except Exception as e:
            logger.exception("unhandled exception in clients input %s: %s", normalized_input_name, e)
