import time

import import_declare_test  # noqa: F401
from splunklib import modularinput as smi

from omada_client import OmadaApiError, OmadaAuthError, flatten
from omada_common import build_client, build_metrics_line, logger_for_input, get_log_level, resolve_site_ids, filter_selected_fields

SOURCETYPE = "omada:device_stats:metrics"
METRIC_PREFIX = "Omada.Devices"
REQUIRED_FIELDS = {"mac", "name", "site_id"}
# NOTE: exact field names (cpuUtil/memUtil/etc.) are per community reports on fw 6.2.14.x
# and have not been confirmed against this specific controller yet - build_metrics_line()
# picks up whatever numeric, non-identifier fields are actually present, so this input
# degrades gracefully (fewer metrics) rather than failing if some fields are absent, but
# should be spot-checked with `| mcatalog values(metric_name)` after first live run.
DIMENSION_KEYS = ("site_id", "mac", "name", "type", "model")


def validate_input(definition: smi.ValidationDefinition):
    return


def stream_events(inputs: smi.InputDefinition, event_writer: smi.EventWriter):
    for input_name, input_item in inputs.inputs.items():
        normalized_input_name = input_name.split("/")[-1]
        logger = logger_for_input(normalized_input_name)
        try:
            session_key = inputs.metadata["session_key"]
            logger.setLevel(get_log_level(logger, session_key))
            logger.info("device_stats input start name=%s", normalized_input_name)

            client = build_client(session_key, input_item.get("account"), logger)
            index = input_item.get("index")
            site_ids = resolve_site_ids(client, input_item.get("site_filter"), logger)

            now = time.time()
            total = 0
            records_seen = 0
            sample_logged = False
            for site_id in site_ids:
                try:
                    devices = client.list_devices(site_id)
                except (OmadaAuthError, OmadaApiError) as e:
                    logger.error("failed to list devices for site=%s: %s", site_id, e)
                    continue
                for device in devices:
                    records_seen += 1
                    record = flatten(device)
                    record["site_id"] = site_id
                    record = filter_selected_fields(record, input_item.get("fields"), REQUIRED_FIELDS)
                    if not sample_logged:
                        logger.debug("sample flattened device record keys/types: %s",
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
                    "device_stats: fetched %d device record(s) across %d site(s) but "
                    "extracted 0 numeric fields to emit as metrics - the field names/types "
                    "this firmware returns don't match what build_metrics_line() expects; "
                    "enable DEBUG logging and check the 'sample flattened device record "
                    "keys/types' line above for the actual shape.",
                    records_seen, len(site_ids),
                )
            logger.info("device_stats input end name=%s events_seen=%d metric_events=%d sites=%d",
                        normalized_input_name, records_seen, total, len(site_ids))
        except (OmadaAuthError, OmadaApiError) as e:
            logger.error("Omada API error in device_stats input %s: %s", normalized_input_name, e)
        except Exception as e:
            logger.exception("unhandled exception in device_stats input %s: %s", normalized_input_name, e)
