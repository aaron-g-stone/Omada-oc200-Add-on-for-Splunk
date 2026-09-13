import time

import import_declare_test  # noqa: F401
from splunklib import modularinput as smi

from omada_client import OmadaApiError, OmadaAuthError, flatten
from omada_common import (
    build_client, build_metrics_line, logger_for_input, get_log_level, resolve_site_ids,
    filter_selected_fields, apply_renames, CLIENT_FIELD_RENAMES,
)

SOURCETYPE = "omada:client_stats:metrics"
METRIC_PREFIX = "Omada.Clients"
REQUIRED_FIELDS = {"mac", "name", "ip", "site_id"}
# "ip" is included as a dimension deliberately (per request: traffic_down/up/active BY ip),
# alongside mac/name/site_id so records stay correlatable.
DIMENSION_KEYS = ("site_id", "mac", "name", "ip")
# "active" is commonly a real boolean field (client currently connected) rather than a
# number, so build_metrics_line()'s default numeric-only filter would drop it - but it
# was explicitly requested as a metric here, so convert it (post-rename) to 1/0 before
# handing the record to build_metrics_line().
BOOLEAN_METRIC_FIELDS = ("active",)


def validate_input(definition: smi.ValidationDefinition):
    return


def stream_events(inputs: smi.InputDefinition, event_writer: smi.EventWriter):
    for input_name, input_item in inputs.inputs.items():
        normalized_input_name = input_name.split("/")[-1]
        logger = logger_for_input(normalized_input_name)
        try:
            session_key = inputs.metadata["session_key"]
            logger.setLevel(get_log_level(logger, session_key))
            logger.info("client_stats input start name=%s", normalized_input_name)

            client = build_client(session_key, input_item.get("account"), logger)
            index = input_item.get("index")
            site_ids = resolve_site_ids(client, input_item.get("site_filter"), logger)

            now = time.time()
            total = 0
            records_seen = 0
            sample_logged = False
            for site_id in site_ids:
                try:
                    clients = client.list_clients(site_id)
                except (OmadaAuthError, OmadaApiError) as e:
                    logger.error("failed to list clients for site=%s: %s", site_id, e)
                    continue
                for c in clients:
                    records_seen += 1
                    record = flatten(c)
                    record = apply_renames(record, CLIENT_FIELD_RENAMES)
                    record["site_id"] = site_id
                    for bf in BOOLEAN_METRIC_FIELDS:
                        if isinstance(record.get(bf), bool):
                            record[bf] = int(record[bf])
                    record = filter_selected_fields(record, input_item.get("fields"), REQUIRED_FIELDS)
                    if not sample_logged:
                        logger.debug("sample flattened client record keys/types: %s",
                                     {k: type(v).__name__ for k, v in record.items()})
                        sample_logged = True
                    line = build_metrics_line(record, METRIC_PREFIX, DIMENSION_KEYS)
                    if line:
                        event_writer.write_event(
                            smi.Event(
                                data=line,
                                index=index,
                                sourcetype=SOURCETYPE,
                                source=normalized_input_name,
                                time=now,
                            )
                        )
                        total += 1

            if records_seen and not total:
                logger.warning(
                    "client_stats: fetched %d client record(s) across %d site(s) but "
                    "extracted 0 numeric fields to emit as metrics - enable DEBUG "
                    "logging and check the 'sample flattened client record keys/types' "
                    "line above for the actual shape.",
                    records_seen, len(site_ids),
                )
            logger.info("client_stats input end name=%s events_seen=%d metric_events=%d sites=%d",
                        normalized_input_name, records_seen, total, len(site_ids))
        except (OmadaAuthError, OmadaApiError) as e:
            logger.error("Omada API error in client_stats input %s: %s", normalized_input_name, e)
        except Exception as e:
            logger.exception("unhandled exception in client_stats input %s: %s", normalized_input_name, e)
