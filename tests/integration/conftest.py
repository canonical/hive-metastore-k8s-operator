# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
import os
import pathlib
import subprocess
import sys
import time
from collections.abc import Generator

import jubilant
import pytest

logger = logging.getLogger(__name__)


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add options to the pytest configuration."""
    parser.addoption("--charm-file", action="store")
    parser.addoption("--hive-metastore-image", action="store")


@pytest.fixture(scope="module")
def juju(request: pytest.FixtureRequest) -> Generator[jubilant.Juju, None, None]:
    """Create a temporary Juju model for running tests."""
    with jubilant.temp_model() as juju:
        yield juju

        if request.session.testsfailed:
            logger.info("Collecting Juju logs...")
            time.sleep(0.5)  # Wait for Juju to process logs.
            log = juju.debug_log(limit=1000)
            print(log, end="", file=sys.stderr)


@pytest.fixture(scope="session")
def charm(request: pytest.FixtureRequest) -> pathlib.Path:
    """Return the path of the charm under test."""
    charm_file = request.config.getoption("--charm-file")
    if charm_file:
        path = pathlib.Path(charm_file)
        if not path.exists():
            raise FileNotFoundError(f"Charm does not exist: {path}")
        return path

    if "CHARM_PATH" in os.environ:
        charm_path = pathlib.Path(os.environ["CHARM_PATH"])
        if not charm_path.exists():
            raise FileNotFoundError(f"Charm does not exist: {charm_path}")
        return charm_path

    # Find existing charm
    charms = list(pathlib.Path(".").glob("*.charm"))
    if charms:
        return charms[0]

    # Build charm if not found
    logger.info("Building charm...")
    try:
        subprocess.run(["charmcraft", "pack", "--verbose"], check=True)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to build charm: {e}")

    charms = list(pathlib.Path(".").glob("*.charm"))
    if not charms:
        raise FileNotFoundError("Charm not found after build.")

    return charms[0]


@pytest.fixture(scope="session")
def hive_metastore_image(request: pytest.FixtureRequest) -> str | None:
    """Return the image path for hive-metastore."""
    return request.config.getoption("--hive-metastore-image")
