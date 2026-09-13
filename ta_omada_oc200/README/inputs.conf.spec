[devices://<name>]
account = Omada controller account to use for this input.
fields = Select which fields to ingest for this input. The list is fetched live from the controller via the selected Account once one is chosen, reflecting whatever this firmware actually returns rather than a fixed guess - refreshes automatically if you change the Account. Leave empty to ingest every field. A small set of identifying fields (e.g. mac/name/site) is always included in the ingested data regardless of this selection, even if not checked here, so records stay correlatable.
index = (Default: default)
interval = Polling interval in seconds. The local Open API has an undocumented daily call quota - 60s across all sites/devices can exhaust it quickly; raise this if you see 429/quota errors in the TA log. (Default: 60)
site_filter = Comma-separated Omada site IDs to restrict collection to. Leave blank to collect all sites visible to this account.

[device_stats://<name>]
account = Omada controller account to use for this input.
fields = Select which fields to ingest for this input. The list is fetched live from the controller via the selected Account once one is chosen, reflecting whatever this firmware actually returns rather than a fixed guess - refreshes automatically if you change the Account. Leave empty to ingest every field. A small set of identifying fields (e.g. mac/name/site) is always included in the ingested data regardless of this selection, even if not checked here, so records stay correlatable.
index = (Default: default)
interval = Polling interval in seconds. This input emits metrics, not events - the destination index must be a metrics index. (Default: 60)
site_filter = Comma-separated Omada site IDs to restrict collection to. Leave blank to collect all sites visible to this account.

[clients://<name>]
account = Omada controller account to use for this input.
fields = Select which fields to ingest for this input. The list is fetched live from the controller via the selected Account once one is chosen, reflecting whatever this firmware actually returns rather than a fixed guess - refreshes automatically if you change the Account. Leave empty to ingest every field. A small set of identifying fields (e.g. mac/name/site) is always included in the ingested data regardless of this selection, even if not checked here, so records stay correlatable.
index = (Default: default)
interval = Polling interval in seconds. (Default: 60)
site_filter = Comma-separated Omada site IDs to restrict collection to. Leave blank to collect all sites visible to this account.

[internet://<name>]
account = Omada controller account to use for this input.
fields = Select which fields to ingest for this input. The list is fetched live from the controller via the selected Account once one is chosen, reflecting whatever this firmware actually returns rather than a fixed guess - refreshes automatically if you change the Account. Leave empty to ingest every field. A small set of identifying fields (e.g. mac/name/site) is always included in the ingested data regardless of this selection, even if not checked here, so records stay correlatable.
index = Destination events index. (Default: default)
interval = Polling interval in seconds. (Default: 60)
site_filter = Comma-separated Omada site IDs to restrict collection to. Leave blank to collect all sites visible to this account.

[client_stats://<name>]
account = Omada controller account to use for this input.
fields = Select which fields to ingest for this input. The list is fetched live from the controller via the selected Account once one is chosen. Leave empty to ingest every field. mac/name/ip/site_id are always included regardless of this selection, so records stay correlatable and can be grouped by IP.
index = (Default: default)
interval = Polling interval in seconds. This input emits metrics, not events - the destination index must be a metrics index. (Default: 60)
site_filter = Comma-separated Omada site IDs to restrict collection to. Leave blank to collect all sites visible to this account.
