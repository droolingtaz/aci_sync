# ACI to NetBox Synchronization

A modular Python solution for synchronizing Cisco ACI (Application Centric Infrastructure) objects to NetBox using the [netbox-aci-plugin](https://github.com/pheus/netbox-aci-plugin) version 0.2.0+.

## Features

- **Comprehensive ACI Object Support**:
  - Fabric details (fabric_id, infra_vlan_id, gipo_pool) - *New in plugin 0.2.0*
  - Fabric Pods and Nodes - *New in plugin 0.2.0*
  - Tenants with all attributes
  - VRFs (Contexts) with policy settings
  - Bridge Domains with full configuration
  - BD Subnets with scope and control settings
  - Application Profiles
  - Endpoint Groups (EPGs) including uSeg EPGs
  - Endpoint Security Groups (ESGs)
  - Contracts, Subjects, and Filters
  - Contract Relationships (EPG provider/consumer and vzAny)
  - **Firmware Version Tracking** via [netbox-software-tracker](https://pypi.org/project/netbox-software-tracker/) plugin

- **Optimized for Performance**:
  - Modular design keeps file sizes small
  - Efficient batched operations
  - Connection pooling with retry logic
  - Minimal API calls through smart caching

- **Robust Update Handling**:
  - Detects changes before updating
  - Verifies updates were successful
  - Continues on errors (configurable)
  - Detailed logging and statistics

## Requirements

- Python 3.9+
- NetBox 4.5.x with ACI Plugin 0.2.0
- Cisco ACI APIC access
- Cobra SDK (acicobra, acimodel)

### Optional

- [netbox-software-tracker](https://pypi.org/project/netbox-software-tracker/) plugin v0.3.7+ (for firmware version tracking)

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

5. **(Optional) Install netbox-software-tracker plugin** in your NetBox instance:
   ```bash
   pip install netbox-software-tracker
   ```
   Then add `'netbox_software_tracker'` to your NetBox `PLUGINS` configuration and run migrations.

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

# Sync firmware versions only (requires netbox-software-tracker plugin)
python -m aci_netbox_sync --only software
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
                   bds, subnets, aps, epgs, esgs, contracts,
                   software

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
│   └── netbox_client.py  # pynetbox wrapper + software tracker
│
└── sync_modules/
    ├── __init__.py       # Module exports and ordering
    ├── base.py           # Base class and orchestrator
    ├── fabric_sync.py    # Fabric, Pods, Nodes
    ├── tenant_sync.py    # Tenants
    ├── vrf_sync.py       # VRFs
    ├── bd_sync.py        # Bridge Domains, Subnets
    ├── ap_sync.py        # Application Profiles
    ├── epg_sync.py       # Endpoint Groups
    ├── esg_sync.py       # Endpoint Security Groups
    ├── contract_sync.py  # Contracts, Filters, Relationships
    └── software_sync.py  # Firmware Version Tracking
```

## Sync Order

Objects are synchronized in dependency order:

1. **Fabric** - Creates the ACI fabric reference
2. **Pods** - Fabric pods (depends on Fabric)
3. **Nodes** - Spine/Leaf nodes (depends on Fabric, optionally Pods)
4. **Tenants** - ACI tenants (depends on Fabric)
5. **VRFs** - Virtual routing contexts (depends on Tenants)
6. **Bridge Domains** - Layer 2 domains (depends on Tenants, VRFs)
7. **Subnets** - BD subnets/gateways (depends on BDs)
8. **Application Profiles** - AP containers (depends on Tenants)
9. **EPGs** - Endpoint groups (depends on APs, BDs)
10. **ESGs** - Endpoint security groups (depends on APs, VRFs)
11. **Contract Filters** - Filter definitions (depends on Tenants)
12. **Contracts** - Security contracts (depends on Tenants, Filters)
13. **Contract Relationships** - Provider/consumer bindings (depends on Contracts, EPGs, VRFs)
14. **Software Versions** - Firmware tracking (depends on Nodes)

## Firmware Version Tracking

This sync tool integrates with the [netbox-software-tracker](https://pypi.org/project/netbox-software-tracker/) plugin to track ACI firmware versions in NetBox.

### What Gets Synced

- **Software Images**: Each unique firmware version running on ACI nodes is created as a software-image entry in the tracker plugin (e.g., `6.1(4h)`, `16.0(7h)`)
- **Device Firmware Context**: Each DCIM device (switch/node) gets its running firmware version set in `local_context_data`, visible on the device page
- **Golden Images**: Firmware versions are linked to their corresponding device types (e.g., N9K-C9396PX → 6.1(4h))
- **Image Metadata**: When firmware images are staged in the APIC repository, filenames and MD5 checksums are automatically populated

### How It Works

1. **pre_sync**: Queries the APIC for firmware details across multiple classes (`firmwareRunning`, `firmwareCtrlrRunning`, `firmwareFirmware`, etc.) to collect filenames and checksums when available
2. **sync**: Deduplicates firmware versions across all nodes and creates/updates software-image entries in NetBox
3. **post_sync**: 
   - Sets `local_context_data` on each DCIM device with firmware info
   - Assigns golden images linking software versions to device types

### Device Context Data

After sync, each device's local context will contain:

```json
{
  "firmware": {
    "version": "16.0(7h)",
    "model": "N9K-C9396PX",
    "serial": "FDO12345678",
    "filename": "aci-n9000-dk9.16.0.7h.bin",
    "checksum": "a1b2c3d4e5f6..."
  }
}
```

The `filename` and `checksum` fields are populated when firmware images are staged in the APIC firmware repository. On simulators or when no images are staged, only `version`, `model`, and `serial` will be present.

### Plugin API Endpoints

The software tracker plugin registers at `/api/plugins/netbox_software_tracker/` with two endpoints:

| Endpoint | Fields | Description |
|---|---|---|
| `software-image/` | version (32), filename (256), md5sum (36), comments | Firmware version entries |
| `golden-image/` | software (FK), device_type (FK) | Links versions to device types |

### Usage

```bash
# Sync firmware versions only
python -m aci_netbox_sync --only software

# Full sync including firmware
python -m aci_netbox_sync

# Skip firmware if plugin isn't installed
python -m aci_netbox_sync --skip software
```

## New in Plugin 0.2.0

This sync tool supports the new features in netbox-aci-plugin 0.2.0:

### Fabric Details
- `fabric_id` - ACI fabric identifier (1-128)
- `infra_vlan_id` - Infrastructure VLAN ID (2-4094)
- `gipo_pool` - GIPO multicast address pool

### Fabric Nodes
- Node ID, name, serial number
- Node role (spine, leaf, controller)
- Node model and firmware version
- TEP address and fabric state
- Pod assignment

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
ContractRelationship: created=12, updated=0, unchanged=4, failed=0, verified=0
SoftwareVersion: created=2, updated=0, unchanged=0, failed=0, verified=2
------------------------------------------------------------
Total: created=108, updated=11, unchanged=18, failed=0
Duration: 52.15 seconds
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

**Validation errors**:
- Review NetBox ACI plugin field requirements
- Check for special characters in names

**Software tracker 404 errors**:
- Verify the netbox-software-tracker plugin is installed and enabled
- Check that the API is accessible at `/api/plugins/netbox_software_tracker/`
- Use `--skip software` to exclude firmware sync if the plugin isn't available

**No firmware filename/checksum populated**:
- This is expected on ACI simulators and sandboxes
- On real fabrics, firmware images must be staged in the APIC repository for filenames and checksums to be available
- The APIC classes `firmwareFirmware` and `firmwareRunning` are the primary sources

### Performance

**Slow sync**:
- Consider using `--only` to sync specific object types
- Ensure good network connectivity to both systems
- Large environments may take several minutes

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
- **NetBox Software Tracker**: See [netbox-software-tracker](https://pypi.org/project/netbox-software-tracker/)
- **Cisco Cobra SDK**: Contact Cisco support or DevNet