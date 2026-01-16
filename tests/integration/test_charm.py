#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import pathlib

import jubilant
import yaml

METADATA = yaml.safe_load(pathlib.Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]
POSTGRESQL_NAME = "postgresql-k8s"


def test_deploy(
    charm: pathlib.Path, juju: jubilant.Juju, hive_metastore_image: str | None
) -> None:
    """Deploy the charm under test.

    Assert on the unit status before any relations/configurations take place.
    """
    resources = {}
    for name, res in METADATA["resources"].items():
        if _res := res.get("upstream-source"):
            resources[name] = _res
    if hive_metastore_image:
        resources["hive-metastore-image"] = hive_metastore_image

    juju.deploy(f"./{charm}", app=APP_NAME, resources=resources)

    juju.wait(jubilant.all_blocked, timeout=1000)


def test_integrate(
    charm: pathlib.Path, juju: jubilant.Juju, hive_metastore_image: str | None
) -> None:
    """Take the charm under test and integrate with PostgreSQL.

    Assert on the unit status after integrations take place.
    """
    # `juju` is module scoped so Hive Metastore is already deployed from `test_deploy`.
    juju.deploy("postgresql-k8s", app=POSTGRESQL_NAME, channel="14/stable", trust=True)
    juju.wait(
        lambda status: jubilant.all_active(status, POSTGRESQL_NAME),
        error=jubilant.any_error,
        timeout=1000,
    )

    # Integrate applications
    juju.integrate(APP_NAME, POSTGRESQL_NAME)

    # Wait for the applications to be active
    juju.wait(jubilant.all_active, error=jubilant.any_error, timeout=1000)
