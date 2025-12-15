# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Hive Metastore related logic."""

import json
from typing import Any, Callable, Optional
from xml.sax.saxutils import escape

import ops
import pydantic

import constants


class PostgresRelationModel(pydantic.BaseModel):
    """Wrapper for PostgreSQL relation to be passed to configuration renderers."""

    database: str
    endpoints: str
    secret_tls: dict[str, str] = pydantic.Field(alias="secret-tls")
    secret_user: dict[str, str] = pydantic.Field(alias="secret-user")

    @property
    def host(self) -> str:
        val = self.endpoints.split(",")[0]
        host, _ = val.split(":", 1)
        return host

    @property
    def port(self) -> str:
        val = self.endpoints.split(",")[0]
        _, port = val.split(":", 1)
        return port

    @property
    def username(self) -> str:
        return self.secret_user["username"]

    @property
    def password(self) -> str:
        return self.secret_user["password"]

    @property
    def tls(self) -> bool:
        return self.secret_tls["tls"].lower() == "true"

    @property
    def tls_ca(self) -> Optional[str]:
        return self.secret_tls.get("tls-ca") 

    @classmethod
    def decode(cls, charm: ops.CharmBase) -> Callable[[str], str | dict[str, str]]:
        def _decode(v: str) -> str | dict[str, str]:
            """Decoder method for 'ops.Relation.load' that accommodates
            Postgres databag is a mix of json dumps and plain strings.
        
            :param cls: Description
            :param v: Value to decode.
            :type v: str
            :return: Decoded string.
            :rtype: Any
            """
            try:
                ret = json.loads(v)
            except json.JSONDecodeError:
                ret = v

            if not v.startswith("secret://"):
                return ret
            
            secret = charm.model.get_secret(id=v)
            content = secret.get_content(refresh=True)
            return content
        
        return _decode


def manage_configuration_files(container: ops.Container, pg_relation: PostgresRelationModel) -> bool:
    has_changed = False

    try:
        curr_file = container.pull(constants.HIVE_SITE_PATH)
        curr_contents = "\n".join(curr_file.readlines())
    except ops.pebble.PathError:
        curr_contents = ""

    new_contents = _render_hive_site(pg_relation)
    if curr_contents != new_contents:
        container.push(constants.HIVE_SITE_PATH, new_contents, make_dirs=True, permissions=0o640)
        has_changed = True

    if pg_relation.tls and pg_relation.tls_ca:
        ca_content = _normalize_ca(pg_relation.tls_ca)
        container.push(constants.POSTGRES_CA_PATH, ca_content, make_dirs=True, permissions=0o600)
        has_changed = True
    else:
        try:
            container.remove_path(constants.POSTGRES_CA_PATH)
            # Even though removing a file is a change
            # this does not warrant a restart, so we do not set the flag.
        except ops.pebble.PathError:
            pass

    return has_changed


def _render_hive_site(pg_relation: PostgresRelationModel):
    """Render `hive-site.xml` configuration file.

    :param pg_relation: Wrapper for Postgres relation data.
    :type pg_relation: PostgresRelation
    """
    properties = {
        "javax.jdo.option.ConnectionURL": _build_jdbc_url(pg_relation),
        "javax.jdo.option.ConnectionDriverName": "org.postgresql.Driver",
        "javax.jdo.option.ConnectionUserName": str(pg_relation.username),
        "javax.jdo.option.ConnectionPassword": str(pg_relation.password),
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


def _build_jdbc_url(pg_relation: PostgresRelationModel) -> str:
    """Build a JDBC URL from Postgres credentials.

    :param pg_relation: Wrapper for Postgres relation data.
    :type pg_relation: PostgresRelation
    :return: A JDBC URL to connect to a Postgres database.
    :rtype: str
    """
    base = f"jdbc:postgresql://{pg_relation.host}:{pg_relation.port}/{pg_relation.database}"

    if not pg_relation.tls:
        return base

    params = ["sslmode=require"]
    if pg_relation.tls_ca:
        params[0] = "sslmode=verify-ca"
        params.append(f"sslrootcert={constants.POSTGRES_CA_PATH}")

    return f"{base}?{'&'.join(params)}"


# This is currently a no-op but we will probably need it.
def _normalize_ca(text: str) -> str:
    """Normalize a CA certificate in PEM format.

    Currently does nothing.

    :param text: A CA certificate in PEM format.
    :type text: str
    :return: A CA certificate in PEM format that is formatted for Hive Metastore.
    :rtype: str
    """
    return text
