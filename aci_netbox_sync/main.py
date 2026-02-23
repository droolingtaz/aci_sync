#!/usr/bin/env python3
"""
ACI to NetBox Synchronization - Main Entry Point

Synchronizes Cisco ACI objects to NetBox using the ACI plugin.
Supports all major ACI objects: Fabrics, Pods, Nodes, Tenants, VRFs,
Bridge Domains, Subnets, Application Profiles, EPGs, ESGs, Contracts,
and Software Versions (via netbox-software-tracker plugin).

Usage:
    python -m aci_netbox_sync [options]
    
Or set environment variables and run:
    ./main.py

Environment Variables:
    ACI_HOST        - APIC hostname/IP
    ACI_USERNAME    - APIC username
    ACI_PASSWORD    - APIC password
    NETBOX_URL      - NetBox URL
    NETBOX_TOKEN    - NetBox API token
"""

import argparse
import sys
import logging
from typing import Optional

from .config import Config, setup_logging
from .utils import ACIClient, NetBoxClient
from .sync_modules import (
    SyncOrchestrator,
    SYNC_MODULE_ORDER,
    FabricSyncModule,
    PodSyncModule,
    NodeSyncModule,
    TenantSyncModule,
    VRFSyncModule,
    BridgeDomainSyncModule,
    SubnetSyncModule,
    AppProfileSyncModule,
    EPGSyncModule,
    ESGSyncModule,
    ContractFilterSyncModule,
    ContractSyncModule,
    SoftwareVersionSyncModule,
)

logger = logging.getLogger(__name__)

# Valid object type choices for CLI
OBJECT_TYPE_CHOICES = [
    'fabric', 'pods', 'nodes', 'tenants', 'vrfs',
    'bds', 'subnets', 'aps', 'epgs', 'esgs', 'contracts',
    'software',
]


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description='Synchronize Cisco ACI objects to NetBox',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    # Connection options
    parser.add_argument('--aci-host', help='ACI APIC hostname/IP')
    parser.add_argument('--aci-username', help='ACI username')
    parser.add_argument('--aci-password', help='ACI password')
    parser.add_argument('--netbox-url', help='NetBox URL')
    parser.add_argument('--netbox-token', help='NetBox API token')
    
    # Config file option
    parser.add_argument('-c', '--config', help='Path to config file (YAML/JSON)')
    
    # Sync options
    parser.add_argument('--dry-run', action='store_true',
                        help='Show what would be synced without making changes')
    parser.add_argument('--no-verify', action='store_true',
                        help='Skip verification of updates')
    parser.add_argument('--continue-on-error', action='store_true', default=True,
                        help='Continue syncing after errors (default: True)')
    
    # Object selection
    parser.add_argument('--only', nargs='+', 
                        choices=OBJECT_TYPE_CHOICES,
                        help='Only sync specified object types')
    parser.add_argument('--skip', nargs='+',
                        choices=OBJECT_TYPE_CHOICES,
                        help='Skip specified object types')
    
    # Logging options
    parser.add_argument('-v', '--verbose', action='store_true',
                        help='Enable verbose (DEBUG) logging')
    parser.add_argument('--log-file', help='Write logs to file')
    
    return parser.parse_args()


def get_modules_to_sync(args: argparse.Namespace) -> list:
    """Determine which modules to run based on arguments."""
    module_map = {
        'fabric': FabricSyncModule,
        'pods': PodSyncModule,
        'nodes': NodeSyncModule,
        'tenants': TenantSyncModule,
        'vrfs': VRFSyncModule,
        'bds': BridgeDomainSyncModule,
        'subnets': SubnetSyncModule,
        'aps': AppProfileSyncModule,
        'epgs': EPGSyncModule,
        'esgs': ESGSyncModule,
        'contracts': ContractSyncModule,
        'software': SoftwareVersionSyncModule,
    }
    
    if args.only:
        # Only run specified modules
        modules = []
        for name in args.only:
            if name in module_map:
                modules.append(module_map[name])
            if name == 'contracts':
                # Add filter module before contracts
                modules.insert(-1, ContractFilterSyncModule)
        return modules
    
    # Start with full list
    modules = list(SYNC_MODULE_ORDER)
    
    if args.skip:
        # Remove skipped modules
        for name in args.skip:
            if name in module_map:
                module = module_map[name]
                if module in modules:
                    modules.remove(module)
            if name == 'contracts':
                # Also remove filter module
                if ContractFilterSyncModule in modules:
                    modules.remove(ContractFilterSyncModule)
    
    return modules


def main() -> int:
    """Main entry point."""
    args = parse_args()
    
    # Setup logging
    log_level = 'DEBUG' if args.verbose else 'INFO'
    setup_logging(level=log_level, log_file=args.log_file)
    
    logger.info("ACI to NetBox Sync starting...")
    
    # Load configuration
    if args.config:
        config = Config.from_file(args.config)
    else:
        config = Config.from_env()
    
    # Override with command line args
    if args.aci_host:
        config.aci.host = args.aci_host
    if args.aci_username:
        config.aci.username = args.aci_username
    if args.aci_password:
        config.aci.password = args.aci_password
    if args.netbox_url:
        config.netbox.url = args.netbox_url
    if args.netbox_token:
        config.netbox.token = args.netbox_token
    if args.dry_run:
        config.sync.dry_run = True
    if args.no_verify:
        config.sync.verify_updates = False
    
    # Validate configuration
    if not config.validate():
        logger.error("Invalid configuration. Please check settings.")
        return 1
    
    # Initialize clients
    aci_client = ACIClient(
        host=config.aci.host,
        username=config.aci.username,
        password=config.aci.password,
        verify_ssl=config.aci.verify_ssl,
        timeout=config.aci.timeout
    )
    
    netbox_client = NetBoxClient(
        url=config.netbox.url,
        token=config.netbox.token,
        verify_ssl=config.netbox.verify_ssl,
        timeout=config.netbox.timeout
    )
    
    # Connect to both systems
    if not aci_client.connect():
        logger.error("Failed to connect to ACI")
        return 1
    
    if not netbox_client.connect():
        logger.error("Failed to connect to NetBox")
        aci_client.disconnect()
        return 1
    
    try:
        # Determine modules to run
        modules = get_modules_to_sync(args)
        logger.info(f"Will sync {len(modules)} object types")
        
        # Create orchestrator and run sync
        orchestrator = SyncOrchestrator(aci_client, netbox_client, config.sync)
        stats = orchestrator.run_all(modules)
        
        # Print summary
        print("\n" + stats.summary())
        
        # Return success if no failures
        return 0 if stats.total_failed == 0 else 1
        
    except KeyboardInterrupt:
        logger.info("Sync interrupted by user")
        return 130
    except Exception as e:
        logger.exception(f"Sync failed with error: {e}")
        return 1
    finally:
        aci_client.disconnect()


if __name__ == '__main__':
    sys.exit(main())