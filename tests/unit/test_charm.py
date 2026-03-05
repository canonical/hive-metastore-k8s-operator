# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import textwrap

from ops import testing

import constants
from charm import HiveMetastoreK8SOperatorCharm


def test_pebble_ready():
    """Test state in pebble-ready after fresh install."""
    # Arrange:
    ctx = testing.Context(HiveMetastoreK8SOperatorCharm)
    container = testing.Container("hive-metastore", can_connect=True)
    state_in = testing.State(
        containers={container},
        leader=True,
    )

    # Act:
    state_out = ctx.run(ctx.on.pebble_ready(container), state_in)

    # Assert:
    updated_plan = state_out.get_container(container.name).plan
    expected_plan = {}
    assert expected_plan == updated_plan
    assert state_out.unit_status == testing.BlockedStatus("waiting for postgresql relation")


def _pg_relation_and_secrets():
    """Create a PostgreSQL relation with secrets for testing."""
    tls_secret = testing.Secret(
        {
            "tls": "False",
        }
    )
    user_secret = testing.Secret(
        {
            "username": "foo",
            "password": "bar",
        }
    )
    relation = testing.Relation(
        endpoint="postgresql",
        interface="postgresql_client",
        remote_app_name="postgresql-k8s",
        remote_app_data={
            "database": "hive_metastore_db",
            "endpoints": "example.com:5432",
            "secret-tls": tls_secret.id,
            "secret-user": user_secret.id,
        },
    )
    return relation, tls_secret, user_secret


def _s3_relation():
    """Create an S3 relation for testing."""
    return testing.Relation(
        endpoint=constants.S3_RELATION,
        interface="s3",
        remote_app_name="s3-integrator",
        remote_app_data={
            "access-key": "test-access-key",
            "secret-key": "test-secret-key",
            "bucket": "hive-metastore",
            "endpoint": "http://minio.example.com:9000",
        },
    )


def test_blocked_without_s3():
    """Test that the charm blocks when S3 relation is missing but PG is present."""
    # Arrange:
    ctx = testing.Context(HiveMetastoreK8SOperatorCharm)
    container = testing.Container("hive-metastore", can_connect=True)
    pg_relation, tls_secret, user_secret = _pg_relation_and_secrets()
    state_in = testing.State(
        containers={container},
        relations={pg_relation},
        secrets={user_secret, tls_secret},
        leader=True,
    )

    # Act:
    state_out = ctx.run(ctx.on.pebble_ready(container), state_in)

    # Assert:
    updated_plan = state_out.get_container(container.name).plan
    expected_plan = {}
    assert expected_plan == updated_plan
    assert state_out.unit_status == testing.BlockedStatus("waiting for s3-credentials relation")


def test_database_created():
    """Test state in database-created after PostgreSQL and S3 relations."""
    # Arrange:
    ctx = testing.Context(HiveMetastoreK8SOperatorCharm)
    container = testing.Container(
        "hive-metastore",
        can_connect=True,
        execs=[
            testing.Exec(
                [constants.SCHEMATOOL_PATH, "-dbType", "postgres", "-info"],
                return_code=1,
                stderr="Failed to get schema version",
            ),
            testing.Exec(
                [constants.SCHEMATOOL_PATH, "-dbType", "postgres", "-initSchema", "-verbose"],
                return_code=0,
            ),
        ],
    )
    pg_relation, tls_secret, user_secret = _pg_relation_and_secrets()
    s3_rel = _s3_relation()
    state_in = testing.State(
        containers={container},
        relations={pg_relation, s3_rel},
        secrets={
            user_secret,
            tls_secret,
        },
        leader=True,
    )

    # Act:
    state_out = ctx.run(ctx.on.relation_changed(pg_relation), state_in)

    # Assert:
    assert len(ctx.exec_history["hive-metastore"]) == 2
    assert state_out.unit_status == testing.ActiveStatus()
    container_fs = state_out.get_container("hive-metastore").get_filesystem(ctx)
    # Strip the leading slash
    cfg_file = container_fs / constants.HIVE_SITE_PATH[1:]
    config = cfg_file.read_text()
    expected_config = textwrap.dedent("""\
        <?xml version="1.0" encoding="UTF-8"?>
        <configuration>
          <property>
            <name>javax.jdo.option.ConnectionURL</name>
            <value>jdbc:postgresql://example.com:5432/hive_metastore_db</value>
          </property>
          <property>
            <name>javax.jdo.option.ConnectionDriverName</name>
            <value>org.postgresql.Driver</value>
          </property>
          <property>
            <name>javax.jdo.option.ConnectionUserName</name>
            <value>foo</value>
          </property>
          <property>
            <name>javax.jdo.option.ConnectionPassword</name>
            <value>bar</value>
          </property>
          <property>
            <name>datanucleus.schema.autoCreateAll</name>
            <value>false</value>
          </property>
          <property>
            <name>datanucleus.autoCreateSchema</name>
            <value>false</value>
          </property>
          <property>
            <name>datanucleus.fixedDatastore</name>
            <value>true</value>
          </property>
          <property>
            <name>hive.metastore.schema.verification</name>
            <value>false</value>
          </property>
          <property>
            <name>hive.metastore.uris</name>
            <value>thrift://0.0.0.0:9083</value>
          </property>
          <property>
            <name>hive.metastore.warehouse.dir</name>
            <value>s3a://hive-metastore/warehouse</value>
          </property>
          <property>
            <name>fs.s3a.endpoint</name>
            <value>http://minio.example.com:9000</value>
          </property>
          <property>
            <name>fs.s3a.access.key</name>
            <value>test-access-key</value>
          </property>
          <property>
            <name>fs.s3a.secret.key</name>
            <value>test-secret-key</value>
          </property>
          <property>
            <name>fs.s3a.path.style.access</name>
            <value>true</value>
          </property>
        </configuration>
        """)
    assert config == expected_config
