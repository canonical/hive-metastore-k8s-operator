#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Hive Metastore Kubernetes charm."""

import logging
from typing import Optional

import ops
import yaml
from charms.data_platform_libs.v0.data_interfaces import (
    DatabaseCreatedEvent,
    DatabaseEndpointsChangedEvent,
    DatabaseRequires,
)
from charms.data_platform_libs.v0.data_models import TypedCharmBase
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from ops.pebble import APIError, ChangeError, ConnectionError, PathError

import constants
import hive_metastore
import schematool
from config import CharmConfig

logger = logging.getLogger(__name__)


class HiveMetastoreK8SOperatorCharm(TypedCharmBase[CharmConfig]):
    """Operator to configure and run the Hive Metastore workload."""

    config_type = CharmConfig

    def __init__(self, framework: ops.Framework):
        super().__init__(framework)

        self.postgresql = DatabaseRequires(
            self,
            relation_name="postgresql",
            database_name=constants.DEFAULT_DATABASE_NAME,
        )

        framework.observe(self.on[constants.CONTAINER_NAME].pebble_ready, self._on_pebble_ready)
        framework.observe(self.on.config_changed, self._on_config_changed)
        framework.observe(self.on.update_status, self._on_update_status)

        framework.observe(self.postgresql.on.database_created, self._on_database_event)
        framework.observe(self.postgresql.on.endpoints_changed, self._on_database_event)

        framework.observe(self.on.postgresql_relation_broken, self._on_postgresql_broken)

    # Event handlers -----------------------------------------------------------------

    def _on_pebble_ready(self, event: ops.PebbleReadyEvent) -> None:
        container = self._get_container()
        if not (container and container.can_connect()):
            event.defer()
            return
        try:
            meta_file = container.pull("/rockcraft.yaml")
            meta = yaml.safe_load(meta_file)
            if meta and "version" in meta:
                self.unit.set_workload_version(meta["version"])
            else:
                raise ValueError("Cannot find 'version' in 'rockcraft.yaml'.")
        except (PathError, ValueError, yaml.YAMLError) as e:
            logger.debug("Could not get workload version: %s", str(e))
        self._reconcile()

    def _on_config_changed(self, _: ops.ConfigChangedEvent) -> None:
        self.unit.open_port("tcp", constants.HIVE_PORT)
        self._reconcile()

    def _on_update_status(self, _: ops.UpdateStatusEvent) -> None:
        self._reconcile()

    def _on_database_event(
        self,
        event: ops.EventBase,
    ) -> None:
        if not isinstance(event, (DatabaseCreatedEvent, DatabaseEndpointsChangedEvent)):
            logger.debug("Ignoring unexpected database event: %s", type(event).__name__)
            return

        self._reconcile()

    def _on_postgresql_broken(self, _: ops.RelationBrokenEvent) -> None:
        self._reconcile()

    # Reconciliation -----------------------------------------------------------------

    def _reconcile(self) -> None:
        # TODO (mertalpt): Check if it would be better to update
        # the pebble plan rather than stopping the service.
        container = self._get_container()
        if container is None or not container.can_connect():
            self.unit.status = WaitingStatus("waiting for container startup")
            return

        # Check if Postgres relation is there.
        if (raw_pg_relation := self.model.get_relation(constants.POSTGRES_RELATION)) is None:
            self._stop_service(container)
            self.unit.status = BlockedStatus("waiting for postgresql relation")
            return

        pg_relation = raw_pg_relation.load(
            hive_metastore.PostgresRelationModel,
            raw_pg_relation.app,
            decoder=hive_metastore.PostgresRelationModel.decode(self),
        )

        # Check if a configuration update is needed.
        try:
            do_restart = hive_metastore.manage_configuration_files(container, pg_relation)
        except ConnectionError:
            self.unit.status = WaitingStatus("waiting for container filesystem")
            return

        env = self._service_environment()
        res = schematool.info(container, env)

        do_init = False
        if not res.success:
            err_text = res.stderr or ""
            # TODO (mertalpt): This needs to be handled better after we get
            # some experience with the charm.
            if "Failed to get schema version" not in err_text:
                logger.error("Failed to fetch schema version: %s", err_text)
                self._stop_service(container)
                self.unit.status = BlockedStatus(
                    "schematool (info) is broken; run 'juju debug-log' for details"
                )
                return
            # This means schema needs to be initialized.
            do_init, do_restart = True, True

        if do_restart:
            self.unit.status = MaintenanceStatus("restarting metastore service")
            self._stop_service(container)
        if do_init:
            self.unit.status = MaintenanceStatus("initializing metastore schema")
            try:
                res = schematool.initialize(container, env)
            except schematool.SchemaInitializationError as e:
                logger.error("Failed to initialize metastore schema: %s", str(e))
                self.unit.status = BlockedStatus(
                    "schematool (initSchema) is broken; run 'juju debug-log' for details"
                )
                return

        try:
            self._apply_pebble_layer(container)
        except ConnectionError:
            self.unit.status = WaitingStatus("waiting for pebble service")
            return

        container.replan()
        self.unit.status = ActiveStatus()

    # Helpers ------------------------------------------------------------------------

    def _get_container(self) -> Optional[ops.Container]:
        try:
            return self.unit.get_container(constants.CONTAINER_NAME)
        except ops.model.ModelError:
            return None

    def _apply_pebble_layer(self, container: ops.Container) -> None:
        desired_layer = self._pebble_layer()
        container.add_layer(constants.SERVICE_NAME, desired_layer, combine=True)

    def _pebble_layer(self) -> ops.pebble.LayerDict:
        environment = self._service_environment()
        return {
            "summary": "Hive Metastore service",
            "description": "Pebble layer configuring the Hive Metastore process",
            "services": {
                constants.SERVICE_NAME: {
                    "override": "replace",
                    "summary": "Hive Metastore",
                    "command": "/opt/hive/bin/start-metastore",
                    "startup": "enabled",
                    "environment": environment,
                }
            },
        }

    def _service_environment(self) -> dict[str, str]:
        return {
            "HIVE_HOME": "/opt/hive",
            "HADOOP_HOME": "/opt/hadoop",
            "JAVA_HOME": "/usr/lib/jvm/java-21-openjdk-amd64",
            "HIVE_CONF_DIR": constants.HIVE_CONF_DIR,
            "HADOOP_CONF_DIR": constants.HIVE_CONF_DIR,
            "PATH": "/opt/hadoop/bin:/opt/hive/bin:/usr/bin:/bin",
        }

    def _stop_service(self, container: ops.Container) -> None:
        """Stop the Hive Metastore service in the given container.

        Args:
            container: Container in which the service will be stopped.
        """
        # Documentation is unclear on failure scenarios
        # but I think it is OK to ask for forgiveness
        # rather than permission here.
        try:
            container.stop(constants.SERVICE_NAME)
        except APIError as e:
            if (
                e.message
                == f"cannot stop services: service {constants.SERVICE_NAME} does not exist"
            ):
                pass
            else:
                raise
        except (ConnectionError, ChangeError) as e:
            logger.debug("Failed to stop service: %s", e)


if __name__ == "__main__":  # pragma: nocover
    ops.main(HiveMetastoreK8SOperatorCharm)
