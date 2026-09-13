"""
Shared client for the Omada Open API (local hardware controller, e.g. OC200).

Auth: supports the two Open API application modes.
  - "client_credentials" (headless app-to-app): POST with a raw JSON body
    {omadacId, client_id, client_secret} to
      <base_url>/openapi/authorize/token?grant_type=client_credentials
  - "authorization_code" (delegated to a specific Omada operator): a one-time
    authorization code (obtained by the user visiting the controller's auth URL in a
    browser) is exchanged once for an access+refresh token pair; every call after that
    uses grant_type=refresh_token. Refresh tokens appear to rotate on each use, so the
    latest one must be persisted back into the account config (see omada_common.py's
    on_refresh_token wiring) or the *next* refresh will fail with a stale token.

Unlike standard OAuth2, the resulting access token is NOT sent as "Bearer <token>" -
Omada requires the non-standard header:
  Authorization: AccessToken=<token>

Community reports (firmware 6.2.x) indicate the local Open API enforces an undocumented
*daily* call quota even though it's a LAN-only endpoint, and some resource paths (notably
/clients) have been inconsistent between v1/v2 across patch levels. This client targets
v1 throughout, retries on 429 with backoff, and logs quota-looking errors distinctly so
they're easy to spot in the TA log.
"""
import json
import re
import time

import requests

DEFAULT_TIMEOUT = 30
TOKEN_REFRESH_SKEW_SECONDS = 120  # re-auth this many seconds before actual expiry


class OmadaAuthError(Exception):
    pass


class OmadaApiError(Exception):
    pass


_CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")


def _camel_to_snake(name: str) -> str:
    return _CAMEL_RE.sub("_", name).lower()


def flatten(obj, prefix="", out=None):
    """Flatten nested dict/list JSON into a single-level dict with snake_case,
    underscore-joined keys (never dots), matching the project's field-naming standard.
    Lists of scalars are joined into a comma-separated string. Lists of objects are
    flattened element-by-element with an index suffix (e.g. wan_port_statistics_0_download)
    rather than dropped - important for endpoints like internet-info whose useful numeric
    fields can sit inside a nested array (e.g. per-WAN-port stats on multi-WAN gateways)."""
    if out is None:
        out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = _camel_to_snake(str(k)).lstrip("_")
            full_key = f"{prefix}_{key}" if prefix else key
            flatten(v, full_key, out)
    elif isinstance(obj, list):
        if all(not isinstance(i, (dict, list)) for i in obj):
            out[prefix] = ",".join(str(i) for i in obj)
        else:
            for idx, item in enumerate(obj):
                flatten(item, f"{prefix}_{idx}", out)
    else:
        out[prefix] = obj
    return out


