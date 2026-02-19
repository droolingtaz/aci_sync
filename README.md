# ACI to NetBox Synchronization

A modular Python solution for synchronizing Cisco ACI (Application Centric Infrastructure) objects to NetBox using the [netbox-aci-plugin](https://github.com/pheus/netbox-aci-plugin) version 0.2.0+.

## Features

- **Comprehensive ACI Object Support**:
  - Fabric details (fabric_id, infra_vlan_id, gipo_pool) — *New in plugin 0.2.0*
  - Fabric Pods and Nodes — *New in plugin 0.2.0*
  - Tenants with all attributes
  - VRFs (Contexts) with policy settings
  - Bridge Domains with full configuration
  - BD Subnets with scope and control settings
  - Automatic parent prefix creation in IPAM for BD subnets
  - Application Profiles
  - Endpoint Groups (EPGs) including uSeg EPGs
  - Endpoint Security Groups (ESGs)
  - Contracts, Subjects, and Filters
  - Contract Relationships (EPG provider/consumer and vzAny)

- **Optimized for Performance**:
  - Pre-fetched caching reduces API calls from ~200+ to ~12 per sync run
  - Declarative field mappings eliminate duplicated comparison logic
  - Cached contract relations avoid O(n²) lookups
  - Reusable HTTP session for direct API calls
  - Connection pooling with retry logic

- **Robust Update Handling**:
  - Detects changes before updating using consistent `values_equal` comparison
  - Verifies updates were successful
  - Continues on errors (configurable)
  - Detailed logging and statistics

## Requirements

- Python 3.9+
- NetBox 4.5.x with ACI Plugin 0.2.0
- Cisco ACI APIC access
- Cobra SDK (acicobra, acimodel)

## Installation

1. **Clone or download the project**:
   ```bash
   git clone <repository-url>
   cd aci_netbox_sync
   ```

2. **Install Python dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Install Cisco Cobra SDK**:

   Download from your APIC (https://apic/cobra/_downloads) or Cisco DevNet:
   ```bash
   pip install acicobra-<version>.whl
   pip install acimodel-<version>.whl
   ```

4. **Configure the sync**:
   ```bash
   cp config.yaml.example config.yaml
   # Edit config.yaml with your settings
   ```

## Configuration

### Environment Variables

```bash
export ACI_HOST="apic.example.com"
export ACI_USERNAME="admin"
export ACI_PASSWORD="your-password"
export NETBOX_URL="https://netbox.example.com"
export NETBOX_TOKEN="your-api-token"
```

### Configuration File

Create `config.yaml`:

```yaml
aci:
  host: "apic.example.com"
  username: "admin"
  password: "your-password"
  verify_ssl: false

netbox:
  url: "https://netbox.example.com"
  token: "your-api-token"
  verify_ssl: true

sync:
  dry_run: false
  verify_updates: true
  continue_on_error: true
```

## Usage

### Basic Sync

```bash
# Using environment variables
python -m aci_netbox_sync

# Using config file
python -m aci_netbox_sync -c config.yaml

# Dry run to see what would change
python -m aci_netbox_sync --dry-run
```

### Selective Sync

```bash
# Sync only specific object types
python -m aci_netbox_sync --only fabric tenants vrfs

# Skip certain object types
python -m aci_netbox_sync --skip contracts esgs

# Sync fabric infrastructure only (new 0.2.0 features)
python -m aci_netbox_sync --only fabric pods nodes
```

### Command Line Options

```
usage: python -m aci_netbox_sync [options]

Connection options:
  --aci-host HOST       ACI APIC hostname/IP
  --aci-username USER   ACI username
  --aci-password PASS   ACI password
  --netbox-url URL      NetBox URL
  --netbox-token TOKEN  NetBox API token

Sync options:
  --dry-run             Show changes without applying
  --no-verify           Skip update verification
  --continue-on-error   Continue after errors (default)

Object selection:
  --only TYPES          Only sync specified types
  --skip TYPES          Skip specified types

  Available types: fabric, pods, nodes, tenants, vrfs,
                   bds, subnets, aps, epgs, esgs, contracts

Logging:
  -v, --verbose         Enable debug logging
  --log-file FILE       Write logs to file
  -c, --config FILE     Path to config file
```

## Project Structure

```
aci_netbox_sync/
├── __init__.py           # Package initialization
├── __main__.py           # Module entry point
├── main.py               # Main script with CLI
├── requirements.txt      # Python dependencies
├── config.yaml.example   # Sample configuration
│
├── config/
│   ├── __init__.py
│   └── settings.py       # Configuration management
│
├── utils/
│   ├── __init__.py
│   ├── aci_client.py     # Cobra SDK wrapper
│   └── netbox_client.py  # pynetbox wrapper with bulk fetch and caching
│
└── sync_modules/
    ├── __init__.py       # Module exports and ordering
    ├── base.py           # Base class, field mapping helpers, orchestrator
    ├── fabric_sync.py    # Fabric, Pods, Nodes
    ├── tenant_sync.py    # Tenants
    ├── vrf_sync.py       # VRFs
    ├── bd_sync.py        # Bridge Domains, Subnets (with IPAM prefix creation)
    ├── ap_sync.py        # Application Profiles
    ├── epg_sync.py       # Endpoint Groups
    ├── esg_sync.py       # Endpoint Security Groups
    └── contract_sync.py  # Contracts, Filters, Relationships
```

## Architecture

### Declarative Field Mappings

Sync modules define `FIELD_MAP` and `CONVERTERS` as class-level attributes instead of
repeating field-by-field comparison logic. The base class provides `_build_params()`,
`_build_updates()`, and `_apply_updates()` methods that handle all conversion,
comparison, and update tracking automatically.

```python
class VRFSyncModule(BaseSyncModule):
    FIELD_MAP = {
        'name_alias': 'name_alias',
        'description': 'description',
        'bd_enforced_enabled': 'bd_enforcement_enabled',
        'ip_data_plane_learning': 'ip_data_plane_learning_enabled',
        'pc_enf_dir': 'pc_enforcement_direction',
        'pc_enf_pref': 'pc_enforcement_preference',
        # ...
    }
    CONVERTERS = {
        'ip_data_plane_learning': lambda v: v == 'enabled',
    }
```

### Pre-Fetch Caching

Each sync module's `pre_sync()` hook bulk-fetches all existing NetBox objects for its
scope (per tenant, per app profile, etc.) in a single API call. Individual object syncs
then look up from the in-memory cache instead of making per-object GET requests:

- **Before**: ~200+ individual GET calls for existence checks
- **After**: ~12 bulk GET calls (one per object type per tenant)

### IPAM Integration

When syncing BD subnets, the script automatically creates both the parent prefix and
the gateway IP address in NetBox IPAM. For example, a BD subnet with gateway
`10.1.1.1/24` will create:

- **Prefix**: `10.1.1.0/24` under IPAM → Prefixes
- **IP Address**: `10.1.1.1/24` under IPAM → IP Addresses

This ensures proper IPAM hierarchy in NetBox. The subnet lookup uses the plugin's
unique constraint on `gateway_ip_address_id`, so re-runs are idempotent and BD
reassignments are handled automatically.

## Sync Order

Objects are synchronized in dependency order:

1. **Fabric** — Creates the ACI fabric reference
2. **Pods** — Fabric pods (depends on Fabric)
3. **Nodes** — Spine/Leaf nodes (depends on Fabric, Pods)
4. **Tenants** — ACI tenants (depends on Fabric)
5. **VRFs** — Virtual routing contexts (depends on Tenants)
6. **Bridge Domains** — Layer 2 domains (depends on Tenants, VRFs)
7. **Subnets** — BD subnets/gateways (depends on BDs)
8. **Application Profiles** — AP containers (depends on Tenants)
9. **EPGs** — Endpoint groups (depends on APs, BDs)
10. **ESGs** — Endpoint security groups (depends on APs, VRFs)
11. **Contract Filters** — Filter definitions (depends on Tenants)
12. **Contracts** — Security contracts (depends on Tenants, Filters)
13. **Contract Relationships** — Provider/consumer bindings (depends on Contracts, EPGs, VRFs)

## New in Plugin 0.2.0

This sync tool supports the new features in netbox-aci-plugin 0.2.0:

### Fabric Details
- `fabric_id` — ACI fabric identifier (1-128)
- `infra_vlan_id` — Infrastructure VLAN ID (2-4094)
- `gipo_pool` — GIPO multicast address pool

### Fabric Nodes
- Node ID, name, serial number
- Node role (spine, leaf, controller)
- Node model and firmware version
- TEP address and fabric state
- Pod assignment
- Automatic DCIM device creation and linking

## Output Example

```
============================================================
SYNC SUMMARY
============================================================
Fabric: created=1, updated=0, unchanged=0, failed=0, verified=1
Pod: created=2, updated=0, unchanged=0, failed=0, verified=2
Node: created=6, updated=0, unchanged=0, failed=0, verified=6
Tenant: created=4, updated=0, unchanged=1, failed=0, verified=4
VRF: created=8, updated=2, unchanged=0, failed=0, verified=10
BridgeDomain: created=15, updated=3, unchanged=2, failed=0, verified=18
Subnet: created=12, updated=0, unchanged=3, failed=0, verified=12
ApplicationProfile: created=6, updated=0, unchanged=2, failed=0, verified=6
EndpointGroup: created=24, updated=5, unchanged=1, failed=0, verified=29
EndpointSecurityGroup: created=3, updated=0, unchanged=0, failed=0, verified=3
ContractFilter: created=5, updated=0, unchanged=3, failed=0, verified=5
Contract: created=8, updated=1, unchanged=2, failed=0, verified=9
ContractRelationship: created=10, updated=0, unchanged=5, failed=0, verified=10
------------------------------------------------------------
Total: created=104, updated=11, unchanged=19, failed=0
Duration: 45.32 seconds
============================================================
```

## Troubleshooting

### Connection Issues

**ACI Connection Failed**:
- Verify APIC hostname/IP is correct
- Check username/password
- Ensure network connectivity to APIC
- Check if `verify_ssl: false` is needed for self-signed certs

**NetBox Connection Failed**:
- Verify NetBox URL (include https://)
- Check API token has write permissions
- Ensure ACI plugin is installed and enabled

### Sync Errors

**Tenant/VRF/BD not found**:
- Ensure sync runs in correct order (use default module order)
- Check if parent objects exist in ACI

**Duplicate key constraint errors on subnets**:
- This was fixed in v1.1.0 by looking up subnets by `gateway_ip_address_id`
  alone, matching the plugin's unique constraint
- If upgrading from an older version, existing data will be found correctly

**Validation errors**:
- Review NetBox ACI plugin field requirements
- Check for special characters in names

### Performance

**Slow sync**:
- Consider using `--only` to sync specific object types
- Ensure good network connectivity to both systems
- Large environments may take several minutes
- Pre-fetch caching significantly reduces API calls on subsequent runs

## Changelog

### v1.1.0

- **Performance**: Pre-fetch caching reduces per-object API lookups to bulk fetches
- **Performance**: Cached contract relations eliminates O(n²) duplicate checks
- **Performance**: Reusable HTTP session for direct API calls
- **Performance**: Cached DCIM helpers (manufacturer, site, device types) during node sync
- **Maintainability**: Declarative `FIELD_MAP` / `CONVERTERS` in all sync modules
- **Maintainability**: Shared `_build_params()`, `_build_updates()`, `_apply_updates()` in base class
- **Maintainability**: Removed unused `sync_parallel` method (was running sequentially)
- **Bugfix**: Consistent `values_equal` comparison in `_update_if_changed` (was using lossy string conversion)
- **Bugfix**: Subnet lookup uses `gateway_ip_address_id` alone to match plugin unique constraint
- **Feature**: Automatic parent prefix creation in IPAM for BD subnets
- **Feature**: Contract relationship sync (EPG provider/consumer and vzAny)

### v1.0.0

- Initial release with full ACI object synchronization
- Support for netbox-aci-plugin 0.2.0 features (fabric, pods, nodes)

## License

This project is provided as-is for integration purposes.

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Submit a pull request

## Support

For issues with:
- **This sync tool**: Open an issue in this repository
- **NetBox ACI Plugin**: See [pheus/netbox-aci-plugin](https://github.com/pheus/netbox-aci-plugin)
- **Cisco Cobra SDK**: Contact Cisco support or DevNet