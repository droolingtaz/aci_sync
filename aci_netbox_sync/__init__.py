"""
ACI to NetBox Synchronization Package

Synchronizes Cisco ACI objects to NetBox using the ACI plugin (version 0.2.0+).
Supports comprehensive data synchronization including:
- Fabric details (fabric_id, infra_vlan_id, gipo_pool)
- Fabric nodes and pods
- Tenants, VRFs, Bridge Domains, Subnets
- Application Profiles, EPGs, ESGs
- Contracts, Subjects, and Filters

Uses Cobra SDK for ACI access and pynetbox for NetBox operations.
"""

__version__ = '1.0.0'
__author__ = 'ACI-NetBox Sync'

from .config import Config, setup_logging
from .utils import ACIClient, NetBoxClient
from .sync_modules import (
    SyncOrchestrator,
    SyncResult,
    SyncStats,
    SYNC_MODULE_ORDER,
)

__all__ = [
    'Config',
    'setup_logging',
    'ACIClient',
    'NetBoxClient',
    'SyncOrchestrator',
    'SyncResult',
    'SyncStats',
    'SYNC_MODULE_ORDER',
]
