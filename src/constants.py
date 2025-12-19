# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Module to store constants for the charm."""

CONTAINER_NAME = "hive-metastore"
SERVICE_NAME = "hive-metastore"
DEFAULT_DATABASE_NAME = "hive_metastore"
HIVE_CONF_DIR = "/etc/hive-conf"
HIVE_SITE_PATH = f"{HIVE_CONF_DIR}/hive-site.xml"
POSTGRES_CA_PATH = f"{HIVE_CONF_DIR}/postgresql-ca.crt"
SCHEMATOOL_PATH = "/opt/hive/bin/schematool"
SCHEMATOOL_TIMEOUT = 600
HIVE_PORT = 9083
POSTGRES_RELATION = "postgresql"
