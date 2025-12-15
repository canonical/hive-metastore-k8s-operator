# Variables for paths and configuration
MKFILE_PATH := $(abspath $(lastword $(MAKEFILE_LIST)))
PROJECT_ROOT := $(dir $(MKFILE_PATH))

CHARMCRAFT_YAML := $(PROJECT_ROOT)charmcraft.yaml
ROCK_DIR := $(PROJECT_ROOT)hive_metastore_rock
ROCKCRAFT_YAML := $(ROCK_DIR)/rockcraft.yaml
IMPORT_SCRIPT := $(PROJECT_ROOT)scripts/import_rock.sh

REGISTRY := localhost:32000

# Ensure yq is installed: 'sudo snap install yq'
CHARM_NAME := $(shell yq '.name' $(CHARMCRAFT_YAML))
CHARM_ARCH := amd64

ROCK_NAME := $(shell yq '.name' $(ROCKCRAFT_YAML))
ROCK_VERSION := $(shell yq '.version' $(ROCKCRAFT_YAML))
ROCK_ARCH := amd64

# The expected output files from charmcraft/rockcraft pack
CHARM_FILE := $(PROJECT_ROOT)$(CHARM_NAME)_$(CHARM_ARCH).charm
ROCK_FILE := $(ROCK_DIR)/$(ROCK_NAME)_$(ROCK_VERSION)_$(ROCK_ARCH).rock

# Phony targets are not files
.PHONY: all build build_charm build_rock clean clean_charmcraft clean_rockcraft deploy_local help import_rock

# Default target
all: build

help:
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@echo "  build_rock   Build the OCI archive (rock) using rockcraft"
	@echo "  import_rock  Build and import the rock into MicroK8s"
	@echo "  clean        Remove built rock files"
	@echo "  help         Show this help message"

build: build_charm build_rock

clean:
	@echo "Cleaning up..."
	rm -f $(PROJECT_ROOT)/*.charm
	rm -f $(ROCK_DIR)/*.rock

clean_charmcraft:
	@echo "Cleaning charmcraft environment..."
	cd $(PROJECT_ROOT) && charmcraft clean

clean_rockcraft:
	@echo "Cleaning rockcraft environment..."
	cd $(ROCK_DIR) && rockcraft clean

deploy_local:
	@echo "Deploying charm with local resources..."
	juju deploy $(CHARM_FILE) --resource hive-metastore-image=$(REGISTRY)/$(ROCK_NAME):$(ROCK_VERSION)

build_charm:
	@echo "Building charm..."
	cd $(PROJECT_ROOT) && charmcraft pack --use-lxd --verbose

# Build the rock only if rockcraft.yaml changes or the file is missing
$(ROCK_FILE): $(ROCKCRAFT_YAML)
	@echo "Building rock..."
	cd $(ROCK_DIR) && rockcraft pack --use-lxd --verbose

build_rock: $(ROCK_FILE)

# import_rock depends on the rock file
import_rock: $(ROCK_FILE)
	@echo "Importing rock $(ROCK_FILE)..."
	$(IMPORT_SCRIPT) $(ROCK_FILE) $(NAME) $(VERSION)
