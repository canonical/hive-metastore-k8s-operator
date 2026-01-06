import logging
import os
import pathlib
import subprocess
import sys
import time

import jubilant
import pytest

logger = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def juju(request: pytest.FixtureRequest):
    """Create a temporary Juju model for running tests."""
    with jubilant.temp_model() as juju:
        yield juju

        if request.session.testsfailed:
            logger.info("Collecting Juju logs...")
            time.sleep(0.5)  # Wait for Juju to process logs.
            log = juju.debug_log(limit=1000)
            print(log, end="", file=sys.stderr)


@pytest.fixture(scope="session")
def charm():
    """Return the path of the charm under test."""
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
