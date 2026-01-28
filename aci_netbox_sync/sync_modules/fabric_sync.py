"""
Fabric Sync Module - Synchronize ACI Fabric, Pods, and Nodes.
Includes fabric details: fabric_id, infra_vlan_id, gipo_pool (new in 0.2.0).
"""

import logging
from typing import Any, Dict, List, Optional

from .base import BaseSyncModule, SyncResult, values_equal

logger = logging.getLogger(__name__)


class FabricSyncModule(BaseSyncModule):
    """Sync ACI Fabric settings to NetBox."""

    @property
    def object_type(self) -> str:
        return "Fabric"

    def fetch_from_aci(self) -> List[Dict[str, Any]]:
        """Fetch fabric settings from ACI."""
        fabric_data = self.aci.get_fabric_settings()
        return [fabric_data] if fabric_data else []

    def sync_object(self, aci_data: Dict[str, Any]) -> bool:
        """Sync fabric to NetBox."""
        try:
            # Get fabric name and sanitize (replace spaces/special chars)
            raw_name = aci_data.get('name') or 'ACI_Fabric'
            # Replace spaces and special characters with underscores
            fabric_name = raw_name.replace(' ', '_').replace('/', '_').replace('\\', '_')
            
            # Get required fields with defaults
            fabric_id = aci_data.get('fabric_id') or 1
            infra_vlan_id = aci_data.get('infra_vlan_id') or 4093  # Default ACI infra VLAN
            
            # Prepare fabric data including required and optional fields
            fabric_params = {
                'name': fabric_name,
                'fabric_id': fabric_id,
                'infra_vlan_vid': infra_vlan_id,  # NetBox field name is infra_vlan_vid
            }
            
            # Add GIPO pool if available (new in 0.2.0)
            if aci_data.get('gipo_pool'):
                fabric_params['gipo_pool'] = aci_data['gipo_pool']

            # Get or create fabric with all required params
            fabric, created = self.netbox.get_or_create_fabric(
                fabric_name, 
                fabric_id=fabric_id,
                infra_vlan_vid=infra_vlan_id
            )
            
            if created:
                self.result.created += 1
                logger.info(f"Created fabric: {fabric_name}")
            else:
                # Update existing fabric
                updates = {}
                if fabric_id and getattr(fabric, 'fabric_id', None) != fabric_id:
                    updates['fabric_id'] = fabric_id
                if infra_vlan_id and getattr(fabric, 'infra_vlan_vid', None) != infra_vlan_id:
                    updates['infra_vlan_vid'] = infra_vlan_id
                if aci_data.get('gipo_pool') and getattr(fabric, 'gipo_pool', None) != aci_data.get('gipo_pool'):
                    updates['gipo_pool'] = aci_data['gipo_pool']
                
                if updates:
                    changed, verified = self.netbox.update_fabric(
                        fabric, updates, self.settings.verify_updates
                    )
                    if changed:
                        self.result.updated += 1
                        if verified:
                            self.result.verified += 1
                        logger.info(f"Updated fabric: {fabric_name}")
                    else:
                        self.result.unchanged += 1
                else:
                    self.result.unchanged += 1

            # Store fabric ID in context for other modules
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

    def fetch_from_aci(self) -> List[Dict[str, Any]]:
        """Fetch pods from ACI."""
        return self.aci.get_fabric_pods()

    def sync_object(self, aci_data: Dict[str, Any]) -> bool:
        """Sync pod to NetBox."""
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

            # Prepare pod parameters - tep_pool needs to be a Prefix ID
            pod_params = {}
            tep_pool_id = None
            tep_pool_mask = '16'  # Default mask
            if aci_data.get('tep_pool'):
                # Extract mask from TEP pool (e.g., "10.0.0.0/16" -> "16")
                tep_pool_str = aci_data['tep_pool']
                if '/' in tep_pool_str:
                    tep_pool_mask = tep_pool_str.split('/')[1]
                
                # Create or get the prefix in NetBox IPAM
                tep_pool_obj, _ = self.netbox.get_or_create_prefix(
                    prefix=aci_data['tep_pool'],
                    description=f"TEP Pool - {pod_name}"
                )
                tep_pool_id = tep_pool_obj.id
                pod_params['tep_pool'] = tep_pool_id
                
                # Store TEP pool mask in context for Node sync to use
                self.context['tep_pool_mask'] = tep_pool_mask

            # Get or create pod
            pod, created = self.netbox.get_or_create_pod(
                fabric_id=fabric_id,
                pod_id=pod_id,
                name=pod_name,
                **pod_params
            )

            if created:
                self.result.created += 1
                logger.info(f"Created pod: {pod_name}")
            else:
                # Check for updates - compare by ID since it's a foreign key
                updates = {}
                if tep_pool_id:
                    current = getattr(pod, 'tep_pool', None)
                    current_id = None
                    if hasattr(current, 'id'):
                        current_id = current.id
                    elif current:
                        current_id = current
                    
                    if current_id != tep_pool_id:
                        logger.debug(f"Pod {pod_name} tep_pool differs: current_id={current_id}, new_id={tep_pool_id}")
                        updates['tep_pool'] = tep_pool_id
                
                if updates:
                    changed, verified = self.netbox.update_pod(
                        pod, updates, self.settings.verify_updates
                    )
                    if changed:
                        self.result.updated += 1
                        if verified:
                            self.result.verified += 1
                        logger.info(f"Updated pod: {pod_name}")
                    else:
                        self.result.unchanged += 1
                else:
                    self.result.unchanged += 1

            # Store pod mapping in context
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

    @property
    def object_type(self) -> str:
        return "Node"

    def fetch_from_aci(self) -> List[Dict[str, Any]]:
        """Fetch nodes from ACI."""
        return self.aci.get_fabric_nodes()

    def sync_object(self, aci_data: Dict[str, Any]) -> bool:
        """Sync node to NetBox."""
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

            # Get pod reference - required field
            pod_id = aci_data.get('pod_id', 1)  # Default to pod 1
            pod_map = self.context.get('pod_map', {})
            aci_pod_id = pod_map.get(pod_id)
            
            if not aci_pod_id:
                logger.warning(f"Pod {pod_id} not found in context for node {node_name}, skipping")
                return False

            # Map ACI role to NetBox valid choices
            # NetBox valid choices are typically: spine, leaf, apic (not 'controller')
            role_mapping = {
                'controller': 'apic',
                'spine': 'spine',
                'leaf': 'leaf',
                'unspecified': 'leaf',  # Default unknown to leaf
            }
            aci_role = aci_data.get('role', 'leaf').lower()
            node_role = role_mapping.get(aci_role, 'leaf')

            # Create DCIM device first to link to ACI node
            # This requires: manufacturer, device_type, site, device_role
            model = aci_data.get('model') or f"ACI-{node_role.upper()}"
            
            # Get or create Cisco manufacturer
            manufacturer, _ = self.netbox.get_or_create_manufacturer("Cisco")
            
            # Get or create device type
            device_type, _ = self.netbox.get_or_create_device_type(
                manufacturer_id=manufacturer.id,
                model=model
            )
            
            # Get or create site (use fabric name or default)
            fabric_name = self.context.get('fabric_name', 'ACI-Fabric')
            site, _ = self.netbox.get_or_create_site(fabric_name)
            
            # Get or create device role based on ACI role
            role_name = f"ACI {node_role.title()}"
            device_role, _ = self.netbox.get_or_create_device_role(role_name)
            
            # Create the DCIM device
            device_params = {}
            if aci_data.get('serial'):
                device_params['serial'] = aci_data['serial']
            
            dcim_device, device_created = self.netbox.get_or_create_dcim_device(
                name=node_name,
                device_type_id=device_type.id,
                site_id=site.id,
                role_id=device_role.id,
                **device_params
            )
            
            if device_created:
                logger.debug(f"Created DCIM device: {node_name}")

            # Now prepare ACI node parameters with device link
            node_params = {
                'name': node_name,
                'aci_pod': aci_pod_id,  # Required field
                'role': node_role,
                'node_type': 'virtual',  # Use 'virtual' for simulators, 'physical' for real hardware
                'node_object_type': 'dcim.device',
                'node_object_id': dcim_device.id,
            }
            
            # Handle TEP IP address - needs to be created in IPAM first
            # Must use same mask length as the Pod's TEP pool (typically /16)
            tep_address = aci_data.get('address')
            tep_ip_id = None
            if tep_address and tep_address != '0.0.0.0':
                # Get the TEP pool mask from context (set by Pod sync)
                tep_pool_mask = self.context.get('tep_pool_mask', '16')
                
                # Create IP with correct mask length to match TEP pool
                tep_ip_only = tep_address.split('/')[0] if '/' in tep_address else tep_address
                tep_address_with_mask = f"{tep_ip_only}/{tep_pool_mask}"
                
                try:
                    # Create or get the IP address in NetBox IPAM
                    tep_ip_obj, _ = self.netbox.get_or_create_ip_address(
                        address=tep_address_with_mask,
                        description=f"TEP IP - {node_name}"
                    )
                    tep_ip_id = tep_ip_obj.id
                    node_params['tep_ip_address'] = tep_ip_id
                except Exception as e:
                    # IP might already exist with different mask, try to find it
                    logger.warning(f"Could not create TEP IP {tep_address_with_mask}: {e}")
                    try:
                        # Try to find existing IP with any mask
                        existing_ip = self.netbox.api.ipam.ip_addresses.get(address=tep_ip_only)
                        if existing_ip:
                            tep_ip_id = existing_ip.id
                            node_params['tep_ip_address'] = tep_ip_id
                            logger.debug(f"Found existing TEP IP: {existing_ip.address}")
                    except Exception as e2:
                        logger.debug(f"Could not find existing TEP IP: {e2}")

            # Get or create node
            node, created = self.netbox.get_or_create_node(
                fabric_id=fabric_id,
                node_id=node_id,
                **node_params
            )

            if created:
                self.result.created += 1
                logger.info(f"Created node: {node_name} (ID: {node_id})")
            else:
                # Update existing node - only fields that NetBox ACI plugin supports
                updates = {}
                
                # Check role
                current_role = getattr(node, 'role', None)
                current_role_str = str(current_role) if current_role else ''
                if current_role_str != node_role:
                    updates['role'] = node_role
                
                # Check tep_ip_address - compare by ID since it's a foreign key
                if tep_ip_id:
                    current_tep = getattr(node, 'tep_ip_address', None)
                    current_tep_id = None
                    if hasattr(current_tep, 'id'):
                        current_tep_id = current_tep.id
                    elif current_tep:
                        current_tep_id = current_tep
                    
                    if current_tep_id != tep_ip_id:
                        logger.debug(f"Node {node_name} tep_ip_address differs: current_id={current_tep_id}, new_id={tep_ip_id}")
                        updates['tep_ip_address'] = tep_ip_id

                if updates:
                    changed, verified = self.netbox.update_node(
                        node, updates, self.settings.verify_updates
                    )
                    if changed:
                        self.result.updated += 1
                        if verified:
                            self.result.verified += 1
                        logger.info(f"Updated node: {node_name}")
                    else:
                        self.result.unchanged += 1
                else:
                    self.result.unchanged += 1

            # Store node mapping in context
            node_map = self.context.setdefault('node_map', {})
            node_map[node_id] = node.id

            return True

        except Exception as e:
            logger.error(f"Failed to sync node {aci_data.get('name')}: {e}")
            self.result.failed += 1
            self.result.errors.append(str(e))
            return False
