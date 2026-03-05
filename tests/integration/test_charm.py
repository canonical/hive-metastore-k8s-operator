#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import pathlib

import jubilant
import yaml

METADATA = yaml.safe_load(pathlib.Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]
POSTGRESQL_NAME = "postgresql-k8s"
S3_INTEGRATOR_NAME = "s3-integrator"
MINIO_NAME = "minio"


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
    """Take the charm under test and integrate with PostgreSQL and S3.

    Assert on the unit status after integrations take place.
    """
    # `juju` is module scoped so Hive Metastore is already deployed from `test_deploy`.

    # Deploy PostgreSQL
    juju.deploy("postgresql-k8s", app=POSTGRESQL_NAME, channel="14/stable", trust=True)
    juju.wait(
        lambda status: jubilant.all_active(status, POSTGRESQL_NAME),
        error=jubilant.any_error,
        timeout=1000,
    )

    # Integrate with PostgreSQL first — HMS should still be blocked on S3
    juju.integrate(APP_NAME, POSTGRESQL_NAME)
    juju.wait(
        lambda status: jubilant.all_blocked(status, APP_NAME),
        timeout=1000,
    )

    # Deploy MinIO and S3 integrator
    juju.deploy(
        "minio",
        app=MINIO_NAME,
        channel="ckf-1.9/stable",
        trust=True,
        config={
            "access-key": "minio-access-key",
            "secret-key": "minio-secret-key",
        },
    )
    juju.wait(
        lambda status: jubilant.all_active(status, MINIO_NAME),
        error=jubilant.any_error,
        timeout=1000,
    )

    juju.deploy("s3-integrator", app=S3_INTEGRATOR_NAME, channel="latest/stable", trust=True)
    juju.wait(
        lambda status: jubilant.all_blocked(status, S3_INTEGRATOR_NAME),
        timeout=1000,
    )

    # Configure s3-integrator to point at MinIO
    minio_status = juju.status()
    minio_ip = minio_status.apps[MINIO_NAME].units[f"{MINIO_NAME}/0"].address
    juju.run(
        f"{S3_INTEGRATOR_NAME}/leader",
        "sync-s3-credentials",
        **{"access-key": "minio-access-key", "secret-key": "minio-secret-key"},
    )
    juju.config(
        S3_INTEGRATOR_NAME,
        {
            "endpoint": f"http://{minio_ip}:9000",
            "bucket": "hive-metastore",
            "path": "",
        },
    )
    juju.wait(
        lambda status: jubilant.all_active(status, S3_INTEGRATOR_NAME),
        error=jubilant.any_error,
        timeout=1000,
    )

    # Integrate HMS with S3
    juju.integrate(APP_NAME, S3_INTEGRATOR_NAME)

    # Wait for all applications to be active
    juju.wait(jubilant.all_active, error=jubilant.any_error, timeout=1000)
