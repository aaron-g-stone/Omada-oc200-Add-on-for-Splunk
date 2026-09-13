import import_declare_test

from splunktaucclib.rest_handler.endpoint import (
    field,
    validator,
    RestModel,
    SingleModel,
)
from splunktaucclib.rest_handler import admin_external, util
from splunktaucclib.rest_handler.admin_external import AdminExternalHandler
from splunktaucclib.rest_handler.error import RestError
import logging

from omada_client import OmadaClient, OmadaAuthError, OmadaApiError
from omada_common import logger_for_input

util.remove_http_proxy_env_vars()


special_fields = [
    field.RestField(
        'name',
        required=True,
        encrypted=False,
        default=None,
        validator=validator.AllOf(
            validator.Pattern(
                regex=r"""^[a-zA-Z]\w*$""",
            ),
            validator.String(
                max_len=100,
                min_len=1,
            )
        )
    )
]

fields = [
    field.RestField(
        'base_url',
        required=True,
        encrypted=False,
        default=None,
        validator=validator.Pattern(
            regex=r"""^https?://.+$""",
        )
    ),
    field.RestField(
        'omadac_id',
        required=True,
        encrypted=False,
        default=None,
        validator=None
    ),
    field.RestField(
        'auth_type',
        required=True,
        encrypted=False,
        default='client_credentials',
        validator=None
    ),
    field.RestField(
        'client_id',
        required=True,
        encrypted=False,
        default=None,
        validator=None
    ),
    field.RestField(
        'client_secret',
        required=True,
        encrypted=True,
        default=None,
        validator=None
    ),
    field.RestField(
        'authorization_code',
        required=False,
        encrypted=True,
        default=None,
        validator=None
    ),
    field.RestField(
        'redirect_uri',
        required=False,
        encrypted=False,
        default=None,
        validator=None
    ),
    field.RestField(
        'refresh_token',
        required=False,
        encrypted=True,
        default=None,
        validator=None
    ),
    field.RestField(
        'verify_ssl',
        required=False,
        encrypted=False,
        default=True,
        validator=None
    )
]
model = RestModel(fields, name=None, special_fields=special_fields)


endpoint = SingleModel(
    'ta_omada_oc200_account',
    model,
    config_name='account',
    need_reload=False,
)


class OmadaAccountValidationHandler(AdminExternalHandler):
    """Makes a real authenticated call (listSites) against the Omada controller before
    an account is allowed to save, so bad credentials/URL fail fast with a clear error
    instead of surfacing hours later in the TA log. For Authorization Code mode, this
    is also where the one-time authorization code actually gets exchanged for tokens -
    the resulting refresh token is written back into the saved account (and the
    one-time code cleared) so it isn't reused on a later edit, which Omada would
    reject since these codes are single-use."""

    def _validate_connection(self):
        payload = self.payload
        base_url = payload.get('base_url')
        omadac_id = payload.get('omadac_id')
        client_id = payload.get('client_id')
        client_secret = payload.get('client_secret')
        auth_type = payload.get('auth_type') or 'client_credentials'
        verify_ssl = str(payload.get('verify_ssl', True)) in ('1', 'true', 'True', True)

        if not all([base_url, omadac_id, client_id, client_secret]):
            # required-field validators already cover this; skip a redundant live call
            return

        if auth_type == 'authorization_code':
            authorization_code = payload.get('authorization_code')
            existing_refresh_token = payload.get('refresh_token')
            if not authorization_code and not existing_refresh_token:
                raise RestError(
                    400,
                    "Authorization Code mode requires either a one-time Authorization "
                    "Code (first save) or an existing refresh token (subsequent edits).",
                )
        else:
            authorization_code = None
            existing_refresh_token = None

        logger = logger_for_input('account_validation')
        client = OmadaClient(
            base_url=base_url,
            omadac_id=omadac_id,
            client_id=client_id,
            client_secret=client_secret,
            verify_ssl=verify_ssl,
            proxies=None,
            logger=logger,
            timeout=15,
            auth_type=auth_type,
            authorization_code=authorization_code,
            redirect_uri=payload.get('redirect_uri') or None,
            refresh_token=existing_refresh_token,
        )
        try:
            client.list_sites()
        except OmadaAuthError as e:
            raise RestError(400, f"Could not authenticate to the Omada controller: {e}")
        except OmadaApiError as e:
            raise RestError(400, f"Omada controller rejected the request: {e}")
        except Exception as e:
            raise RestError(400, f"Could not reach the Omada controller at {base_url}: {e}")

        if auth_type == 'authorization_code' and client.refresh_token:
            # persist whatever refresh token resulted from this save (new exchange or
            # rotation on refresh), and clear the one-time code so it can't be resubmitted
            self.payload['refresh_token'] = client.refresh_token
            self.payload['authorization_code'] = ''

    def handleCreate(self, confInfo):
        self._validate_connection()
        AdminExternalHandler.handleCreate(self, confInfo)

    def handleEdit(self, confInfo):
        self._validate_connection()
        AdminExternalHandler.handleEdit(self, confInfo)


if __name__ == '__main__':
    logging.getLogger().addHandler(logging.NullHandler())
    admin_external.handle(
        endpoint,
        handler=OmadaAccountValidationHandler,
    )
