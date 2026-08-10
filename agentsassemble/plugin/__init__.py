"""First-party plugin host for room activity plugins.

Third-party install and marketplaces are intentionally out of scope. Only
repository-bundled plugins with an ``agentsassemble.plugin/v1`` manifest may run.
"""

from agentsassemble.plugin.manifest import (
    PLUGIN_API_VERSION,
    PluginManifest,
    load_first_party_manifests,
)
from agentsassemble.plugin.registry import PluginRegistry

__all__ = [
    "PLUGIN_API_VERSION",
    "PluginManifest",
    "PluginRegistry",
    "load_first_party_manifests",
]
