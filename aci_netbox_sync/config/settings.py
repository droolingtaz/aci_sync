"""
ACI to NetBox Sync - Configuration Settings
Manages configuration, environment variables, and connection settings.
"""

import os
from dataclasses import dataclass, field
from typing import Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class ACISettings:
    """Cisco ACI APIC connection settings."""
    host: str = field(default_factory=lambda: os.getenv("ACI_HOST", ""))
    username: str = field(default_factory=lambda: os.getenv("ACI_USERNAME", ""))
    password: str = field(default_factory=lambda: os.getenv("ACI_PASSWORD", ""))
    verify_ssl: bool = field(default_factory=lambda: os.getenv("ACI_VERIFY_SSL", "false").lower() == "true")
    timeout: int = field(default_factory=lambda: int(os.getenv("ACI_TIMEOUT", "30")))

    def validate(self) -> bool:
        """Validate required ACI settings."""
        if not all([self.host, self.username, self.password]):
            logger.error("Missing required ACI settings: host, username, or password")
            return False
        return True


@dataclass
class NetBoxSettings:
    """NetBox connection settings."""
    url: str = field(default_factory=lambda: os.getenv("NETBOX_URL", ""))
    token: str = field(default_factory=lambda: os.getenv("NETBOX_TOKEN", ""))
    verify_ssl: bool = field(default_factory=lambda: os.getenv("NETBOX_VERIFY_SSL", "true").lower() == "true")
    timeout: int = field(default_factory=lambda: int(os.getenv("NETBOX_TIMEOUT", "30")))

    def validate(self) -> bool:
        """Validate required NetBox settings."""
        if not all([self.url, self.token]):
            logger.error("Missing required NetBox settings: url or token")
            return False
        return True


@dataclass
class SyncSettings:
    """Synchronization behavior settings."""
    batch_size: int = field(default_factory=lambda: int(os.getenv("SYNC_BATCH_SIZE", "50")))
    max_workers: int = field(default_factory=lambda: int(os.getenv("SYNC_MAX_WORKERS", "4")))
    dry_run: bool = field(default_factory=lambda: os.getenv("SYNC_DRY_RUN", "false").lower() == "true")
    verify_updates: bool = field(default_factory=lambda: os.getenv("SYNC_VERIFY_UPDATES", "true").lower() == "true")
    continue_on_error: bool = field(default_factory=lambda: os.getenv("SYNC_CONTINUE_ON_ERROR", "true").lower() == "true")
    
    # Object types to sync
    sync_fabrics: bool = True
    sync_fabric_nodes: bool = True
    sync_tenants: bool = True
    sync_vrfs: bool = True
    sync_bridge_domains: bool = True
    sync_subnets: bool = True
    sync_app_profiles: bool = True
    sync_epgs: bool = True
    sync_esgs: bool = True
    sync_contracts: bool = True


@dataclass
class Config:
    """Main configuration container."""
    aci: ACISettings = field(default_factory=ACISettings)
    netbox: NetBoxSettings = field(default_factory=NetBoxSettings)
    sync: SyncSettings = field(default_factory=SyncSettings)

    def validate(self) -> bool:
        """Validate all configuration settings."""
        return self.aci.validate() and self.netbox.validate()

    @classmethod
    def from_env(cls) -> "Config":
        """Create configuration from environment variables."""
        return cls()

    @classmethod
    def from_file(cls, filepath: str) -> "Config":
        """Create configuration from a YAML/JSON file."""
        import json
        import yaml

        config = cls()
        
        with open(filepath, 'r') as f:
            if filepath.endswith('.yaml') or filepath.endswith('.yml'):
                data = yaml.safe_load(f)
            else:
                data = json.load(f)

        if 'aci' in data:
            for key, value in data['aci'].items():
                if hasattr(config.aci, key):
                    setattr(config.aci, key, value)

        if 'netbox' in data:
            for key, value in data['netbox'].items():
                if hasattr(config.netbox, key):
                    setattr(config.netbox, key, value)

        if 'sync' in data:
            for key, value in data['sync'].items():
                if hasattr(config.sync, key):
                    setattr(config.sync, key, value)

        return config


def setup_logging(level: str = "INFO", log_file: Optional[str] = None) -> None:
    """Configure logging for the sync process."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    
    handlers = [logging.StreamHandler()]
    if log_file:
        handlers.append(logging.FileHandler(log_file))

    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=handlers
    )
