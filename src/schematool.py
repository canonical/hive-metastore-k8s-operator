# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Schematool related logic."""

from dataclasses import dataclass
from typing import Any, Optional

import ops

import constants


class SchemaInitializationError(RuntimeError):
    """Raised when the Hive Metastore schema cannot be initialized."""


@dataclass
class SchematoolOperation:
    """Wrapper for a `schematool` subprocess call."""

    stdout: Optional[str]
    stderr: Optional[str]
    success: bool


def initialize(container: ops.Container, environment: dict[str, Any]) -> SchematoolOperation:
    """Run the `initialize` option for the `schematool` command.

    :param container: Container in which the command will be run.
    :type container: ops.Container
    :param environment: Environment to run the command with.
    :type environment: dict[str, Any]
    :return: The result and the output of the command.
    :rtype: SchematoolOperation
    """
    command = [
        constants.SCHEMATOOL_PATH,
        "-dbType",
        "postgres",
        "-initSchema",
        "-verbose",
    ]

    process = container.exec(
        command,
        timeout=constants.SCHEMATOOL_TIMEOUT,
        environment=environment,
    )

    try:
        stdout, stderr = process.wait_output()
        return SchematoolOperation(stdout, stderr, True)
    except ops.pebble.ExecError as e:
        failure_output = f"{e.stdout}\n{e.stderr}".lower()
        if "already" in failure_output and "exist" in failure_output:
            return SchematoolOperation(e.stdout, e.stderr, False)
        raise SchemaInitializationError from e


def info(container: ops.Container, environment: dict[str, Any]) -> SchematoolOperation:
    """Run the `info` option for the `schematool` command.

    :param container: Container in which the command will be run.
    :type container: ops.Container
    :param environment: Environment to run the command with.
    :type environment: dict[str, Any]
    :return: The result and the output of the command.
    :rtype: SchematoolOperation
    """
    command = [
        constants.SCHEMATOOL_PATH,
        "-dbType",
        "postgres",
        "-info",
    ]

    process = container.exec(
        command,
        timeout=constants.SCHEMATOOL_TIMEOUT,
        environment=environment,
    )

    try:
        stdout, stderr = process.wait_output()
        return SchematoolOperation(stdout, stderr, True)
    except ops.pebble.ExecError as e:
        return SchematoolOperation(e.stdout, e.stderr, False)
