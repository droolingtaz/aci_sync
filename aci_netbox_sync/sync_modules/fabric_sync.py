"""
Fabric Sync Module - Synchronize ACI Fabric, Pods, and Nodes.
Includes fabric details: fabric_id, infra_vlan_id, gipo_pool (new in 0.2.0).

Optimized with:
- Pre-fetch caching for pods and nodes
- _build_updates for DRY field comparison
"""

import logging
from typing import Any, Dict, List

from .base import BaseSyncModule, values_equal

logger = logging.getLogger(__name__)


class FabricSyncModule(BaseSyncModule):
    """Sync ACI Fabric settings to NetBox."""

    @property
    def object_type(self) -> str:
        return "Fabric"

    def fetch_from_aci(self) -> List[Dict[str, Any]]:
        fabric_data = self.aci.get_fabric_settings()
        return [fabric_data] if fabric_data else []

    def sync_object(self, aci_data: Dict[str, Any]) -> bool:
        try:
            raw_name = aci_data.get('name') or 'ACI_Fabric'
            fabric_name = raw_name.replace(' ', '_').replace('/', '_').replace('\\', '_')

            fabric_id = aci_data.get('fabric_id') or 1
            infra_vlan_id = aci_data.get('infra_vlan_id') or 4093

            fabric, created = self.netbox.get_or_create_fabric(
                fabric_name,
                fabric_id=fabric_id,
                infra_vlan_vid=infra_vlan_id,
            )

            if created:
                self.result.created += 1
                logger.info(f"Created fabric: {fabric_name}")
            else:
                updates = {}
                if fabric_id and getattr(fabric, 'fabric_id', None) != fabric_id:
                    updates['fabric_id'] = fabric_id
                if infra_vlan_id and getattr(fabric, 'infra_vlan_vid', None) != infra_vlan_id:
                    updates['infra_vlan_vid'] = infra_vlan_id
                if aci_data.get('gipo_pool') and getattr(fabric, 'gipo_pool', None) != aci_data['gipo_pool']:
                    updates['gipo_pool'] = aci_data['gipo_pool']

                self._apply_updates(fabric, updates, fabric_name, self.netbox.update_fabric)

            self.context['fabric_id'] = fabric.id
            self.context['fabric_name'] = fabric_name
            return True

        except Exception as e:
            logger.error(f"Failed to sync fabric: {e}")
            self.result.failed += 1
            self.result.errors.append(str(e))
            return False


class PodSyncModule(BaseSyncModule):
    """Sync ACI Fabric Pods to NetBox."""

    @property
    def object_type(self) -> str:
        return "Pod"

    def pre_sync(self) -> None:
        """Pre-fetch existing pods."""
        fabric_id = self.context.get('fabric_id')
        if fabric_id:
            self._existing_cache = self.netbox.fetch_all_pods(fabric_id)
            logger.debug(f"Pre-fetched {len(self._existing_cache)} existing pods")

    def fetch_from_aci(self) -> List[Dict[str, Any]]:
        return self.aci.get_fabric_pods()

    def sync_object(self, aci_data: Dict[str, Any]) -> bool:
        try:
            fabric_id = self.context.get('fabric_id')
            if not fabric_id:
                logger.error("Fabric ID not found in context")
                return False

            pod_id = aci_data.get('pod_id')
            pod_name = aci_data.get('name') or f"pod-{pod_id}"
            if not pod_id:
                logger.warning(f"Skipping pod without ID: {aci_data}")
                return False

            pod_params = {}
            tep_pool_id = None
            tep_pool_mask = '16'
            if aci_data.get('tep_pool'):
                tep_pool_str = aci_data['tep_pool']
                if '/' in tep_pool_str:
                    tep_pool_mask = tep_pool_str.split('/')[1]

                tep_pool_obj, _ = self.netbox.get_or_create_prefix(
                    prefix=aci_data['tep_pool'],
                    description=f"TEP Pool - {pod_name}",
                )
                tep_pool_id = tep_pool_obj.id
                pod_params['tep_pool'] = tep_pool_id
                self.context['tep_pool_mask'] = tep_pool_mask

            # Use cached lookup
            pod, created = self.netbox.get_or_create_pod_cached(
                self._existing_cache, pod_id,
                fabric_id=fabric_id, name=pod_name, **pod_params
            )

            if created:
                self.result.created += 1
                logger.info(f"Created pod: {pod_name}")
            else:
                updates = {}
                if tep_pool_id:
                    current = getattr(pod, 'tep_pool', None)
                    current_id = current.id if hasattr(current, 'id') else current
                    if current_id != tep_pool_id:
                        updates['tep_pool'] = tep_pool_id

                self._apply_updates(pod, updates, pod_name, self.netbox.update_pod)

            pod_map = self.context.setdefault('pod_map', {})
            pod_map[pod_id] = pod.id
            return True

        except Exception as e:
            logger.error(f"Failed to sync pod: {e}")
            self.result.failed += 1
            self.result.errors.append(str(e))
            return False


