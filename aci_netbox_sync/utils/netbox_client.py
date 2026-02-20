"""
NetBox Client - pynetbox wrapper with ACI plugin support.
Provides efficient CRUD operations for NetBox ACI plugin objects.

Optimized with:
- Reusable HTTP session for direct API calls (contract relations)
- values_equal() for consistent comparison logic
- Bulk fetch methods for pre-caching existing objects
- Cached contract relations to avoid per-object GET calls
"""

import logging
from typing import Any, Dict, List, Optional, Set, Tuple
import time

logger = logging.getLogger(__name__)

try:
    import pynetbox
    PYNETBOX_AVAILABLE = True
except ImportError:
    PYNETBOX_AVAILABLE = False
    logger.warning("pynetbox not installed. Install with: pip install pynetbox")


def _values_equal(current: Any, new: Any) -> bool:
    """
    Compare two values for equality, handling common type mismatches.
    Duplicated from base.py to avoid circular imports.
    """
    if current is None and new == '':
        return True
    if current == '' and new is None:
        return True
    if current is None and new is None:
        return True

    if hasattr(current, 'id'):
        current = current.id
    if hasattr(new, 'id'):
        new = new.id

    if isinstance(new, bool):
        current = bool(current) if current is not None else False
        return current == new

    if isinstance(current, str) and isinstance(new, str):
        return current == new

    return current == new


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
        self._http_session = None
        self._contract_relations_cache: Optional[List[Dict]] = None

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
        return self.api.plugins.aci

    @property
    def http_session(self):
        """
        Reusable HTTP session for direct API calls.
        Avoids creating a new requests.Session per call.
        """
        if self._http_session is None:
            import requests
            self._http_session = requests.Session()
            self._http_session.verify = self.verify_ssl
            self._http_session.headers.update({
                "Authorization": f"Token {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            })
        return self._http_session

    # ── Bulk Fetch Methods (for pre-caching) ─────────────────────────

    def fetch_all_tenants(self, fabric_id: int) -> Dict[str, Any]:
        """Fetch all tenants for a fabric, keyed by name."""
        try:
            return {t.name: t for t in self.aci_plugin.tenants.filter(aci_fabric_id=fabric_id)}
        except Exception as e:
            logger.error(f"Failed to bulk-fetch tenants: {e}")
            return {}

    def fetch_all_vrfs(self, tenant_id: int) -> Dict[str, Any]:
        """Fetch all VRFs for a tenant, keyed by name."""
        try:
            return {v.name: v for v in self.aci_plugin.vrfs.filter(aci_tenant_id=tenant_id)}
        except Exception as e:
            logger.error(f"Failed to bulk-fetch VRFs for tenant {tenant_id}: {e}")
            return {}

    def fetch_all_bridge_domains(self, tenant_id: int) -> Dict[str, Any]:
        """Fetch all bridge domains for a tenant, keyed by name."""
        try:
            return {bd.name: bd for bd in self.aci_plugin.bridge_domains.filter(aci_tenant_id=tenant_id)}
        except Exception as e:
            logger.error(f"Failed to bulk-fetch BDs for tenant {tenant_id}: {e}")
            return {}

    def fetch_all_app_profiles(self, tenant_id: int) -> Dict[str, Any]:
        """Fetch all app profiles for a tenant, keyed by name."""
        try:
            return {ap.name: ap for ap in self.aci_plugin.app_profiles.filter(aci_tenant_id=tenant_id)}
        except Exception as e:
            logger.error(f"Failed to bulk-fetch APs for tenant {tenant_id}: {e}")
            return {}

    def fetch_all_epgs(self, ap_id: int) -> Dict[str, Any]:
        """Fetch all EPGs for an app profile, keyed by name."""
        try:
            return {e.name: e for e in self.aci_plugin.endpoint_groups.filter(aci_app_profile_id=ap_id)}
        except Exception as e:
            logger.error(f"Failed to bulk-fetch EPGs for AP {ap_id}: {e}")
            return {}

    def fetch_all_esgs(self, ap_id: int) -> Dict[str, Any]:
        """Fetch all ESGs for an app profile, keyed by name."""
        try:
            return {e.name: e for e in self.aci_plugin.endpoint_security_groups.filter(aci_app_profile_id=ap_id)}
        except Exception as e:
            logger.error(f"Failed to bulk-fetch ESGs for AP {ap_id}: {e}")
            return {}

    def fetch_all_subnets(self, bd_id: int) -> Dict[int, Any]:
        """Fetch all subnets for a bridge domain, keyed by gateway_ip_address ID."""
        try:
            result = {}
            for s in self.aci_plugin.bridge_domain_subnets.filter(aci_bridge_domain_id=bd_id):
                gw = getattr(s, 'gateway_ip_address', None)
                gw_id = gw.id if hasattr(gw, 'id') else gw
                if gw_id:
                    result[gw_id] = s
            return result
        except Exception as e:
            logger.error(f"Failed to bulk-fetch subnets for BD {bd_id}: {e}")
            return {}

    def fetch_all_contracts(self, tenant_id: int) -> Dict[str, Any]:
        """Fetch all contracts for a tenant, keyed by name."""
        try:
            return {c.name: c for c in self.aci_plugin.contracts.filter(aci_tenant_id=tenant_id)}
        except Exception as e:
            logger.error(f"Failed to bulk-fetch contracts for tenant {tenant_id}: {e}")
            return {}

    def fetch_all_contract_filters(self, tenant_id: int) -> Dict[str, Any]:
        """Fetch all contract filters for a tenant, keyed by name."""
        try:
            return {f.name: f for f in self.aci_plugin.contract_filters.filter(aci_tenant_id=tenant_id)}
        except Exception as e:
            logger.error(f"Failed to bulk-fetch filters for tenant {tenant_id}: {e}")
            return {}

    def fetch_all_nodes(self, fabric_id: int) -> Dict[int, Any]:
        """Fetch all nodes for a fabric, keyed by node_id."""
        try:
            result = {}
            for n in self.aci_plugin.nodes.filter(aci_fabric_id=fabric_id):
                nid = getattr(n, 'node_id', None)
                if nid is not None:
                    result[int(nid)] = n
            return result
        except Exception as e:
            logger.error(f"Failed to bulk-fetch nodes for fabric {fabric_id}: {e}")
            return {}

    def fetch_all_pods(self, fabric_id: int) -> Dict[int, Any]:
        """Fetch all pods for a fabric, keyed by pod_id."""
        try:
            result = {}
            for p in self.aci_plugin.pods.filter(aci_fabric_id=fabric_id):
                pid = getattr(p, 'pod_id', None)
                if pid is not None:
                    result[int(pid)] = p
            return result
        except Exception as e:
            logger.error(f"Failed to bulk-fetch pods for fabric {fabric_id}: {e}")
            return {}

    # ── Generic CRUD Operations ───────────────────────────────────────

    def _get_or_create(self, endpoint, lookup_params: Dict,
                       create_params: Dict) -> Tuple[Any, bool]:
        """
        Get existing object or create new one.
        Returns (object, created_flag).
        """
        try:
            existing = endpoint.get(**lookup_params)
            if existing:
                return existing, False

            new_obj = endpoint.create(create_params)
            return new_obj, True
        except Exception as e:
            logger.error(f"Error in get_or_create: {e}")
            raise

    def _get_or_create_cached(self, cache: Dict, cache_key: Any,
                              endpoint, create_params: Dict) -> Tuple[Any, bool]:
        """
        Get from pre-fetched cache or create new. Avoids per-object API lookup.

        Args:
            cache: Pre-fetched dict of existing objects.
            cache_key: Key to look up in the cache.
            endpoint: pynetbox endpoint for creating.
            create_params: Parameters for creation.

        Returns:
            (object, created_flag)
        """
        if cache_key in cache:
            return cache[cache_key], False

        try:
            new_obj = endpoint.create(create_params)
            cache[cache_key] = new_obj
            return new_obj, True
        except Exception as e:
            logger.error(f"Error creating object (key={cache_key}): {e}")
            raise

    def _update_if_changed(self, obj: Any, updates: Dict,
                           verify: bool = True) -> Tuple[bool, bool]:
        """
        Update object if any attributes have changed.
        Uses values_equal for consistent comparison.
        Returns (changed, verified).
        """
        changes = {}
        for key, value in updates.items():
            current_value = getattr(obj, key, None)
            if not _values_equal(current_value, value):
                changes[key] = value

        if not changes:
            return False, True

        try:
            obj.update(changes)

            if verify:
                time.sleep(0.1)  # Brief delay for consistency
                try:
                    if hasattr(obj, 'full_details'):
                        obj.full_details()
                    elif hasattr(obj, 'refresh'):
                        obj.refresh()
                except Exception as refresh_err:
                    logger.debug(f"Could not refresh object for verification: {refresh_err}")
                    return True, False

                for key, expected in changes.items():
                    actual = getattr(obj, key, None)
                    if not _values_equal(actual, expected):
                        logger.warning(
                            f"Verification failed for {key}: expected {expected}, got {actual}"
                        )
                        return True, False
                return True, True
            return True, True
        except Exception as e:
            logger.error(f"Error updating object: {e}")
            return False, False

    # ── Fabric Operations ─────────────────────────────────────────────

    def get_or_create_fabric(self, name: str, fabric_id: int = 1, **kwargs) -> Tuple[Any, bool]:
        """Get or create an ACI Fabric."""
        try:
            existing = self.aci_plugin.fabrics.get(fabric_id=fabric_id)
            if existing:
                return existing, False

            existing = self.aci_plugin.fabrics.get(name=name)
            if existing:
                return existing, False

            create_params = {'name': name, 'fabric_id': fabric_id, **kwargs}
            new_obj = self.aci_plugin.fabrics.create(create_params)
            return new_obj, True
        except Exception as e:
            logger.error(f"Error in get_or_create_fabric: {e}")
            raise

    def update_fabric(self, fabric: Any, updates: Dict, verify: bool = True) -> Tuple[bool, bool]:
        return self._update_if_changed(fabric, updates, verify)

    def get_fabric_by_name(self, name: str) -> Optional[Any]:
        try:
            return self.aci_plugin.fabrics.get(name=name)
        except Exception:
            return None

    # ── Pod Operations ────────────────────────────────────────────────

    def get_or_create_pod(self, fabric_id: int, pod_id: int, **kwargs) -> Tuple[Any, bool]:
        name = kwargs.get('name', f"pod-{pod_id}")
        return self._get_or_create(
            self.aci_plugin.pods,
            {'aci_fabric_id': fabric_id, 'pod_id': pod_id},
            {'aci_fabric': fabric_id, 'pod_id': pod_id, 'name': name, **kwargs}
        )

    def get_or_create_pod_cached(self, cache: Dict, pod_id: int,
                                 fabric_id: int, **kwargs) -> Tuple[Any, bool]:
        """Get pod from cache or create."""
        name = kwargs.get('name', f"pod-{pod_id}")
        return self._get_or_create_cached(
            cache, pod_id,
            self.aci_plugin.pods,
            {'aci_fabric': fabric_id, 'pod_id': pod_id, 'name': name, **kwargs}
        )

    def update_pod(self, pod: Any, updates: Dict, verify: bool = True) -> Tuple[bool, bool]:
        return self._update_if_changed(pod, updates, verify)

    # ── Node Operations ───────────────────────────────────────────────

    def get_or_create_node(self, fabric_id: int, node_id: int, **kwargs) -> Tuple[Any, bool]:
        name = kwargs.get('name', f"node-{node_id}")
        try:
            existing = self.aci_plugin.nodes.get(aci_fabric_id=fabric_id, node_id=node_id)
            if existing:
                return existing, False

            existing = self.aci_plugin.nodes.get(aci_fabric_id=fabric_id, name=name)
            if existing:
                return existing, False

            all_nodes = list(self.aci_plugin.nodes.filter(aci_fabric_id=fabric_id))
            for node in all_nodes:
                if node.name == name:
                    return node, False

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

    def get_or_create_node_cached(self, cache: Dict, node_id: int,
                                  fabric_id: int, **kwargs) -> Tuple[Any, bool]:
        """Get node from cache or create."""
        name = kwargs.get('name', f"node-{node_id}")
        if node_id in cache:
            return cache[node_id], False
        try:
            create_kwargs = {k: v for k, v in kwargs.items() if k != 'name'}
            create_data = {
                'aci_fabric': fabric_id,
                'node_id': node_id,
                'name': name,
                **create_kwargs,
            }
            logger.debug(f"Creating node with data: {create_data}")
            new_obj = self.aci_plugin.nodes.create(create_data)
            cache[node_id] = new_obj
            return new_obj, True
        except Exception as e:
            logger.error(f"Error in get_or_create_node_cached: {e}")
            raise

    def update_node(self, node: Any, updates: Dict, verify: bool = True) -> Tuple[bool, bool]:
        return self._update_if_changed(node, updates, verify)

    # ── Tenant Operations ─────────────────────────────────────────────

    def get_or_create_tenant(self, fabric_id: int, name: str, **kwargs) -> Tuple[Any, bool]:
        return self._get_or_create(
            self.aci_plugin.tenants,
            {'aci_fabric_id': fabric_id, 'name': name},
            {'aci_fabric': fabric_id, 'name': name, **kwargs}
        )

    def get_or_create_tenant_cached(self, cache: Dict, name: str,
                                    fabric_id: int, **kwargs) -> Tuple[Any, bool]:
        """Get tenant from cache or create."""
        return self._get_or_create_cached(
            cache, name,
            self.aci_plugin.tenants,
            {'aci_fabric': fabric_id, 'name': name, **kwargs}
        )

    def update_tenant(self, tenant: Any, updates: Dict, verify: bool = True) -> Tuple[bool, bool]:
        return self._update_if_changed(tenant, updates, verify)

    def get_tenant_by_name(self, fabric_id: int, name: str) -> Optional[Any]:
        try:
            return self.aci_plugin.tenants.get(aci_fabric_id=fabric_id, name=name)
        except Exception:
            return None

    # ── VRF Operations ────────────────────────────────────────────────

    def get_or_create_vrf(self, tenant_id: int, name: str, **kwargs) -> Tuple[Any, bool]:
        return self._get_or_create(
            self.aci_plugin.vrfs,
            {'aci_tenant_id': tenant_id, 'name': name},
            {'aci_tenant': tenant_id, 'name': name, **kwargs}
        )

    def get_or_create_vrf_cached(self, cache: Dict, name: str,
                                 tenant_id: int, **kwargs) -> Tuple[Any, bool]:
        return self._get_or_create_cached(
            cache, name,
            self.aci_plugin.vrfs,
            {'aci_tenant': tenant_id, 'name': name, **kwargs}
        )

    def update_vrf(self, vrf: Any, updates: Dict, verify: bool = True) -> Tuple[bool, bool]:
        return self._update_if_changed(vrf, updates, verify)

    def get_vrf_by_name(self, tenant_id: int, name: str) -> Optional[Any]:
        try:
            return self.aci_plugin.vrfs.get(aci_tenant_id=tenant_id, name=name)
        except Exception:
            return None

    # ── Bridge Domain Operations ──────────────────────────────────────

    def get_or_create_bridge_domain(self, tenant_id: int, vrf_id: int,
                                     name: str, **kwargs) -> Tuple[Any, bool]:
        return self._get_or_create(
            self.aci_plugin.bridge_domains,
            {'aci_tenant_id': tenant_id, 'name': name},
            {'aci_tenant': tenant_id, 'aci_vrf': vrf_id, 'name': name, **kwargs}
        )

    def get_or_create_bd_cached(self, cache: Dict, name: str,
                                tenant_id: int, vrf_id: int, **kwargs) -> Tuple[Any, bool]:
        return self._get_or_create_cached(
            cache, name,
            self.aci_plugin.bridge_domains,
            {'aci_tenant': tenant_id, 'aci_vrf': vrf_id, 'name': name, **kwargs}
        )

    def update_bridge_domain(self, bd: Any, updates: Dict, verify: bool = True) -> Tuple[bool, bool]:
        return self._update_if_changed(bd, updates, verify)

    def get_bridge_domain_by_name(self, tenant_id: int, name: str) -> Optional[Any]:
        try:
            return self.aci_plugin.bridge_domains.get(aci_tenant_id=tenant_id, name=name)
        except Exception:
            return None

    # ── Subnet Operations ─────────────────────────────────────────────

    def get_or_create_subnet(self, bd_id: int, gateway_ip: int, **kwargs) -> Tuple[Any, bool]:
        """Get or create a BD Subnet. Looks up by gateway_ip alone (unique constraint)."""
        return self._get_or_create(
            self.aci_plugin.bridge_domain_subnets,
            {'gateway_ip_address_id': gateway_ip},
            {'aci_bridge_domain': bd_id, 'gateway_ip_address': gateway_ip, **kwargs}
        )

    def get_or_create_subnet_cached(self, cache: Dict, gateway_ip_id: int,
                                    bd_id: int, **kwargs) -> Tuple[Any, bool]:
        return self._get_or_create_cached(
            cache, gateway_ip_id,
            self.aci_plugin.bridge_domain_subnets,
            {'aci_bridge_domain': bd_id, 'gateway_ip_address': gateway_ip_id, **kwargs}
        )

    def update_subnet(self, subnet: Any, updates: Dict, verify: bool = True) -> Tuple[bool, bool]:
        return self._update_if_changed(subnet, updates, verify)

    # ── Application Profile Operations ────────────────────────────────

    def get_or_create_app_profile(self, tenant_id: int, name: str, **kwargs) -> Tuple[Any, bool]:
        return self._get_or_create(
            self.aci_plugin.app_profiles,
            {'aci_tenant_id': tenant_id, 'name': name},
            {'aci_tenant': tenant_id, 'name': name, **kwargs}
        )

    def get_or_create_ap_cached(self, cache: Dict, name: str,
                                tenant_id: int, **kwargs) -> Tuple[Any, bool]:
        return self._get_or_create_cached(
            cache, name,
            self.aci_plugin.app_profiles,
            {'aci_tenant': tenant_id, 'name': name, **kwargs}
        )

    def update_app_profile(self, ap: Any, updates: Dict, verify: bool = True) -> Tuple[bool, bool]:
        return self._update_if_changed(ap, updates, verify)

    def get_app_profile_by_name(self, tenant_id: int, name: str) -> Optional[Any]:
        try:
            return self.aci_plugin.app_profiles.get(aci_tenant_id=tenant_id, name=name)
        except Exception:
            return None

    # ── EPG Operations ────────────────────────────────────────────────

    def get_or_create_epg(self, ap_id: int, bd_id: int, name: str, **kwargs) -> Tuple[Any, bool]:
        return self._get_or_create(
            self.aci_plugin.endpoint_groups,
            {'aci_app_profile_id': ap_id, 'name': name},
            {'aci_app_profile': ap_id, 'aci_bridge_domain': bd_id, 'name': name, **kwargs}
        )

    def get_or_create_epg_cached(self, cache: Dict, name: str,
                                 ap_id: int, bd_id: int, **kwargs) -> Tuple[Any, bool]:
        return self._get_or_create_cached(
            cache, name,
            self.aci_plugin.endpoint_groups,
            {'aci_app_profile': ap_id, 'aci_bridge_domain': bd_id, 'name': name, **kwargs}
        )

    def update_epg(self, epg: Any, updates: Dict, verify: bool = True) -> Tuple[bool, bool]:
        return self._update_if_changed(epg, updates, verify)

    def get_epg_by_name(self, ap_id: int, name: str) -> Optional[Any]:
        try:
            return self.aci_plugin.endpoint_groups.get(aci_app_profile_id=ap_id, name=name)
        except Exception:
            return None

    # ── ESG Operations ────────────────────────────────────────────────

    def get_or_create_esg(self, ap_id: int, vrf_id: int, name: str, **kwargs) -> Tuple[Any, bool]:
        return self._get_or_create(
            self.aci_plugin.endpoint_security_groups,
            {'aci_app_profile_id': ap_id, 'name': name},
            {'aci_app_profile': ap_id, 'aci_vrf': vrf_id, 'name': name, **kwargs}
        )

    def get_or_create_esg_cached(self, cache: Dict, name: str,
                                 ap_id: int, vrf_id: int, **kwargs) -> Tuple[Any, bool]:
        return self._get_or_create_cached(
            cache, name,
            self.aci_plugin.endpoint_security_groups,
            {'aci_app_profile': ap_id, 'aci_vrf': vrf_id, 'name': name, **kwargs}
        )

    def update_esg(self, esg: Any, updates: Dict, verify: bool = True) -> Tuple[bool, bool]:
        return self._update_if_changed(esg, updates, verify)

    def get_esg_by_name(self, ap_id: int, name: str) -> Optional[Any]:
        try:
            return self.aci_plugin.endpoint_security_groups.get(aci_app_profile_id=ap_id, name=name)
        except Exception:
            return None

    # ── Contract Operations ───────────────────────────────────────────

    def get_or_create_contract(self, tenant_id: int, name: str, **kwargs) -> Tuple[Any, bool]:
        return self._get_or_create(
            self.aci_plugin.contracts,
            {'aci_tenant_id': tenant_id, 'name': name},
            {'aci_tenant': tenant_id, 'name': name, **kwargs}
        )

    def get_or_create_contract_cached(self, cache: Dict, name: str,
                                      tenant_id: int, **kwargs) -> Tuple[Any, bool]:
        return self._get_or_create_cached(
            cache, name,
            self.aci_plugin.contracts,
            {'aci_tenant': tenant_id, 'name': name, **kwargs}
        )

    def update_contract(self, contract: Any, updates: Dict, verify: bool = True) -> Tuple[bool, bool]:
        return self._update_if_changed(contract, updates, verify)

    def get_contract_by_name(self, tenant_id: int, name: str) -> Optional[Any]:
        try:
            return self.aci_plugin.contracts.get(aci_tenant_id=tenant_id, name=name)
        except Exception:
            return None

    # ── Contract Filter Operations ────────────────────────────────────

    def get_or_create_contract_filter(self, tenant_id: int, name: str, **kwargs) -> Tuple[Any, bool]:
        return self._get_or_create(
            self.aci_plugin.contract_filters,
            {'aci_tenant_id': tenant_id, 'name': name},
            {'aci_tenant': tenant_id, 'name': name, **kwargs}
        )

    def get_or_create_filter_cached(self, cache: Dict, name: str,
                                    tenant_id: int, **kwargs) -> Tuple[Any, bool]:
        return self._get_or_create_cached(
            cache, name,
            self.aci_plugin.contract_filters,
            {'aci_tenant': tenant_id, 'name': name, **kwargs}
        )

    def update_contract_filter(self, flt: Any, updates: Dict, verify: bool = True) -> Tuple[bool, bool]:
        return self._update_if_changed(flt, updates, verify)

    # ── Contract Subject Operations ───────────────────────────────────

    def get_or_create_contract_subject(self, contract_id: int, name: str, **kwargs) -> Tuple[Any, bool]:
        return self._get_or_create(
            self.aci_plugin.contract_subjects,
            {'aci_contract_id': contract_id, 'name': name},
            {'aci_contract': contract_id, 'name': name, **kwargs}
        )

    def update_contract_subject(self, subject: Any, updates: Dict, verify: bool = True) -> Tuple[bool, bool]:
        return self._update_if_changed(subject, updates, verify)

    # ── Contract Filter Entry Operations ──────────────────────────────

    def get_or_create_filter_entry(self, filter_id: int, name: str, **kwargs) -> Tuple[Any, bool]:
        return self._get_or_create(
            self.aci_plugin.contract_filter_entries,
            {'aci_contract_filter_id': filter_id, 'name': name},
            {'aci_contract_filter': filter_id, 'name': name, **kwargs}
        )

    def update_filter_entry(self, entry: Any, updates: Dict, verify: bool = True) -> Tuple[bool, bool]:
        return self._update_if_changed(entry, updates, verify)

    # ── Contract Relations (cached) ───────────────────────────────────

    def _fetch_contract_relations(self) -> List[Dict]:
        """Fetch all contract relations once and cache them."""
        if self._contract_relations_cache is not None:
            return self._contract_relations_cache

        try:
            url = f"{self.url}/api/plugins/aci/contract-relations/"
            response = self.http_session.get(url)
            if response.status_code != 200:
                logger.warning(f"Failed to fetch contract relations: {response.status_code}")
                self._contract_relations_cache = []
                return []

            data = response.json()
            results = data.get('results', data if isinstance(data, list) else [])
            self._contract_relations_cache = results
            logger.debug(f"Cached {len(results)} contract relations")
            return results
        except Exception as e:
            logger.warning(f"Error fetching contract relations: {e}")
            self._contract_relations_cache = []
            return []

    def invalidate_contract_relations_cache(self) -> None:
        """Clear the contract relations cache (e.g., after creating new ones)."""
        self._contract_relations_cache = None

    def _relation_exists(self, object_id: int, contract_id: int,
                         role: str, object_type_filter: Optional[str] = None) -> bool:
        """Check if a contract relation already exists in the cache."""
        relations = self._fetch_contract_relations()
        for rel in relations:
            rel_object_id = rel.get('aci_object_id')
            rel_contract = rel.get('aci_contract')
            rel_contract_id = (
                rel_contract.get('id') if isinstance(rel_contract, dict)
                else rel_contract
            )
            rel_role = rel.get('role')

            if rel_object_id != object_id or rel_contract_id != contract_id or rel_role != role:
                continue

            if object_type_filter:
                rel_type = rel.get('aci_object_type', '')
                if object_type_filter not in rel_type.lower():
                    continue

            return True
        return False

    def create_contract_relation(self, contract_id: int, epg_id: int,
                                 role: str, tenant_id: int = None,
                                 fabric_id: int = None) -> bool:
        """Create a contract relation (EPG as provider/consumer)."""
        try:
            if self._relation_exists(epg_id, contract_id, role):
                logger.debug(
                    f"Contract relation already exists: epg={epg_id}, "
                    f"contract={contract_id}, role={role}"
                )
                return False

            post_data = {
                'aci_contract': contract_id,
                'aci_object_type': 'netbox_aci_plugin.aciendpointgroup',
                'aci_object_id': epg_id,
                'role': role,
            }
            if tenant_id:
                post_data['aci_tenant'] = tenant_id
            if fabric_id:
                post_data['aci_fabric'] = fabric_id

            url = f"{self.url}/api/plugins/aci/contract-relations/"
            logger.info(
                f"Creating contract relation: epg={epg_id}, "
                f"contract={contract_id}, role={role}"
            )
            response = self.http_session.post(url, json=post_data)

            if response.status_code in (200, 201):
                logger.info("Successfully created contract relation")
                # Invalidate cache so next lookup sees the new relation
                self.invalidate_contract_relations_cache()
                return True
            elif response.status_code == 500:
                logger.debug(f"Contract relation creation failed with 500: {response.text[:100]}")
                return False
            else:
                logger.warning(
                    f"Failed to create contract relation: "
                    f"{response.status_code} - {response.text[:200]}"
                )
                return False

        except Exception as e:
            logger.warning(f"Error creating contract relation: {e}")
            return False

    def create_vrf_contract_relation(self, vrf_id: int, contract_id: int,
                                     role: str, tenant_id: int = None) -> bool:
        """Create a VRF contract relation for vzAny."""
        try:
            if self._relation_exists(vrf_id, contract_id, role, object_type_filter='vrf'):
                logger.debug(
                    f"VRF contract relation already exists: vrf={vrf_id}, "
                    f"contract={contract_id}, role={role}"
                )
                return False

            post_data = {
                'aci_contract': contract_id,
                'aci_object_type': 'netbox_aci_plugin.acivrf',
                'aci_object_id': vrf_id,
                'role': role,
            }
            if tenant_id:
                post_data['aci_tenant'] = tenant_id

            url = f"{self.url}/api/plugins/aci/contract-relations/"
            logger.info(
                f"Creating VRF contract relation: vrf={vrf_id}, "
                f"contract={contract_id}, role={role}"
            )
            response = self.http_session.post(url, json=post_data)

            if response.status_code in (200, 201):
                logger.info("Successfully created VRF contract relation")
                self.invalidate_contract_relations_cache()
                return True
            else:
                logger.warning(
                    f"Failed to create VRF contract relation: "
                    f"{response.status_code} - {response.text[:200]}"
                )
                return False

        except Exception as e:
            logger.warning(f"Error creating VRF contract relation: {e}")
            return False

    # ── DCIM Device Operations ────────────────────────────────────────

    def get_or_create_dcim_device(self, name: str, device_type_id: int,
                                   site_id: int, role_id: int, **kwargs) -> Tuple[Any, bool]:
        return self._get_or_create(
            self.api.dcim.devices,
            {'name': name},
            {'name': name, 'device_type': device_type_id, 'site': site_id,
             'role': role_id, **kwargs}
        )

    def get_or_create_device_type(self, manufacturer_id: int, model: str, **kwargs) -> Tuple[Any, bool]:
        slug = model.lower().replace(' ', '-').replace('/', '-')[:50]
        return self._get_or_create(
            self.api.dcim.device_types,
            {'model': model},
            {'manufacturer': manufacturer_id, 'model': model, 'slug': slug, **kwargs}
        )

    def fetch_all_device_types(self, manufacturer_id: int) -> List[Any]:
        """Fetch all device types for a manufacturer."""
        try:
            return list(self.api.dcim.device_types.filter(manufacturer_id=manufacturer_id))
        except Exception as e:
            logger.error(f"Failed to fetch device types for manufacturer {manufacturer_id}: {e}")
            return []

    def get_or_create_manufacturer(self, name: str) -> Tuple[Any, bool]:
        slug = name.lower().replace(' ', '-')[:50]
        return self._get_or_create(
            self.api.dcim.manufacturers,
            {'name': name},
            {'name': name, 'slug': slug}
        )

    def get_or_create_site(self, name: str) -> Tuple[Any, bool]:
        slug = name.lower().replace(' ', '-')[:50]
        return self._get_or_create(
            self.api.dcim.sites,
            {'name': name},
            {'name': name, 'slug': slug}
        )

    def get_or_create_device_role(self, name: str) -> Tuple[Any, bool]:
        slug = name.lower().replace(' ', '-')[:50]
        return self._get_or_create(
            self.api.dcim.device_roles,
            {'name': name},
            {'name': name, 'slug': slug}
        )

    # ── IP Address Management ─────────────────────────────────────────

    def get_or_create_ip_address(self, address: str, **kwargs) -> Tuple[Any, bool]:
        """Get or create a NetBox IP Address."""
        ip_only = address.split('/')[0] if '/' in address else address
        try:
            existing = list(self.api.ipam.ip_addresses.filter(address=ip_only))
            if existing:
                return existing[0], False
        except Exception as e:
            logger.debug(f"Error searching for IP {ip_only}: {e}")

        return self._get_or_create(
            self.api.ipam.ip_addresses,
            {'address': address},
            {'address': address, **kwargs}
        )

    def get_or_create_prefix(self, prefix: str, **kwargs) -> Tuple[Any, bool]:
        return self._get_or_create(
            self.api.ipam.prefixes,
            {'prefix': prefix},
            {'prefix': prefix, **kwargs}
        )

    def get_ip_address(self, address: str) -> Optional[Any]:
        try:
            return self.api.ipam.ip_addresses.get(address=address)
        except Exception:
            return None

    # ── Bulk Operations ───────────────────────────────────────────────

    def bulk_create(self, endpoint, objects: List[Dict]) -> List[Any]:
        """Create multiple objects at once."""
        try:
            return endpoint.create(objects)
        except Exception as e:
            logger.error(f"Bulk create failed: {e}")
            return []

    def clear_cache(self) -> None:
        """Clear all cached data."""
        self._contract_relations_cache = None