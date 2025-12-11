# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import re
import shlex
from typing import Any
from charms.data_platform_libs.v0.data_models import BaseConfigModel
from pydantic import validator

# Regexes for parsing Kubernetes style resource values
_K8S_CPU_REGEX = re.compile(r'^\d+(\.\d+)?m?$')
_K8S_MEMORY_REGEX = re.compile(r'^\d+(\.\d+)?(Ki|Mi|Gi|Ti|Pi|Ei|k|M|G|T|P|E)?$')

# Regex for parsing JVM options
_JVM_MEMORY_REGEX = re.compile(r'^-Xm[sx]\d+[kKmMgG]?$')
 
_JVM_ARGS_TAKING_FLAGS = {"-cp", "-classpath", "-jar", "-agentpath", "-javaagent"}


class CharmConfig(BaseConfigModel):
    """Typed configuration for the charm."""

    additional_jvm_options: list[str]
    hms_min_threads: int
    hms_max_threads: int
    kubernetes_requests: dict[str, str]
    kubernetes_limits: dict[str, str]
    sql_connection_pool_max_size: int

    @validator("additional_jvm_options", pre=True)
    def validate_jvm_options(cls, v: Any) -> list[str]:
        """Parses and validates the input into a list of JVM options.
        
        Expects a string of JVM options as if they are passed to `java`
        in the shell.

        :param cls: Class.
        :param v: Value to validate.
        :type v: Any
        :return: List of strings where each item is a valid JVM option.
        :rtype: list[str]
        """
        if v is None:
            return []

        # In case validators are chained
        if isinstance(v, list):
            v = " ".join((str(item) for item in v))

        if not isinstance(v, str):
            raise ValueError(f"Unexpected type (must be 'str'): {type(v)}")

        if not v.strip():
            return []
        try:
            tokens = shlex.split(v)
        except ValueError:
            raise ValueError(f"Unbalanced quotes: {v}") from None
        
        skip_next = False
        for i, opt in enumerate(tokens):
            if skip_next:
                skip_next = False
                continue

            if opt in _JVM_ARGS_TAKING_FLAGS:
                if i+1 >= len(tokens):
                    raise ValueError(f"Option is missing an argument: {opt}")
                # Assume next token will be valid as it is an argument to this option
                skip_next = True
                continue

            if not opt.startswith("-"):
                raise ValueError(f"Invalid option (must start with '-'): {opt}")
            
            if opt.startswith(("-Xmx", "-Xms")):
                if not _JVM_MEMORY_REGEX.match(opt):
                    raise ValueError(f"Invalid option (malformed value): {opt}")
                
            if opt.startswith("-D"):
                if "=" not in opt:
                    pass
                
                key = opt[2:].split("=")[0]
                if not key:
                    raise ValueError(f"Invalid property (empty key): {opt}")
                
        return tokens
    
    @validator("hms_min_threads", "hms_max_threads", "sql_connection_pool_max_size", pre=True)
    def validate_positive_ints(cls, v: Any) -> int:
        """Parses and validates the input into a positive integer.
        
        :param cls: Class.
        :param v: Value to validate.
        :type v: Any
        :return: A positive integer.
        :rtype: int
        """
        try:
            if v is None or isinstance(v, float):
                raise ValueError
            v = int(v)
        except ValueError:
            raise ValueError(f"Value must be a valid integer: {v}") from None
        
        if v <= 0:
            raise ValueError(f"Value must be a positive integer: {v}") from None
        
        return v
    
    @validator("kubernetes_requests", "kubernetes_limits", pre=True)
    def validate_kubernetes_resources(cls, v: Any) -> dict[str, str]:
        """Parses and validates the input into a dictionary that maps
        Kubernetes resources types to allocation.

        Expects comma separated `key=value` pairs where key is 'cpu' or 'memory'.
        
        :param cls: Class.
        :param v: Value to validate.
        :type v: Any
        :return: Dictionary that maps Kubernetes resources to values.
        :rtype: dict[str, str]
        """
        if v is None:
            return {}
        
        # In case validators are chained
        if isinstance(v, dict):
            v = ",".join([f"{str(key)}={str(val)}" for key, val in v.items()])
        
        if not isinstance(v, str):
            raise ValueError(f"Unexpected type (must be 'str'): {type(v)}")
        
        if v.strip() == "":
            return {}
        
        # Parsing
        parsed: dict[str, str] = {}
        items = [item.strip() for item in v.split(",") if item.strip()]

        for item in items:
            if "=" not in item:
                raise ValueError(f"Invalid format (missing '='): {item}")
            
            key_raw, val_raw = item.split("=", 1)
            key, val = key_raw.strip(), val_raw.strip()

            if key not in ["cpu", "memory"]:
                raise ValueError(f"Unknown resource type (must be 'cpu' or 'memory'): {key}")
            
            if key in parsed:
                raise ValueError(f"Duplicate resource type: {key}")
            
            if key == "cpu" and not _K8S_CPU_REGEX.match(val):
                raise ValueError(f"Invalid 'cpu' value: {val}")
            if key == "memory" and not _K8S_MEMORY_REGEX.match(val):
                raise ValueError(f"Invalid 'memory' value: {val}")
            
            parsed[key] = val

        return parsed
