# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Hive Metastore related logic."""

import json
from pathlib import Path
from typing import Callable, Optional

import ops
import pydantic
from jinja2 import Environment, FileSystemLoader

import constants


class PostgresRelationModel(pydantic.BaseModel):
    """Wrapper for PostgreSQL relation to be passed to configuration renderers."""

    database: str
    endpoints: str
    secret_tls: dict[str, str] = pydantic.Field(alias="secret-tls")
    secret_user: dict[str, str] = pydantic.Field(alias="secret-user")

    @property
    def host(self) -> str:
        """Host for the database primary.

        Returns:
            Host value for the database primary.
        """
        val = self.endpoints.split(",")[0]
        host, _ = val.split(":", 1)
        return host

    @property
    def port(self) -> str:
        """Port for the database primary.

        Returns:
            Port value for the database primary.
        """
        val = self.endpoints.split(",")[0]
        _, port = val.split(":", 1)
        return port

    @property
    def username(self) -> str:
        """Username for the database server.

        Returns:
            Username of the relation user.
        """
        return self.secret_user["username"]

    @property
    def password(self) -> str:
        """Password for the database server.

        Returns:
            Password of the relation user.
        """
        return self.secret_user["password"]

    @property
    def tls(self) -> bool:
        """Whether the database server implements TLS.

        Returns:
            True if the database server implements TLS, false otherwise.
        """
        return self.secret_tls["tls"].lower() == "true"

    @property
    def tls_ca(self) -> Optional[str]:
        """Certificate of the certificate authority used for TLS.

        Returns:
            If exists, the CA certificate used for the TLS certificate.
        """
        return self.secret_tls.get("tls-ca")

    @classmethod
    def decode(cls, charm: ops.CharmBase) -> Callable[[str], str | dict[str, str]]:
        """Generate a decoder for Postgres databag that normalizes JSON and fetches secrets.

        Args:
            charm: Charm object that consumes Postgres.

        Returns:
            A function that decodes the Postgres databag key-value pairs.
        """

        def wrapped(v: str) -> str | dict[str, str]:
            """Decode contents of the Postgres databag.

            Args:
                v: Raw value from Postgres databag.

            Returns:
                Decoded string.
            """
            try:
                ret = json.loads(v)
            except json.JSONDecodeError:
                ret = v

            if not v.startswith("secret:"):
                return ret

            secret = charm.model.get_secret(id=v)
            content = secret.get_content(refresh=True)
            return content

        return wrapped


class S3RelationModel(pydantic.BaseModel):
    """Wrapper for S3 connection information from the s3-credentials relation."""

    access_key: str = pydantic.Field(alias="access-key")
    secret_key: str = pydantic.Field(alias="secret-key")
    bucket: str
    endpoint: str
    region: str = ""
    path: str = ""
    tls_ca_chain: Optional[list[str]] = pydantic.Field(default=None, alias="tls-ca-chain")

    class Config:
        """Pydantic model configuration."""

        populate_by_name = True

    @pydantic.validator("tls_ca_chain", pre=True)
    def parse_tls_ca_chain(cls, v):  # noqa: N805
        """Parse tls-ca-chain from JSON string if necessary.

        Args:
            v: Raw value from the relation databag.

        Returns:
            A list of CA chain strings or None.
        """
        if isinstance(v, str):
            try:
                return json.loads(v)
            except json.JSONDecodeError:
                return None
        return v

    @property
    def warehouse_dir(self) -> str:
        """S3A warehouse directory URI.

        Returns:
            An s3a:// URI pointing to the warehouse directory.
        """
        base = f"s3a://{self.bucket}"
        if self.path:
            base = f"{base}/{self.path.strip('/')}"
        return f"{base}/{constants.S3_WAREHOUSE_PREFIX}"

    @property
    def path_style_access(self) -> bool:
        """Whether to use path-style access for S3.

        MinIO and most self-hosted S3-compatible endpoints require path-style.

        Returns:
            True (path-style is the safe default for MinIO).
        """
        return True


def manage_configuration_files(
    container: ops.Container,
    pg_relation: PostgresRelationModel,
    s3_info: S3RelationModel,
) -> bool:
    """Render and organize configuration files in the container filesystem.

    Args:
        container: Container in which files will be managed.
        pg_relation: Object to use for Postgres credentials.
        s3_info: S3 connection information for warehouse storage.

    Returns:
        True if there has been a change in files, false otherwise.
    """
    has_changed = False

    try:
        curr_file = container.pull(constants.HIVE_SITE_PATH)
        curr_contents = "\n".join(curr_file.readlines())
    except ops.pebble.PathError:
        curr_contents = ""

    new_contents = _render_hive_site(pg_relation, s3_info)
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


def _render_hive_site(pg_relation: PostgresRelationModel, s3_info: S3RelationModel) -> str:
    """Render ``hive-site.xml`` configuration file using a Jinja template.

    Args:
        pg_relation: Wrapper for Postgres relation data.
        s3_info: S3 connection information for warehouse storage.
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
        # S3A warehouse configuration
        "hive.metastore.warehouse.dir": s3_info.warehouse_dir,
        "fs.s3a.endpoint": s3_info.endpoint,
        "fs.s3a.access.key": s3_info.access_key,
        "fs.s3a.secret.key": s3_info.secret_key,
        "fs.s3a.path.style.access": str(s3_info.path_style_access).lower(),
    }

    if s3_info.region:
        properties["fs.s3a.endpoint.region"] = s3_info.region

    template_path = Path(__file__).parent.parent / "templates"
    template_env = Environment(loader=FileSystemLoader(template_path))
    template = template_env.get_template(constants.HIVE_SITE_TEMPLATE)
    rendered = template.render(properties=properties)
    # Ensure trailing newline for compatibility
    return rendered.rstrip() + "\n"


def _build_jdbc_url(pg_relation: PostgresRelationModel) -> str:
    """Build a JDBC URL from Postgres credentials.

    Args:
        pg_relation: Wrapper for Postgres relation data.

    Returns:
        A JDBC URL to connect to a Postgres database.
    """
    base = f"jdbc:postgresql://{pg_relation.host}:{pg_relation.port}/{pg_relation.database}"

    if not pg_relation.tls:
        return base

    params = ["sslmode=require"]
    if pg_relation.tls_ca:
        params[0] = "sslmode=verify-ca"
        params.append(f"sslrootcert={constants.POSTGRES_CA_PATH}")

    return f"{base}?{'&'.join(params)}"


# TODO (mertalpt): This is currently a no-op but we will probably need it.
def _normalize_ca(text: str) -> str:
    """Normalize a CA certificate in PEM format.

    Currently does nothing.

    Args:
        text: A CA certificate in PEM format.

    Returns:
        A CA certificate in PEM format that is formatted for Hive Metastore.
    """
    return text