class OmadaClient:
    def __init__(self, base_url, omadac_id, client_id, client_secret,
                 verify_ssl=True, proxies=None, logger=None, timeout=DEFAULT_TIMEOUT,
                 auth_type="client_credentials", authorization_code=None,
                 redirect_uri=None, refresh_token=None, on_refresh_token=None):
        """
        auth_type: "client_credentials" (headless app-to-app) or "authorization_code"
        (delegated to an Omada operator). For authorization_code, pass EITHER
        authorization_code (first-ever use - a one-time code, exchanged once and then
        discarded) OR refresh_token (every run after that). on_refresh_token, if given,
        is called with the newest refresh token any time one is (re)issued, so the
        caller can persist it (e.g. back into the account's stored config) - Omada
        refresh tokens are typically rotating, so the one used to obtain them stops
        working once a new one is issued.
        """
        self.base_url = base_url.rstrip("/")
        self.omadac_id = omadac_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.verify_ssl = verify_ssl
        self.proxies = proxies
        self.log = logger
        self.timeout = timeout
        self.auth_type = auth_type
        self.authorization_code = authorization_code
        self.redirect_uri = redirect_uri
        self.refresh_token = refresh_token
        self.on_refresh_token = on_refresh_token
        self.session = requests.Session()
        self._access_token = None
        self._token_expires_at = 0

    # ---------------------------------------------------------------- auth
    def _post_token(self, grant_type, extra_body):
        url = f"{self.base_url}/openapi/authorize/token"
        body = {
            "omadacId": self.omadac_id,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }
        body.update(extra_body)
        try:
            r = self.session.post(
                url,
                params={"grant_type": grant_type},
                headers={"Content-Type": "application/json"},
                data=json.dumps(body),
                proxies=self.proxies,
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
        except requests.RequestException as e:
            raise OmadaAuthError(f"token request transport error: {e}")

        try:
            payload = r.json()
        except ValueError:
            raise OmadaAuthError(f"token endpoint returned non-JSON (HTTP {r.status_code})")

        # Omada wraps everything, including auth errors, in HTTP 200 with errorCode != 0
        error_code = payload.get("errorCode")
        if error_code not in (0, None) or "result" not in payload:
            raise OmadaAuthError(
                f"token request (grant_type={grant_type}) failed: errorCode={error_code} msg={payload.get('msg')}"
            )
        return payload["result"]

    def _apply_token_result(self, result):
        token = result.get("accessToken")
        if not token:
            raise OmadaAuthError(f"token response missing accessToken: {result}")
        expires_in = int(result.get("expiresIn", 7200))
        self._access_token = token
        self._token_expires_at = time.time() + max(expires_in - TOKEN_REFRESH_SKEW_SECONDS, 30)
        new_refresh_token = result.get("refreshToken")
        if new_refresh_token and new_refresh_token != self.refresh_token:
            self.refresh_token = new_refresh_token
            if self.on_refresh_token:
                self.on_refresh_token(new_refresh_token)
        if self.log:
            self.log.info("obtained new Omada access token, expires_in=%ss", expires_in)

    def _fetch_token(self):
        if self.auth_type == "authorization_code":
            if self.refresh_token:
                result = self._post_token("refresh_token", {"refreshToken": self.refresh_token})
            elif self.authorization_code:
                extra = {"code": self.authorization_code}
                if self.redirect_uri:
                    extra["redirectUri"] = self.redirect_uri
                result = self._post_token("authorization_code", extra)
            else:
                raise OmadaAuthError(
                    "authorization_code auth mode configured but no authorization code "
                    "or refresh token is available - re-enter the one-time authorization "
                    "code on the account and save."
                )
        else:
            result = self._post_token("client_credentials", {})
        self._apply_token_result(result)

    def _ensure_token(self):
        if not self._access_token or time.time() >= self._token_expires_at:
            self._fetch_token()

    # ------------------------------------------------------------- request
    def _request(self, method, path, params=None, max_retries=5, _retried_auth=False):
        self._ensure_token()
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"AccessToken={self._access_token}",
            "Content-Type": "application/json",
        }
        for attempt in range(max_retries):
            try:
                r = self.session.request(
                    method, url, params=params, headers=headers,
                    proxies=self.proxies, timeout=self.timeout, verify=self.verify_ssl,
                )
            except requests.RequestException as e:
                if self.log:
                    self.log.warning("attempt=%d transient error calling %s: %s", attempt, path, e)
                time.sleep(min(2 ** attempt, 60))
                continue

            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", min(2 ** attempt, 60)))
                if self.log:
                    self.log.warning(
                        "429 rate limited on %s, sleeping %ss (this is often the Omada "
                        "*daily* quota, not a burst limit - consider raising the input interval)",
                        path, wait,
                    )
                time.sleep(wait)
                continue

            if r.status_code == 401 and not _retried_auth:
                if self.log:
                    self.log.info("401 from %s, refreshing token and retrying once", path)
                self._access_token = None
                return self._request(method, path, params=params, max_retries=max_retries, _retried_auth=True)

            r.raise_for_status()
            try:
                payload = r.json()
            except ValueError:
                raise OmadaApiError(f"{path}: non-JSON response (HTTP {r.status_code})")

            error_code = payload.get("errorCode")
            if error_code not in (0, None):
                msg = payload.get("msg", "")
                if error_code in (-44112, -44113) or "quota" in msg.lower() or "limit" in msg.lower():
                    if self.log:
                        self.log.error("Omada API call-quota error on %s: errorCode=%s msg=%s", path, error_code, msg)
                raise OmadaApiError(f"{path}: errorCode={error_code} msg={msg}")

            return payload.get("result")
        raise OmadaApiError(f"giving up on {path} after {max_retries} attempts")

    def get(self, path, params=None):
        return self._request("GET", path, params=params)

    def get_paginated(self, path, page_size=100, extra_params=None):
        """Yields individual records from an Omada list endpoint. Always sends page/pageSize
        (the API silently returns nothing useful on some firmware builds if they're
        omitted - see devices/clients endpoints). Handles both response shapes seen in the
        wild: the standard wrapper {"totalRows": N, "data": [...]}, and a bare list (some
        firmware builds return the full unpaginated array regardless of the params)."""
        page = 1
        while True:
            params = {"page": page, "pageSize": page_size}
            if extra_params:
                params.update(extra_params)
            result = self.get(path, params=params)
            if isinstance(result, list):
                # bare list: the whole result set in one go, nothing left to page through
                for row in result:
                    yield row
                break
            result = result or {}
            data = result.get("data", [])
            for row in data:
                yield row
            total_rows = result.get("totalRows", len(data))
            if not data or page * page_size >= total_rows:
                break
            page += 1

    # ------------------------------------------------------------ resources
    def list_sites(self):
        path = f"/openapi/v1/{self.omadac_id}/sites"
        return list(self.get_paginated(path, page_size=100))

    def list_devices(self, site_id):
        path = f"/openapi/v1/{self.omadac_id}/sites/{site_id}/devices"
        return list(self.get_paginated(path, page_size=100))

    def get_device_stat(self, site_id):
        path = f"/openapi/v1/{self.omadac_id}/sites/{site_id}/dashboard/device-stat"
        return self.get(path)

    def list_clients(self, site_id, page_size=100):
        path = f"/openapi/v1/{self.omadac_id}/sites/{site_id}/clients"
        return list(self.get_paginated(path, page_size=page_size))

    def get_internet_info(self, site_id):
        path = f"/openapi/v1/{self.omadac_id}/sites/{site_id}/internet"
        return self.get(path)
