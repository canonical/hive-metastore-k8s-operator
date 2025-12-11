#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Hive Metastore Kubernetes charm."""

import base64
import logging
from typing import Dict, Optional
from xml.sax.saxutils import escape

import ops
from charms.data_platform_libs.v0.data_interfaces import (
    DatabaseCreatedEvent,
    DatabaseEndpointsChangedEvent,
    DatabaseRequires,
)
from charms.data_platform_libs.v0.data_models import TypedCharmBase
from ops.framework import StoredState
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from ops.pebble import APIError, ChangeError, ConnectionError, ExecError
from pydantic import ValidationError

from config import CharmConfig
import constants

logger = logging.getLogger(__name__)




class SchemaInitializationError(RuntimeError):
    """Raised when the Hive metastore schema cannot be initialised."""


class HiveMetastoreK8SOperatorCharm(TypedCharmBase[CharmConfig]):
    """Operator to configure and run the Hive Metastore workload."""

    config_type = CharmConfig
    _stored = StoredState()

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)

        self._stored.set_default(
            connection_info=None,
            schema_initialized=False,
        )

        self.postgresql = DatabaseRequires(
            self,
            relation_name="postgresql",
            database_name=constants.DEFAULT_DATABASE_NAME,
        )

        framework.observe(self.on[constants.CONTAINER_NAME].pebble_ready, self._on_container_ready)
        framework.observe(self.on.config_changed, self._on_config_changed)

        framework.observe(self.postgresql.on.database_created, self._on_database_event)
        framework.observe(self.postgresql.on.endpoints_changed, self._on_database_event)

        framework.observe(self.on.postgresql_relation_broken, self._on_postgresql_broken)

    # Event handlers -----------------------------------------------------------------

    def _on_container_ready(self, _: ops.PebbleReadyEvent) -> None:
        self._reconcile()

    def _on_config_changed(self, event: ops.ConfigChangedEvent) -> None:
        self.unit.open_port("tcp", constants.HIVE_PORT)
        self._reconcile()

    def _on_database_event(
        self,
        event: ops.EventBase,
    ) -> None:
        if not isinstance(event, (DatabaseCreatedEvent, DatabaseEndpointsChangedEvent)):
            logger.debug("Ignoring unexpected database event: %s", type(event).__name__)
            return

        connection = self._merge_connection_info(event)
        if connection is None:
            logger.debug("PostgreSQL relation data incomplete; deferring event")
            event.defer()
            return

        if connection != self._stored.connection_info:
            self._stored.connection_info = connection
            self._stored.schema_initialized = False

        self._reconcile()

    def _on_postgresql_broken(self, _: ops.RelationBrokenEvent) -> None:
        container = self._get_container()
        if container and container.can_connect():
            self._stop_service(container)

        self._stored.connection_info = None
        self._stored.schema_initialized = False
        self.unit.status = BlockedStatus("waiting for postgresql relation")

    # Reconciliation -----------------------------------------------------------------

    def _reconcile(self) -> None:
        container = self._get_container()
        if container is None or not container.can_connect():
            self.unit.status = WaitingStatus("waiting for container startup")
            return

        connection = self._stored.connection_info
        if not connection:
            self._stop_service(container)
            self.unit.status = BlockedStatus("waiting for postgresql relation")
            return

        self.unit.status = MaintenanceStatus("applying hive configuration")

        try:
            self._render_configuration(container, connection)
        except ConnectionError:
            self.unit.status = WaitingStatus("waiting for container filesystem")
            return

        try:
            self._apply_pebble_layer(container)
        except ConnectionError:
            self.unit.status = WaitingStatus("waiting for pebble service")
            return

        if not self._stored.schema_initialized:
            try:
                self.unit.status = MaintenanceStatus("initialising metastore schema")
                self._initialize_schema(container)
                self._stored.schema_initialized = True
            except SchemaInitializationError as exc:
                logger.error("Schema initialisation failed: %s", exc)
                self.unit.status = BlockedStatus(str(exc))
                return
            except ConnectionError:
                self.unit.status = WaitingStatus("waiting for container shell")
                return

        try:
            service = container.get_service(constants.SERVICE_NAME)
            if not service.is_running():
                container.start(constants.SERVICE_NAME)
        except ConnectionError:
            self.unit.status = WaitingStatus("waiting for container startup")
            return
        except ChangeError as exc:
            logger.error("Failed to start hive-metastore service: %s", exc)
            self.unit.status = BlockedStatus("failed to start hive-metastore service")
            return

        self.unit.status = ActiveStatus()

    # Helpers ------------------------------------------------------------------------

    def _get_container(self) -> Optional[ops.Container]:
        try:
            return self.unit.get_container(constants.CONTAINER_NAME)
        except ops.model.ModelError:
            return None

    def _merge_connection_info(self, event: DatabaseCreatedEvent) -> Optional[Dict[str, object]]:
        info: Dict[str, object] = dict(self._stored.connection_info or {})

        if event.endpoints:
            primary = event.endpoints.split(",")[0].strip()
            host, _, raw_port = primary.partition(":")
            if not host:
                logger.warning("Received invalid PostgreSQL endpoints: %s", event.endpoints)
                return None
            info["host"] = host
            info["port"] = raw_port or "5432"
            info["endpoints"] = event.endpoints

        if event.database:
            info["database"] = event.database
        if event.username:
            info["username"] = event.username
        if event.password:
            info["password"] = event.password

        tls_enabled = self._to_bool(getattr(event, "tls", None))
        if tls_enabled:
            info["tls"] = True
        elif "tls" not in info:
            info["tls"] = False

        tls_ca = getattr(event, "tls_ca", None)
        if tls_ca:
            info["tls_ca"] = tls_ca
        elif not info.get("tls"):
            info.pop("tls_ca", None)

        info.setdefault("database", constants.DEFAULT_DATABASE_NAME)

        required_fields = {"host", "port", "database", "username", "password"}
        if all(info.get(field) for field in required_fields):
            info["port"] = str(info["port"])
            info["tls"] = bool(info.get("tls"))
            return info

        return None

    def _render_configuration(self, container: ops.Container, info: Dict[str, object]) -> None:
        hive_site = self._render_hive_site(info)
        container.push(constants.HIVE_SITE_PATH, hive_site, make_dirs=True, permissions=0o640)

        if info.get("tls") and info.get("tls_ca"):
            ca_content = self._normalise_ca(str(info["tls_ca"]))
            container.push(constants.POSTGRES_CA_PATH, ca_content, make_dirs=True, permissions=0o600)
        else:
            try:
                if container.exists(constants.POSTGRES_CA_PATH):
                    container.remove_path(constants.POSTGRES_CA_PATH)
            except APIError:
                logger.debug("Failed to remove CA file; it may not exist yet")

    def _render_hive_site(self, info: Dict[str, object]) -> str:
        properties = {
            "javax.jdo.option.ConnectionURL": self._build_jdbc_url(info),
            "javax.jdo.option.ConnectionDriverName": "org.postgresql.Driver",
            "javax.jdo.option.ConnectionUserName": str(info["username"]),
            "javax.jdo.option.ConnectionPassword": str(info["password"]),
            "datanucleus.schema.autoCreateAll": "false",
            "datanucleus.autoCreateSchema": "false",
            "datanucleus.fixedDatastore": "true",
            "hive.metastore.schema.verification": "false",
            "hive.metastore.uris": f"thrift://0.0.0.0:{constants.HIVE_PORT}",
        }

        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            "<configuration>",
        ]

        for name, value in properties.items():
            lines.extend(
                [
                    "  <property>",
                    f"    <name>{escape(name)}</name>",
                    f"    <value>{escape(str(value))}</value>",
                    "  </property>",
                ]
            )

        lines.append("</configuration>")
        lines.append("")
        return "\n".join(lines)

    def _build_jdbc_url(self, info: Dict[str, object]) -> str:
        host = str(info["host"])
        port = str(info["port"])
        database = str(info["database"])

        base = f"jdbc:postgresql://{host}:{port}/{database}"

        if not info.get("tls"):
            return base

        params = ["sslmode=require"]
        if info.get("tls_ca"):
            params[0] = "sslmode=verify-ca"
            params.append(f"sslrootcert={constants.POSTGRES_CA_PATH}")

        return f"{base}?{'&'.join(params)}"

    def _apply_pebble_layer(self, container: ops.Container, log_level: str) -> None:
        desired_layer = self._pebble_layer(log_level)
        plan = container.get_plan()
        current_service = plan.services.get(constants.SERVICE_NAME)
        current_dict = current_service.to_dict() if current_service else None
        desired_dict = desired_layer["services"][constants.SERVICE_NAME]

        if current_dict != desired_dict:
            container.add_layer(constants.SERVICE_NAME, desired_layer, combine=True)
            container.replan()

    def _pebble_layer(self, log_level: str) -> ops.pebble.LayerDict:
        environment = self._service_environment(log_level)
        return {
            "summary": "Hive Metastore service",
            "description": "Pebble layer configuring the Hive Metastore process",
            "services": {
                constants.SERVICE_NAME: {
                    "override": "replace",
                    "summary": "Hive Metastore",
                    "command": "/opt/hive/bin/hive --service metastore",
                    "startup": "enabled",
                    "environment": environment,
                }
            },
        }

    def _service_environment(self, log_level: str) -> Dict[str, str]:
        return {
            "HIVE_HOME": "/opt/hive",
            "HADOOP_HOME": "/opt/hadoop",
            "JAVA_HOME": "/usr/lib/jvm/java-8-openjdk-amd64",
            "HIVE_CONF_DIR": constants.HIVE_CONF_DIR,
            "HADOOP_CONF_DIR": constants.HIVE_CONF_DIR,
            "PATH": "/opt/hadoop/bin:/opt/hive/bin:/usr/bin:/bin",
            "HIVE_METASTORE_LOGLEVEL": log_level,
        }

    def _initialize_schema(self, container: ops.Container, log_level: str) -> None:
        command = [
            "/opt/hive/bin/schematool",
            "-dbType",
            "postgres",
            "-initSchema",
            "-verbose",
        ]

        process = container.exec(
            command,
            timeout=constants.SCHEMA_TOOL_TIMEOUT,
            environment=self._service_environment(log_level),
        )

        try:
            stdout, stderr = process.wait_output()
            if stdout:
                logger.debug("schematool stdout: %s", stdout)
            if stderr:
                logger.debug("schematool stderr: %s", stderr)
        except ExecError as exc:
            failure_output = f"{exc.stdout}\n{exc.stderr}".lower()
            if "already" in failure_output and "exist" in failure_output:
                logger.info("Hive metastore schema already initialised")
                return
            raise SchemaInitializationError("failed to initialise metastore schema") from exc

    def _stop_service(self, container: ops.Container) -> None:
        try:
            service = container.get_service(constants.SERVICE_NAME)
        except ConnectionError:
            return

        if service.is_running():
            try:
                container.stop(constants.SERVICE_NAME)
            except ChangeError as exc:
                logger.debug("Failed to stop service cleanly: %s", exc)

    @staticmethod
    def _to_bool(value: Optional[object]) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _normalise_ca(raw_value: str) -> str:
        text = raw_value.strip()
        if "-----BEGIN" in text:
            return HiveMetastoreK8SOperatorCharm._ensure_trailing_newline(text)

        try:
            decoded = base64.b64decode(text, validate=True).decode()
            if "-----BEGIN" in decoded:
                return HiveMetastoreK8SOperatorCharm._ensure_trailing_newline(decoded)
        except (ValueError, UnicodeDecodeError):
            logger.debug("Failed to decode PostgreSQL CA as base64; using raw value")

        return HiveMetastoreK8SOperatorCharm._ensure_trailing_newline(text)

    @staticmethod
    def _ensure_trailing_newline(content: str) -> str:
        return content if content.endswith("\n") else f"{content}\n"


if __name__ == "__main__":  # pragma: nocover
    ops.main(HiveMetastoreK8SOperatorCharm)
