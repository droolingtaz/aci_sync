#!/usr/bin/env python3
"""
ACI to NetBox Sync - Standalone Runner

Run this script directly without needing to install the package:
    python run_sync.py -c config.yaml
    
Or with environment variables:
    python run_sync.py
    
Options:
    python run_sync.py --help           # Show all options
    python run_sync.py --dry-run        # Preview changes
    python run_sync.py --only tenants   # Sync specific objects
"""

import sys
import os

# Add the current directory to the path so imports work
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)

from aci_netbox_sync.main import main

if __name__ == '__main__':
    sys.exit(main())
