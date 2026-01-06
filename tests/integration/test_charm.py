#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import pathlib

import jubilant
import yaml

METADATA = yaml.safe_load(pathlib.Path("./charmcraft.yaml").read_text())
APP_NAME = METADATA["name"]


def test_deploy(charm: pathlib.Path, juju: jubilant.Juju):
    """Deploy the charm under test.

    Assert on the unit status before any relations/configurations take place.
    """
    resources = {name: res["upstream-source"] for name, res in METADATA["resources"].items()}

    juju.deploy(f"./{charm}", resources=resources, application_name=APP_NAME)

    # Wait for the application to be blocked (since it's missing relations)
    juju.wait(jubilant.all_blocked, timeout=1000)
