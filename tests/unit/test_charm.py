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


def test_database_created():
    """Test state in database-created after PostgreSQL relation."""
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
    state_in = testing.State(
        containers={container},
        relations={relation},
        secrets={
            user_secret,
            tls_secret,
        },
        leader=True,
    )

    # Act:
    state_out = ctx.run(ctx.on.relation_changed(relation), state_in)

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
        </configuration>
        """)
    assert config == expected_config
