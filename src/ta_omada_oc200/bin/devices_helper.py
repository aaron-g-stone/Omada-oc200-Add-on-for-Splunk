import json
import time

import import_declare_test  # noqa: F401  (sets up sys.path for vendored lib/)
from splunklib import modularinput as smi

from omada_client import OmadaApiError, OmadaAuthError, flatten
from omada_common import build_client, logger_for_input, get_log_level, resolve_site_ids, filter_selected_fields

SOURCETYPE = "omada:device"
REQUIRED_FIELDS = {"mac", "name", "type", "status", "site_id"}


def validate_input(definition: smi.ValidationDefinition):
    # Connection is validated on account save (see ta_omada_oc200_rh_account.py); nothing
    # input-specific to check here beyond what the UI field validators already enforce.
    return


def stream_events(inputs: smi.InputDefinition, event_writer: smi.EventWriter):
    for input_name, input_item in inputs.inputs.items():
        normalized_input_name = input_name.split("/")[-1]
        logger = logger_for_input(normalized_input_name)
        try:
            session_key = inputs.metadata["session_key"]
            logger.setLevel(get_log_level(logger, session_key))
            logger.info("devices input start name=%s", normalized_input_name)

            client = build_client(session_key, input_item.get("account"), logger)
            index = input_item.get("index")
            site_ids = resolve_site_ids(client, input_item.get("site_filter"), logger)

            now = time.time()
            total = 0
            for site_id in site_ids:
                try:
                    devices = client.list_devices(site_id)
                except (OmadaAuthError, OmadaApiError) as e:
                    logger.error("failed to list devices for site=%s: %s", site_id, e)
                    continue
                for device in devices:
                    record = flatten(device)
                    record["site_id"] = site_id
                    record = filter_selected_fields(record, input_item.get("fields"), REQUIRED_FIELDS)
                    event_writer.write_event(
                        smi.Event(
                            data=json.dumps(record, ensure_ascii=False, default=str),
                            index=index,
                            sourcetype=SOURCETYPE,
                            source=normalized_input_name,
                            time=now,
                        )
                    )
                    total += 1

            logger.info("devices input end name=%s events=%d sites=%d", normalized_input_name, total, len(site_ids))
        except (OmadaAuthError, OmadaApiError) as e:
            logger.error("Omada API error in devices input %s: %s", normalized_input_name, e)
        except Exception as e:
            logger.exception("unhandled exception in devices input %s: %s", normalized_input_name, e)