class NodeSyncModule(BaseSyncModule):
    """Sync ACI Fabric Nodes to NetBox (new in 0.2.0)."""

    ROLE_MAPPING = {
        'controller': 'apic',
        'spine': 'spine',
        'leaf': 'leaf',
        'unspecified': 'leaf',
    }

    @property
    def object_type(self) -> str:
        return "Node"

    @staticmethod
    def _normalize_model(model: str) -> str:
        """
        Normalize an ACI/Cisco model string to a canonical form for matching.

        Strips common prefixes and suffixes so that ACI-reported names like
        'N9K-C9508' can match NetBox entries like 'Nexus 9508'.

        Examples:
            N9K-C9508         -> 9508
            Nexus 9508        -> 9508
            N9K-C93180YC-FX   -> 93180ycfx
            Nexus 93180YC-FX  -> 93180ycfx
            APIC-SERVER-M3    -> m3
            APIC-M3           -> m3
            ACI-LEAF          -> leaf
        """
        s = model.strip()

        # Strip common Cisco/ACI prefixes (order matters — longest first)
        for prefix in ('N9K-C', 'N9K-', 'N5K-C', 'N5K-', 'N3K-C', 'N3K-',
                       'N77-C', 'N77-', 'N7K-C', 'N7K-',
                       'APIC-SERVER-', 'APIC-',
                       'Nexus ', 'nexus ',
                       'ACI-'):
            if s.startswith(prefix) or s.lower().startswith(prefix.lower()):
                s = s[len(prefix):]
                break

        # Lowercase, strip hyphens/spaces/underscores for comparison
        return s.lower().replace('-', '').replace(' ', '').replace('_', '')

    def pre_sync(self) -> None:
        """Pre-fetch existing nodes and cache DCIM helper objects."""
        fabric_id = self.context.get('fabric_id')
        if fabric_id:
            self._existing_cache = self.netbox.fetch_all_nodes(fabric_id)
            logger.debug(f"Pre-fetched {len(self._existing_cache)} existing nodes")

        # Cache Cisco manufacturer and site once (instead of per-node)
        self._manufacturer, _ = self.netbox.get_or_create_manufacturer("Cisco")
        fabric_name = self.context.get('fabric_name', 'ACI-Fabric')
        self._site, _ = self.netbox.get_or_create_site(fabric_name)

        # Pre-fetch all existing Cisco device types and build normalized index
        self._device_type_cache: Dict[str, Any] = {}
        self._device_role_cache: Dict[str, Any] = {}

        existing_types = self.netbox.fetch_all_device_types(self._manufacturer.id)
        self._normalized_device_types: Dict[str, Any] = {}
        for dt in existing_types:
            model_str = getattr(dt, 'model', '') or ''
            norm = self._normalize_model(model_str)
            if norm:
                self._normalized_device_types[norm] = dt
                # Also cache by exact model so later lookups hit the cache
                self._device_type_cache[model_str] = dt
        logger.debug(
            f"Pre-fetched {len(existing_types)} Cisco device types, "
            f"{len(self._normalized_device_types)} normalized entries"
        )

    def _get_device_type(self, model: str) -> Any:
        """
        Get device type, matching against existing NetBox entries first.

        Lookup order:
        1. Exact match in cache (already seen this run)
        2. Normalized match against pre-fetched Cisco device types
        3. Create new device type if no match found
        """
        # 1. Exact cache hit
        if model in self._device_type_cache:
            return self._device_type_cache[model]

        # 2. Normalized match against existing NetBox device types
        norm = self._normalize_model(model)
        if norm and norm in self._normalized_device_types:
            dt = self._normalized_device_types[norm]
            existing_model = getattr(dt, 'model', model)
            logger.info(
                f"Matched ACI model '{model}' to existing NetBox device type '{existing_model}'"
            )
            self._device_type_cache[model] = dt
            return dt

        # 3. No match — create new device type
        logger.debug(f"No existing device type match for '{model}', creating new")
        dt, _ = self.netbox.get_or_create_device_type(
            manufacturer_id=self._manufacturer.id, model=model
        )
        self._device_type_cache[model] = dt
        # Also register in normalized index so future ACI models can match
        if norm:
            self._normalized_device_types[norm] = dt
        return dt

    def _get_device_role(self, role_name: str) -> Any:
        """Get or create device role with caching."""
        if role_name not in self._device_role_cache:
            dr, _ = self.netbox.get_or_create_device_role(role_name)
            self._device_role_cache[role_name] = dr
        return self._device_role_cache[role_name]

    def fetch_from_aci(self) -> List[Dict[str, Any]]:
        return self.aci.get_fabric_nodes()

    def sync_object(self, aci_data: Dict[str, Any]) -> bool:
        try:
            fabric_id = self.context.get('fabric_id')
            if not fabric_id:
                logger.error("Fabric ID not found in context")
                return False

            node_id = aci_data.get('node_id')
            node_name = aci_data.get('name') or f"node-{node_id}"
            if not node_id:
                logger.warning(f"Skipping node without ID: {aci_data}")
                return False

            pod_id = aci_data.get('pod_id', 1)
            pod_map = self.context.get('pod_map', {})
            aci_pod_id = pod_map.get(pod_id)
            if not aci_pod_id:
                logger.warning(f"Pod {pod_id} not found for node {node_name}, skipping")
                return False

            aci_role = aci_data.get('role', 'leaf').lower()
            node_role = self.ROLE_MAPPING.get(aci_role, 'leaf')
            model = aci_data.get('model') or f"ACI-{node_role.upper()}"

            # Use cached DCIM helpers
            device_type = self._get_device_type(model)
            role_name = f"ACI {node_role.title()}"
            device_role = self._get_device_role(role_name)

            device_params = {}
            if aci_data.get('serial'):
                device_params['serial'] = aci_data['serial']

            dcim_device, device_created = self.netbox.get_or_create_dcim_device(
                name=node_name,
                device_type_id=device_type.id,
                site_id=self._site.id,
                role_id=device_role.id,
                **device_params,
            )
            if device_created:
                logger.debug(f"Created DCIM device: {node_name}")

            node_params = {
                'name': node_name,
                'aci_pod': aci_pod_id,
                'role': node_role,
                'node_type': 'virtual',
                'node_object_type': 'dcim.device',
                'node_object_id': dcim_device.id,
            }

            # Handle TEP IP
            tep_address = aci_data.get('address')
            tep_ip_id = None
            if tep_address and tep_address != '0.0.0.0':
                tep_pool_mask = self.context.get('tep_pool_mask', '16')
                tep_ip_only = tep_address.split('/')[0] if '/' in tep_address else tep_address
                tep_address_with_mask = f"{tep_ip_only}/{tep_pool_mask}"

                try:
                    tep_ip_obj, _ = self.netbox.get_or_create_ip_address(
                        address=tep_address_with_mask,
                        description=f"TEP IP - {node_name}",
                    )
                    tep_ip_id = tep_ip_obj.id
                    node_params['tep_ip_address'] = tep_ip_id
                except Exception as e:
                    logger.warning(f"Could not create TEP IP {tep_address_with_mask}: {e}")
                    try:
                        existing_ip = self.netbox.api.ipam.ip_addresses.get(address=tep_ip_only)
                        if existing_ip:
                            tep_ip_id = existing_ip.id
                            node_params['tep_ip_address'] = tep_ip_id
                    except Exception as e2:
                        logger.debug(f"Could not find existing TEP IP: {e2}")

            # Use cached lookup
            node, created = self.netbox.get_or_create_node_cached(
                self._existing_cache, node_id,
                fabric_id=fabric_id, **node_params
            )

            if created:
                self.result.created += 1
                logger.info(f"Created node: {node_name} (ID: {node_id})")
            else:
                updates = {}
                current_role = getattr(node, 'role', None)
                if str(current_role) if current_role else '' != node_role:
                    updates['role'] = node_role

                if tep_ip_id:
                    current_tep = getattr(node, 'tep_ip_address', None)
                    current_tep_id = current_tep.id if hasattr(current_tep, 'id') else current_tep
                    if current_tep_id != tep_ip_id:
                        updates['tep_ip_address'] = tep_ip_id

                self._apply_updates(node, updates, node_name, self.netbox.update_node)

            node_map = self.context.setdefault('node_map', {})
            node_map[node_id] = node.id
            return True

        except Exception as e:
            logger.error(f"Failed to sync node {aci_data.get('name')}: {e}")
            self.result.failed += 1
            self.result.errors.append(str(e))
            return False