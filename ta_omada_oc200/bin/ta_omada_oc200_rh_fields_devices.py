"""
Custom admin_external REST handler backing the "Fields" live multiselect on the
devices input. Given the currently-selected account (passed as a dependency query param
by the UCC frontend), it makes one live call through that account to fetch a sample
record and returns its fields as selectable options.

Implemented as a raw splunk.admin.MConfigHandler subclass, matching the proven working
pattern from this project's ta_qld_open_data and ta_statseeker field pickers - NOT
through UCC's splunktaucclib account/input CRUD generation, because this isn't modeling
a config entity, just serving a dynamic lookup.
"""
import import_declare_test  # noqa: F401

import splunk.admin as admin

from omada_common import fetch_sample_record, logger_for_input, REQUIRED_FIELDS_BY_SERVICE

SERVICE = "devices"
REQUIRED = REQUIRED_FIELDS_BY_SERVICE.get(SERVICE, set())


class OmadaFieldsDevicesHandler(admin.MConfigHandler):
    def setup(self):
        # Read-only listing endpoint; no create/edit/delete actions supported.
        # "account" must be declared as a supported arg or splunkd strips it from
        # self.callerArgs.data before handleList() ever sees it.
        if self.requestedAction == admin.ACTION_LIST:
            self.supportedArgs.addOptArg("account")

    def handleList(self, confInfo):
        account_name = self._first(self.callerArgs.data.get("account"))
        if not account_name:
            # Nothing entered yet on the form; return an empty option list rather than erroring.
            return

        logger = logger_for_input("fields_picker_devices")
        try:
            session_key = self.getSessionKey()
            flat = fetch_sample_record(session_key, account_name, SERVICE, logger)
        except Exception as exc:  # noqa: BLE001 - surface as empty list, never break the UI
            logger.warning("field picker: could not fetch fields for account=%s: %s", account_name, exc)
            return

        if not flat:
            return

        for field_name in sorted(flat.keys()):
            stanza = confInfo[field_name]
            stanza["value"] = field_name
            stanza["label"] = field_name + ("  (required)" if field_name in REQUIRED else "")

    @staticmethod
    def _first(value):
        if isinstance(value, (list, tuple)):
            return value[0] if value else None
        return value


if __name__ == "__main__":
    admin.init(OmadaFieldsDevicesHandler, admin.CONTEXT_NONE)
