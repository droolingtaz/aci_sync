"""Sync modules for ACI to NetBox synchronization."""

from .base import BaseSyncModule, SyncResult, SyncStats, SyncOrchestrator
from .fabric_sync import FabricSyncModule, PodSyncModule, NodeSyncModule
from .tenant_sync import TenantSyncModule
from .vrf_sync import VRFSyncModule
from .bd_sync import BridgeDomainSyncModule, SubnetSyncModule
from .ap_sync import AppProfileSyncModule
from .epg_sync import EPGSyncModule
from .esg_sync import ESGSyncModule
from .contract_sync import ContractFilterSyncModule, ContractSyncModule, ContractRelationshipSyncModule

__all__ = [
    'BaseSyncModule',
    'SyncResult',
    'SyncStats', 
    'SyncOrchestrator',
    'FabricSyncModule',
    'PodSyncModule',
    'NodeSyncModule',
    'TenantSyncModule',
    'VRFSyncModule',
    'BridgeDomainSyncModule',
    'SubnetSyncModule',
    'AppProfileSyncModule',
    'EPGSyncModule',
    'ESGSyncModule',
    'ContractFilterSyncModule',
    'ContractSyncModule',
    'ContractRelationshipSyncModule',
]

# Ordered list of modules for proper dependency resolution
SYNC_MODULE_ORDER = [
    FabricSyncModule,      # Must be first - creates fabric reference
    PodSyncModule,         # Depends on fabric
    NodeSyncModule,        # Depends on fabric, optionally pods
    TenantSyncModule,      # Depends on fabric
    VRFSyncModule,         # Depends on tenants
    BridgeDomainSyncModule,# Depends on tenants, VRFs
    SubnetSyncModule,      # Depends on BDs
    AppProfileSyncModule,  # Depends on tenants
    EPGSyncModule,         # Depends on APs, BDs
    ESGSyncModule,         # Depends on APs, VRFs
    ContractFilterSyncModule,  # Depends on tenants
    ContractSyncModule,    # Depends on tenants, filters
    ContractRelationshipSyncModule,  # Depends on contracts, EPGs, VRFs
]
