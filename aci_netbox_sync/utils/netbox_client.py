"""
NetBox Client - pynetbox wrapper with ACI plugin support.
Provides efficient CRUD operations for NetBox ACI plugin objects.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple
from functools import lru_cache
import time

logger = logging.getLogger(__name__)

try:
    import pynetbox
    PYNETBOX_AVAILABLE = True
except ImportError:
    PYNETBOX_AVAILABLE = False
    logger.warning("pynetbox not installed. Install with: pip install pynetbox")


class NetBoxClient:
    """
    NetBox Client using pynetbox for ACI plugin object management.
    Supports create, update, and verification operations.
    """

    def __init__(self, url: str, token: str, verify_ssl: bool = True, timeout: int = 30):
        self.url = url.rstrip('/')
        self.token = token
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self._api: Optional[pynetbox.api] = None
        self._connected = False

    def connect(self) -> bool:
        """Establish connection to NetBox."""
        if not PYNETBOX_AVAILABLE:
            logger.error("pynetbox not available")
            return False

        try:
            self._api = pynetbox.api(
                self.url,
                token=self.token,
            )
            if not self.verify_ssl:
                import requests
                session = requests.Session()
                session.verify = False
                self._api.http_session = session

            # Test connection
            self._api.status()
            self._connected = True
            logger.info(f"Connected to NetBox at {self.url}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to NetBox: {e}")
            self._connected = False
            return False

    @property
    def api(self) -> pynetbox.api:
        """Get pynetbox API instance."""
        if not self._connected or not self._api:
            raise RuntimeError("Not connected to NetBox")
        return self._api

    @property
    def aci_plugin(self):
        """Get ACI plugin API endpoint."""
        # The plugin package is netbox_aci_plugin but API endpoint is 'aci'
        return self.api.plugins.aci

    # Generic CRUD Operations
    def _get_or_create(self, endpoint, lookup_params: Dict, 
                       create_params: Dict) -> Tuple[Any, bool]:
        """
        Get existing object or create new one.
        Returns (object, created_flag).
        """
        try:
            # Try to get existing
            existing = endpoint.get(**lookup_params)
            if existing:
                return existing, False
            
            # Create new
            new_obj = endpoint.create(create_params)
            return new_obj, True
        except Exception as e:
            logger.error(f"Error in get_or_create: {e}")
            raise

    def _update_if_changed(self, obj: Any, updates: Dict, 
                           verify: bool = True) -> Tuple[bool, bool]:
        """
        Update object if any attributes have changed.
        Returns (changed, verified).
        """
        changes = {}
        for key, value in updates.items():
            current_value = getattr(obj, key, None)
            # Handle nested objects (like foreign keys)
            if hasattr(current_value, 'id'):
                current_value = current_value.id
            if hasattr(value, 'id'):
                value = value.id
            
            # Normalize for comparison
            # Handle None vs empty string
            if current_value is None and value == '':
                continue
            if current_value == '' and value is None:
                continue
            
            # Convert to strings for reliable comparison
            current_str = str(current_value) if current_value is not None else ''
            new_str = str(value) if value is not None else ''
            
            if current_str != new_str:
                changes[key] = value

        if not changes:
            return False, True

        try:
            obj.update(changes)
            
            if verify:
                # Re-fetch to verify - try different refresh methods
                time.sleep(0.1)  # Brief delay for consistency
                try:
                    # Try full_details() first (newer pynetbox)
                    if hasattr(obj, 'full_details'):
                        obj.full_details()
                    elif hasattr(obj, 'refresh'):
                        obj.refresh()
                except Exception as refresh_err:
                    logger.debug(f"Could not refresh object for verification: {refresh_err}")
                    # Continue without verification
                    return True, False
                    
                for key, expected in changes.items():
                    actual = getattr(obj, key, None)
                    if hasattr(actual, 'id'):
                        actual = actual.id
                    # Use string comparison for verification too
                    actual_str = str(actual) if actual is not None else ''
                    expected_str = str(expected) if expected is not None else ''
                    if actual_str != expected_str:
                        logger.warning(f"Verification failed for {key}: expected {expected}, got {actual}")
                        return True, False
                return True, True
            return True, True
        except Exception as e:
            logger.error(f"Error updating object: {e}")
            return False, False

    # Fabric Operations
    def get_or_create_fabric(self, name: str, fabric_id: int = 1, **kwargs) -> Tuple[Any, bool]:
        """Get or create an ACI Fabric. Looks up by fabric_id first since it's unique."""
        try:
            # First try to get by fabric_id (unique constraint)
            existing = self.aci_plugin.fabrics.get(fabric_id=fabric_id)
            if existing:
                return existing, False
            
            # Then try by name
            existing = self.aci_plugin.fabrics.get(name=name)
            if existing:
                return existing, False
            
            # Create new
            create_params = {'name': name, 'fabric_id': fabric_id, **kwargs}
            new_obj = self.aci_plugin.fabrics.create(create_params)
            return new_obj, True
        except Exception as e:
            logger.error(f"Error in get_or_create_fabric: {e}")
            raise

    def update_fabric(self, fabric: Any, updates: Dict, verify: bool = True) -> Tuple[bool, bool]:
        """Update fabric with given attributes."""
        return self._update_if_changed(fabric, updates, verify)

    def get_fabric_by_name(self, name: str) -> Optional[Any]:
        """Get fabric by name."""
        try:
            return self.aci_plugin.fabrics.get(name=name)
        except Exception:
            return None

    # Pod Operations
    def get_or_create_pod(self, fabric_id: int, pod_id: int, **kwargs) -> Tuple[Any, bool]:
        """Get or create an ACI Pod."""
        name = kwargs.get('name', f"pod-{pod_id}")
        return self._get_or_create(
            self.aci_plugin.pods,
            {'aci_fabric_id': fabric_id, 'pod_id': pod_id},
            {'aci_fabric': fabric_id, 'pod_id': pod_id, 'name': name, **kwargs}
        )

    def update_pod(self, pod: Any, updates: Dict, verify: bool = True) -> Tuple[bool, bool]:
        """Update pod with given attributes."""
        return self._update_if_changed(pod, updates, verify)

    # Node Operations  
    def get_or_create_node(self, fabric_id: int, node_id: int, **kwargs) -> Tuple[Any, bool]:
        """Get or create an ACI Node."""
        name = kwargs.get('name', f"node-{node_id}")
        aci_pod = kwargs.get('aci_pod')
        model = kwargs.get('model')
        
        try:
            # Try to get by fabric and node_id first
            existing = self.aci_plugin.nodes.get(aci_fabric_id=fabric_id, node_id=node_id)
            if existing:
                return existing, False
            
            # Also try by name within fabric
            existing = self.aci_plugin.nodes.get(aci_fabric_id=fabric_id, name=name)
            if existing:
                return existing, False
            
            # Query all nodes in the fabric and look for a match by name
            # This handles cases where other lookup params don't match
            all_nodes = list(self.aci_plugin.nodes.filter(aci_fabric_id=fabric_id))
            for node in all_nodes:
                if node.name == name:
                    return node, False
            
            # Create new - remove name from kwargs since we use it explicitly
            create_kwargs = {k: v for k, v in kwargs.items() if k != 'name'}
            create_data = {
                'aci_fabric': fabric_id, 
                'node_id': node_id, 
                'name': name, 
                **create_kwargs
            }
            logger.debug(f"Creating node with data: {create_data}")
            new_obj = self.aci_plugin.nodes.create(create_data)
            return new_obj, True
        except Exception as e:
            logger.error(f"Error in get_or_create_node: {e}")
            raise

    def update_node(self, node: Any, updates: Dict, verify: bool = True) -> Tuple[bool, bool]:
        """Update node with given attributes."""
        return self._update_if_changed(node, updates, verify)

    # Tenant Operations
    def get_or_create_tenant(self, fabric_id: int, name: str, **kwargs) -> Tuple[Any, bool]:
        """Get or create an ACI Tenant."""
        return self._get_or_create(
            self.aci_plugin.tenants,
            {'aci_fabric_id': fabric_id, 'name': name},
            {'aci_fabric': fabric_id, 'name': name, **kwargs}
        )

    def update_tenant(self, tenant: Any, updates: Dict, verify: bool = True) -> Tuple[bool, bool]:
        """Update tenant with given attributes."""
        return self._update_if_changed(tenant, updates, verify)

    def get_tenant_by_name(self, fabric_id: int, name: str) -> Optional[Any]:
        """Get tenant by name."""
        try:
            return self.aci_plugin.tenants.get(aci_fabric_id=fabric_id, name=name)
        except Exception:
            return None

    # VRF Operations
    def get_or_create_vrf(self, tenant_id: int, name: str, **kwargs) -> Tuple[Any, bool]:
        """Get or create an ACI VRF."""
        return self._get_or_create(
            self.aci_plugin.vrfs,
            {'aci_tenant_id': tenant_id, 'name': name},
            {'aci_tenant': tenant_id, 'name': name, **kwargs}
        )

    def update_vrf(self, vrf: Any, updates: Dict, verify: bool = True) -> Tuple[bool, bool]:
        """Update VRF with given attributes."""
        return self._update_if_changed(vrf, updates, verify)

    def get_vrf_by_name(self, tenant_id: int, name: str) -> Optional[Any]:
        """Get VRF by name."""
        try:
            return self.aci_plugin.vrfs.get(aci_tenant_id=tenant_id, name=name)
        except Exception:
            return None

    # Bridge Domain Operations
    def get_or_create_bridge_domain(self, tenant_id: int, vrf_id: int, 
                                     name: str, **kwargs) -> Tuple[Any, bool]:
        """Get or create an ACI Bridge Domain."""
        return self._get_or_create(
            self.aci_plugin.bridge_domains,
            {'aci_tenant_id': tenant_id, 'name': name},
            {'aci_tenant': tenant_id, 'aci_vrf': vrf_id, 'name': name, **kwargs}
        )

    def update_bridge_domain(self, bd: Any, updates: Dict, verify: bool = True) -> Tuple[bool, bool]:
        """Update Bridge Domain with given attributes."""
        return self._update_if_changed(bd, updates, verify)

    def get_bridge_domain_by_name(self, tenant_id: int, name: str) -> Optional[Any]:
        """Get Bridge Domain by name."""
        try:
            return self.aci_plugin.bridge_domains.get(aci_tenant_id=tenant_id, name=name)
        except Exception:
            return None

    # Subnet Operations
    def get_or_create_subnet(self, bd_id: int, gateway_ip: int, **kwargs) -> Tuple[Any, bool]:
        """Get or create a Bridge Domain Subnet."""
        # gateway_ip should be the NetBox IPAM IP address ID
        # Pass it as a dict for the API
        return self._get_or_create(
            self.aci_plugin.bridge_domain_subnets,
            {'aci_bridge_domain_id': bd_id, 'gateway_ip_address_id': gateway_ip},
            {'aci_bridge_domain': bd_id, 'gateway_ip_address': gateway_ip, **kwargs}
        )

    def update_subnet(self, subnet: Any, updates: Dict, verify: bool = True) -> Tuple[bool, bool]:
        """Update subnet with given attributes."""
        return self._update_if_changed(subnet, updates, verify)

    # Application Profile Operations
    def get_or_create_app_profile(self, tenant_id: int, name: str, **kwargs) -> Tuple[Any, bool]:
        """Get or create an ACI Application Profile."""
        return self._get_or_create(
            self.aci_plugin.app_profiles,
            {'aci_tenant_id': tenant_id, 'name': name},
            {'aci_tenant': tenant_id, 'name': name, **kwargs}
        )

    def update_app_profile(self, ap: Any, updates: Dict, verify: bool = True) -> Tuple[bool, bool]:
        """Update Application Profile with given attributes."""
        return self._update_if_changed(ap, updates, verify)

    def get_app_profile_by_name(self, tenant_id: int, name: str) -> Optional[Any]:
        """Get Application Profile by name."""
        try:
            return self.aci_plugin.app_profiles.get(aci_tenant_id=tenant_id, name=name)
        except Exception:
            return None

    # EPG Operations
    def get_or_create_epg(self, ap_id: int, bd_id: int, name: str, **kwargs) -> Tuple[Any, bool]:
        """Get or create an ACI Endpoint Group."""
        return self._get_or_create(
            self.aci_plugin.endpoint_groups,
            {'aci_app_profile_id': ap_id, 'name': name},
            {'aci_app_profile': ap_id, 'aci_bridge_domain': bd_id, 'name': name, **kwargs}
        )

    def update_epg(self, epg: Any, updates: Dict, verify: bool = True) -> Tuple[bool, bool]:
        """Update EPG with given attributes."""
        return self._update_if_changed(epg, updates, verify)

    def get_epg_by_name(self, ap_id: int, name: str) -> Optional[Any]:
        """Get EPG by name."""
        try:
            return self.aci_plugin.endpoint_groups.get(aci_app_profile_id=ap_id, name=name)
        except Exception:
            return None

    # ESG Operations
    def get_or_create_esg(self, ap_id: int, vrf_id: int, name: str, **kwargs) -> Tuple[Any, bool]:
        """Get or create an ACI Endpoint Security Group."""
        return self._get_or_create(
            self.aci_plugin.endpoint_security_groups,
            {'aci_app_profile_id': ap_id, 'name': name},
            {'aci_app_profile': ap_id, 'aci_vrf': vrf_id, 'name': name, **kwargs}
        )

    def update_esg(self, esg: Any, updates: Dict, verify: bool = True) -> Tuple[bool, bool]:
        """Update ESG with given attributes."""
        return self._update_if_changed(esg, updates, verify)

    def get_esg_by_name(self, ap_id: int, name: str) -> Optional[Any]:
        """Get ESG by name."""
        try:
            return self.aci_plugin.endpoint_security_groups.get(aci_app_profile_id=ap_id, name=name)
        except Exception:
            return None

    # Contract Operations
    def get_or_create_contract(self, tenant_id: int, name: str, **kwargs) -> Tuple[Any, bool]:
        """Get or create an ACI Contract."""
        return self._get_or_create(
            self.aci_plugin.contracts,
            {'aci_tenant_id': tenant_id, 'name': name},
            {'aci_tenant': tenant_id, 'name': name, **kwargs}
        )

    def update_contract(self, contract: Any, updates: Dict, verify: bool = True) -> Tuple[bool, bool]:
        """Update Contract with given attributes."""
        return self._update_if_changed(contract, updates, verify)

    def get_contract_by_name(self, tenant_id: int, name: str) -> Optional[Any]:
        """Get Contract by name."""
        try:
            return self.aci_plugin.contracts.get(aci_tenant_id=tenant_id, name=name)
        except Exception:
            return None

    # Contract Filter Operations
    def get_or_create_contract_filter(self, tenant_id: int, name: str, **kwargs) -> Tuple[Any, bool]:
        """Get or create an ACI Contract Filter."""
        return self._get_or_create(
            self.aci_plugin.contract_filters,
            {'aci_tenant_id': tenant_id, 'name': name},
            {'aci_tenant': tenant_id, 'name': name, **kwargs}
        )

    def update_contract_filter(self, flt: Any, updates: Dict, verify: bool = True) -> Tuple[bool, bool]:
        """Update Contract Filter with given attributes."""
        return self._update_if_changed(flt, updates, verify)

    # Contract Subject Operations
    def get_or_create_contract_subject(self, contract_id: int, name: str, **kwargs) -> Tuple[Any, bool]:
        """Get or create an ACI Contract Subject."""
        return self._get_or_create(
            self.aci_plugin.contract_subjects,
            {'aci_contract_id': contract_id, 'name': name},
            {'aci_contract': contract_id, 'name': name, **kwargs}
        )

    def update_contract_subject(self, subject: Any, updates: Dict, verify: bool = True) -> Tuple[bool, bool]:
        """Update Contract Subject with given attributes."""
        return self._update_if_changed(subject, updates, verify)

    # Contract Filter Entry Operations
    def get_or_create_filter_entry(self, filter_id: int, name: str, **kwargs) -> Tuple[Any, bool]:
        """Get or create an ACI Contract Filter Entry."""
        return self._get_or_create(
            self.aci_plugin.contract_filter_entries,
            {'aci_contract_filter_id': filter_id, 'name': name},
            {'aci_contract_filter': filter_id, 'name': name, **kwargs}
        )

    def update_filter_entry(self, entry: Any, updates: Dict, verify: bool = True) -> Tuple[bool, bool]:
        """Update Contract Filter Entry with given attributes."""
        return self._update_if_changed(entry, updates, verify)

    # Contract Relation Operations
    def create_contract_relation(self, contract_id: int, epg_id: int, role: str, tenant_id: int = None, fabric_id: int = None) -> bool:
        """
        Create a contract relation (EPG as provider/consumer).
        Uses endpoint: /api/plugins/aci/contract-relations/
        
        Args:
            contract_id: NetBox ACI Contract ID
            epg_id: NetBox ACI Endpoint Group ID
            role: 'prov' (provider) or 'cons' (consumer)
            tenant_id: Optional NetBox ACI Tenant ID
            fabric_id: Optional NetBox ACI Fabric ID
            
        Returns:
            True if created, False if already exists or failed
        """
        try:
            base = self.url.rstrip('/')
            url = f"{base}/api/plugins/aci/contract-relations/"
            
            headers = {
                "Authorization": f"Token {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            
            import requests
            session = requests.Session()
            session.verify = self.verify_ssl
            
            # Check for existing relations
            response = session.get(url, headers=headers)
            
            if response.status_code != 200:
                logger.warning(f"Failed to query contract relations: {response.status_code}")
                return False
            
            try:
                existing = response.json()
            except Exception:
                logger.warning("Invalid JSON response from contract-relations endpoint")
                return False
            
            # Check if this exact relation already exists
            results = existing.get('results', existing if isinstance(existing, list) else [])
            for rel in results:
                rel_object_id = rel.get('aci_object_id')
                rel_contract = rel.get('aci_contract')
                rel_contract_id = rel_contract.get('id') if isinstance(rel_contract, dict) else rel_contract
                rel_role = rel.get('role')
                
                if rel_object_id == epg_id and rel_contract_id == contract_id and rel_role == role:
                    logger.debug(f"Contract relation already exists: epg={epg_id}, contract={contract_id}, role={role}")
                    return False
            
            # Build POST data
            post_data = {
                'aci_contract': contract_id,
                'aci_object_type': 'netbox_aci_plugin.aciendpointgroup',
                'aci_object_id': epg_id,
                'role': role
            }
            
            if tenant_id:
                post_data['aci_tenant'] = tenant_id
            
            if fabric_id:
                post_data['aci_fabric'] = fabric_id
            
            # Create new relation
            logger.info(f"Creating contract relation: epg={epg_id}, contract={contract_id}, role={role}, tenant={tenant_id}")
            response = session.post(url, headers=headers, json=post_data)
            
            if response.status_code in (200, 201):
                logger.info("Successfully created contract relation")
                return True
            elif response.status_code == 500:
                logger.debug(f"Contract relation creation failed with 500 error: {response.text[:100]}")
                return False
            else:
                logger.warning(f"Failed to create contract relation: {response.status_code} - {response.text[:200]}")
                return False
                
        except Exception as e:
            logger.warning(f"Error creating contract relation: {e}")
            return False

    def create_vrf_contract_relation(self, vrf_id: int, contract_id: int, role: str, tenant_id: int = None) -> bool:
        """
        Create a VRF contract relation for vzAny.
        
        Args:
            vrf_id: NetBox ACI VRF ID
            contract_id: NetBox ACI Contract ID
            role: 'prov' (provider) or 'cons' (consumer)
            tenant_id: Optional NetBox ACI Tenant ID
            
        Returns:
            True if created, False if already exists or failed
            
        Note: This uses the generic contract-relations endpoint with VRF content type.
              The vzAny feature may not be fully supported by all NetBox ACI plugin versions.
        """
        try:
            base = self.url.rstrip('/')
            url = f"{base}/api/plugins/aci/contract-relations/"
            
            headers = {
                "Authorization": f"Token {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            
            import requests
            session = requests.Session()
            session.verify = self.verify_ssl
            
            # Check for existing relations
            response = session.get(url, headers=headers)
            
            if response.status_code != 200:
                logger.warning(f"Failed to query contract relations for VRF: {response.status_code}")
                return False
            
            try:
                existing = response.json()
            except Exception:
                logger.warning("Invalid JSON response from contract-relations endpoint")
                return False
            
            # Check if this exact relation already exists
            results = existing.get('results', existing if isinstance(existing, list) else [])
            for rel in results:
                rel_object_id = rel.get('aci_object_id')
                rel_contract = rel.get('aci_contract')
                rel_contract_id = rel_contract.get('id') if isinstance(rel_contract, dict) else rel_contract
                rel_role = rel.get('role')
                rel_type = rel.get('aci_object_type', '')
                
                # Check if it's a VRF relation
                if 'vrf' in rel_type.lower() and rel_object_id == vrf_id and rel_contract_id == contract_id and rel_role == role:
                    logger.debug(f"VRF contract relation already exists: vrf={vrf_id}, contract={contract_id}, role={role}")
                    return False
            
            # Build POST data using the generic contract-relations endpoint
            post_data = {
                'aci_contract': contract_id,
                'aci_object_type': 'netbox_aci_plugin.acivrf',
                'aci_object_id': vrf_id,
                'role': role
            }
            
            if tenant_id:
                post_data['aci_tenant'] = tenant_id
            
            # Create new relation
            logger.info(f"Creating VRF contract relation: vrf={vrf_id}, contract={contract_id}, role={role}, tenant={tenant_id}")
            response = session.post(url, headers=headers, json=post_data)
            
            if response.status_code in (200, 201):
                logger.info("Successfully created VRF contract relation")
                return True
            else:
                logger.warning(f"Failed to create VRF contract relation: {response.status_code} - {response.text[:200]}")
                return False
                
        except Exception as e:
            logger.warning(f"Error creating VRF contract relation: {e}")
            return False

    # DCIM Device Operations (for ACI node linking)
    def get_or_create_dcim_device(self, name: str, device_type_id: int, 
                                   site_id: int, role_id: int, **kwargs) -> Tuple[Any, bool]:
        """Get or create a DCIM Device for ACI node linking."""
        return self._get_or_create(
            self.api.dcim.devices,
            {'name': name},
            {'name': name, 'device_type': device_type_id, 'site': site_id, 
             'role': role_id, **kwargs}
        )

    def get_or_create_device_type(self, manufacturer_id: int, model: str, **kwargs) -> Tuple[Any, bool]:
        """Get or create a device type."""
        slug = model.lower().replace(' ', '-').replace('/', '-')[:50]
        return self._get_or_create(
            self.api.dcim.device_types,
            {'model': model},
            {'manufacturer': manufacturer_id, 'model': model, 'slug': slug, **kwargs}
        )

    def get_or_create_manufacturer(self, name: str) -> Tuple[Any, bool]:
        """Get or create a manufacturer."""
        slug = name.lower().replace(' ', '-')[:50]
        return self._get_or_create(
            self.api.dcim.manufacturers,
            {'name': name},
            {'name': name, 'slug': slug}
        )

    def get_or_create_site(self, name: str) -> Tuple[Any, bool]:
        """Get or create a site."""
        slug = name.lower().replace(' ', '-')[:50]
        return self._get_or_create(
            self.api.dcim.sites,
            {'name': name},
            {'name': name, 'slug': slug}
        )

    def get_or_create_device_role(self, name: str) -> Tuple[Any, bool]:
        """Get or create a device role."""
        slug = name.lower().replace(' ', '-')[:50]
        return self._get_or_create(
            self.api.dcim.device_roles,
            {'name': name},
            {'name': name, 'slug': slug}
        )

    # IP Address Management (for subnets)
    def get_or_create_ip_address(self, address: str, **kwargs) -> Tuple[Any, bool]:
        """Get or create a NetBox IP Address for subnet gateway or TEP IP.
        
        Args:
            address: IP address with optional mask (e.g., "10.0.0.1" or "10.0.0.1/16")
            **kwargs: Additional fields like description
            
        Returns:
            Tuple of (ip_address_object, created_bool)
        """
        # Extract just the IP part for searching
        ip_only = address.split('/')[0] if '/' in address else address
        
        try:
            # First, try to find existing IP by address (any mask)
            # NetBox allows searching by IP without mask
            existing = list(self.api.ipam.ip_addresses.filter(address=ip_only))
            if existing:
                # Return the first match
                return existing[0], False
        except Exception as e:
            logger.debug(f"Error searching for IP {ip_only}: {e}")
        
        # If not found, create with the specified address (including mask)
        return self._get_or_create(
            self.api.ipam.ip_addresses,
            {'address': address},
            {'address': address, **kwargs}
        )

    def get_or_create_prefix(self, prefix: str, **kwargs) -> Tuple[Any, bool]:
        """Get or create a NetBox Prefix for TEP pool or other use."""
        return self._get_or_create(
            self.api.ipam.prefixes,
            {'prefix': prefix},
            {'prefix': prefix, **kwargs}
        )

    def get_ip_address(self, address: str) -> Optional[Any]:
        """Get IP address by address string."""
        try:
            return self.api.ipam.ip_addresses.get(address=address)
        except Exception:
            return None

    # Bulk Operations
    def bulk_create(self, endpoint, objects: List[Dict]) -> List[Any]:
        """Create multiple objects at once."""
        try:
            return endpoint.create(objects)
        except Exception as e:
            logger.error(f"Bulk create failed: {e}")
            return []

    # Cache Management
    def clear_cache(self) -> None:
        """Clear any cached data."""
        pass  # pynetbox doesn't cache by default, but this is here for future use
