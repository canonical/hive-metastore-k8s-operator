# Contributing

To make contributions to this charm, you'll need a working [development setup](https://juju.is/docs/sdk/dev-setup).

A lot of the commands you would need are covered with the [Makefile](./Makefile), learn more by running `make help`.

**Note:** It is recommended to build in the host and deploy in a [Multipass](https://canonical.com/multipass/install) instance.
Use `multipass mount` to mount the project directory with the build artifacts into your Multipass instance.

## Environment for coding

You can install the dependencies for coding with:

```shell
# uv
sudo snap install astral-uv --channel latest/stable --classic
uv version
#> uv 0.9.5 (d5f39331a 2025-10-21)

# Tox
uv tool install tox --with tox-uv
tox --version
#> 4.32.0
```

You can create an environment for coding with:

```shell
make venv
source venv/bin/activate
```

## Environment for building

You can install the dependencies for building with:

```shell
# LXD
sudo snap install lxd --channel 5.21/stable
lxd version
#> 5.21.4 LTS

lxd init --auto

# Charmcraft
sudo snap install charmcraft --channel latest/stable --classic
charmcraft version
#> charmcraft 4.0.1

# Rockcraft
sudo snap install rockcraft --channel latest/stable --classic
rockcraft version
#> rockcraft 1.16.0

# yq
sudo snap install yq

# Required by `import_rock.sh`
sudo snap alias rockcraft.skopeo skopeo
```

## Verify build environment

You can verify that you have all the necessary dependencies installed with:

```shell
make check-build-deps
```

## Building artifacts

You can build the charm with:

```shell
make build-charm
```

You can build the rock with:

```shell
make build-rock
```

## Code quality

You can run linters, static analysis, and unit tests with:

```shell
make fmt     # Runs formatters and also does formatting checks
make lint    # Runs linters
make test    # Runs static analysis and unit tests
make checks  # Runs all of the above

make test-integration  # Runs integration tests*
```

*: It is recommended to let CI runners to run integration tests on GitHub Actions.

## Deploying locally

### Environment setup

You can install the dependencies with:

```shell
# Juju
sudo snap install juju --channel 3.6/stable
juju version
#> 3.6.12-genericlinux-amd64

# MicroK8s
sudo snap install microk8s --channel 1.34-strict/stable
microk8s version
#> MicroK8s v1.34.1 revision 8447

sudo microk8s enable hostpath-storage
sudo microk8s enable registry

sudo usermod -aG snap_microk8s $USER

# Docker
sudo snap install docker --channel latest/stable
docker version
#> ... 28.4.0 ...

sudo groupadd docker
sudo usermod -aG docker $USER
newgrp docker

sudo snap disable docker
sudo snap enable docker

# Both microk8s and docker requires new groups
# and `newgrp` does not cover for both at the same time.
# A system reboot is recommended at this point.

juju bootstrap microk8s
```

### Deployment

You can deploy `hive-metastore` using local artifacts with:

```shell
make deploy-local
```
