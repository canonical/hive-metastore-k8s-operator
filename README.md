# Hive Metastore K8s Operator

The Hive Metastore K8s Operator deploys the [Apache Hive Metastore](https://hive.apache.org/) on Kubernetes. It uses a pre-built Hive Metastore OCI image (Rock) and integrates with a charmed PostgreSQL database to store metadata and an S3-compatible object store (e.g., MinIO) for warehouse storage.

This charm is designed to provide a Hive-compatible metadata service, particularly useful for integrations with query engines like Trino that rely on Hive-compatible metadata services.

## Description

The Hive Metastore service stores the metadata for Hive tables and partitions in a relational database, and provides clients (including Hive, Impala, Trino, and Spark) access to this information via the Metastore Service API.

This operator manages the lifecycle of the Hive Metastore on Kubernetes, handling:
- Deployment of the Hive Metastore service.
- Configuration of the service and connection to the backend database.
- Integration with PostgreSQL using the `postgresql_client` interface.
- Integration with S3-compatible storage (e.g., MinIO) via the `s3` interface for the Hive warehouse directory.
- Day-2 operations like restarting the service or running schema validation tools.

## Usage

### Deployment

This charm requires a PostgreSQL database and an S3-compatible object store to function. You can deploy the Hive Metastore, PostgreSQL, MinIO, and the S3 integrator using Juju.

```shell
# Deploy PostgreSQL
juju deploy postgresql-k8s --channel 14/stable --trust

# Deploy MinIO (or use any S3-compatible storage)
juju deploy minio --channel ckf-1.9/stable --trust \
    --config access-key=<access-key> \
    --config secret-key=<secret-key>

# Deploy s3-integrator
juju deploy s3-integrator --channel latest/stable --trust

# Configure s3-integrator to point at MinIO
juju run s3-integrator/leader sync-s3-credentials \
    access-key=<access-key> secret-key=<secret-key>
juju config s3-integrator \
    endpoint=http://<minio-ip>:9000 \
    bucket=hive-metastore

# Deploy Hive Metastore
juju deploy hive-metastore-k8s

# Integrate them
juju integrate hive-metastore-k8s postgresql-k8s
juju integrate hive-metastore-k8s s3-integrator
```

### Configuration

The charm supports several configuration options to tune the Hive Metastore and the JVM.

| Option | Default | Description |
|---|---|---|
| `hms-min-threads` | 200 | Minimum number of threads for the Hive Metastore service. |
| `hms-max-threads` | 1000 | Maximum number of threads for the Hive Metastore service. |
| `sql-connection-pool-max-size` | 10 | Maximum size of the SQL connection pool to the backend database. |
| `additional-jvm-options` | "" | Additional options to tune JVM (e.g., "-Xmx2g"). |
| `kubernetes-requests` | "" | Custom resource requests (e.g., "cpu=100m,memory=256Mi"). |
| `kubernetes-limits` | "" | Custom resource limits (e.g., "cpu=500m,memory=1Gi"). |

Example configuration:

```shell
juju config hive-metastore-k8s hms-max-threads=500 additional-jvm-options="-Xmx4g"
```

### Actions

The charm provides actions for operational tasks:

- `restart`: Restart the Hive Metastore service.
- `schematool-info`: Display schema version information by running `schematool -info`.
- `schematool-validate`: Validate the metadata schema by running `schematool -validate`.
- `restore-schema-dirs`: Restore the Hive Metastore schema directories from the backend database.
- `clear-init-flag`: Clear the initialization flag to recover from a broken state.

Example usage:

```shell
juju run hive-metastore-k8s/0 schematool-info
```

## Contributing

Please see the [CONTRIBUTING.md](./CONTRIBUTING.md) file for information on how to contribute to this charm.
