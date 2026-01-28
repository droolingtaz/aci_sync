"""
Bridge Domain Sync Module - Synchronize ACI Bridge Domains and Subnets to NetBox.
"""

import logging
from typing import Any, Dict, List, Optional

from .base import BaseSyncModule, values_equal

logger = logging.getLogger(__name__)


class BridgeDomainSyncModule(BaseSyncModule):
    """Sync ACI Bridge Domains to NetBox."""

    @property
    def object_type(self) -> str:
        return "BridgeDomain"

    def fetch_from_aci(self) -> List[Dict[str, Any]]:
        """Fetch Bridge Domains from ACI."""
        return self.aci.get_bridge_domains()

    def sync_object(self, aci_data: Dict[str, Any]) -> bool:
        """Sync Bridge Domain to NetBox."""
        try:
            tenant_name = aci_data.get('tenant')
            if not tenant_name:
                logger.warning(f"Skipping BD without tenant: {aci_data}")
                return False

            tenant_map = self.context.get('tenant_map', {})
            tenant_id = tenant_map.get(tenant_name)
            
            if not tenant_id:
                logger.warning(f"Tenant {tenant_name} not found for BD {aci_data.get('name')}")
                return False

            vrf_name = aci_data.get('vrf')
            vrf_map = self.context.get('vrf_map', {})
            vrf_id = vrf_map.get(f"{tenant_name}/{vrf_name}") if vrf_name else None
            
            # NetBox ACI plugin requires VRF - skip BDs without VRF assignment
            if not vrf_id:
                bd_name = aci_data.get('name')
                if vrf_name:
                    logger.warning(f"VRF {vrf_name} not found for BD {tenant_name}/{bd_name} - skipping")
                else:
                    logger.warning(f"BD {tenant_name}/{bd_name} has no VRF assigned - NetBox ACI plugin requires VRF")
                return False

            bd_name = aci_data.get('name')
            if not bd_name:
                logger.warning(f"Skipping BD without name: {aci_data}")
                return False

            # Prepare BD parameters
            bd_params = {}
            
            if aci_data.get('name_alias'):
                bd_params['name_alias'] = aci_data['name_alias']
            if aci_data.get('description'):
                bd_params['description'] = aci_data['description']
            
            # BD-specific attributes mapping
            bd_field_mapping = {
                'arp_flood': 'arp_flooding_enabled',
                'ip_learning': 'ip_data_plane_learning_enabled',
                'limit_ip_learn': 'limit_ip_learn_enabled',
                'mac': 'mac_address',
                'multi_dest_pkt_act': 'multi_destination_flooding',
                'unicast_route': 'unicast_routing_enabled',
                'unk_mac_ucast_act': 'unknown_unicast',
                'unk_mcast_act': 'unknown_ipv4_multicast',
                'v6_unk_mcast_act': 'unknown_ipv6_multicast',
                'vmac': 'virtual_mac_address',
                'pim_v4_enabled': 'pim_ipv4_enabled',
                'host_route_adv': 'advertise_host_routes_enabled',
            }

            for aci_field, nb_field in bd_field_mapping.items():
                if aci_field in aci_data and aci_data[aci_field] is not None:
                    value = aci_data[aci_field]
                    # Skip invalid MAC addresses like 'not-applicable'
                    if nb_field in ('mac_address', 'virtual_mac_address'):
                        if not value or value.lower() in ('not-applicable', 'n/a', 'none', ''):
                            continue
                        # Validate MAC format (basic check)
                        if ':' not in value and '-' not in value:
                            continue
                    bd_params[nb_field] = value

            # Handle ep_move_detect specially
            if aci_data.get('ep_move_detect'):
                bd_params['ep_move_detection_enabled'] = aci_data['ep_move_detect'] == 'garp'

            bd, created = self.netbox.get_or_create_bridge_domain(
                tenant_id=tenant_id,
                vrf_id=vrf_id,
                name=bd_name,
                **bd_params
            )

            if created:
                self.result.created += 1
                logger.info(f"Created Bridge Domain: {tenant_name}/{bd_name}")
            else:
                updates = {}
                for aci_field, nb_field in bd_field_mapping.items():
                    if aci_field in aci_data and aci_data[aci_field] is not None:
                        value = aci_data[aci_field]
                        # Skip invalid MAC addresses like 'not-applicable'
                        if nb_field in ('mac_address', 'virtual_mac_address'):
                            if not value or value.lower() in ('not-applicable', 'n/a', 'none', ''):
                                continue
                            if ':' not in value and '-' not in value:
                                continue
                        current = getattr(bd, nb_field, None)
                        if not values_equal(current, value):
                            updates[nb_field] = value

                if aci_data.get('name_alias'):
                    current = getattr(bd, 'name_alias', None) or ''
                    if current != aci_data['name_alias']:
                        updates['name_alias'] = aci_data['name_alias']
                if aci_data.get('description'):
                    current = getattr(bd, 'description', None) or ''
                    if current != aci_data['description']:
                        updates['description'] = aci_data['description']

                if updates:
                    changed, verified = self.netbox.update_bridge_domain(
                        bd, updates, self.settings.verify_updates
                    )
                    if changed:
                        self.result.updated += 1
                        if verified:
                            self.result.verified += 1
                        logger.info(f"Updated Bridge Domain: {tenant_name}/{bd_name}")
                    else:
                        self.result.unchanged += 1
                else:
                    self.result.unchanged += 1

            bd_map = self.context.setdefault('bd_map', {})
            bd_map[f"{tenant_name}/{bd_name}"] = bd.id

            return True

        except Exception as e:
            logger.error(f"Failed to sync Bridge Domain {aci_data.get('name')}: {e}")
            self.result.failed += 1
            self.result.errors.append(str(e))
            return False


class SubnetSyncModule(BaseSyncModule):
    """Sync ACI Bridge Domain Subnets to NetBox."""

    @property
    def object_type(self) -> str:
        return "Subnet"

    def fetch_from_aci(self) -> List[Dict[str, Any]]:
        """Fetch Subnets from ACI."""
        return self.aci.get_subnets()

    def sync_object(self, aci_data: Dict[str, Any]) -> bool:
        """Sync Subnet to NetBox."""
        try:
            tenant_name = aci_data.get('tenant')
            bd_name = aci_data.get('bridge_domain')
            
            if not tenant_name or not bd_name:
                logger.warning(f"Skipping subnet without tenant/BD: {aci_data}")
                return False

            bd_map = self.context.get('bd_map', {})
            bd_id = bd_map.get(f"{tenant_name}/{bd_name}")
            
            if not bd_id:
                logger.warning(f"BD {tenant_name}/{bd_name} not found for subnet")
                return False

            subnet_ip = aci_data.get('ip')
            if not subnet_ip:
                logger.warning(f"Skipping subnet without IP: {aci_data}")
                return False

            # First, create or get the IP address in NetBox IPAM
            # The gateway_ip_address field requires an IP address object ID
            ip_obj, ip_created = self.netbox.get_or_create_ip_address(
                address=subnet_ip,
                description=f"BD Subnet Gateway - {bd_name}"
            )
            
            if ip_created:
                logger.debug(f"Created IP address in IPAM: {subnet_ip}")

            # Generate a name for the subnet (required field)
            subnet_name = aci_data.get('name') or f"{bd_name}-{subnet_ip.replace('/', '_')}"

            # Prepare subnet parameters
            subnet_params = {
                'name': subnet_name,  # Required field
            }
            
            if aci_data.get('name_alias'):
                subnet_params['name_alias'] = aci_data['name_alias']
            if aci_data.get('description'):
                subnet_params['description'] = aci_data['description']
            if 'preferred' in aci_data:
                subnet_params['preferred_ip_address_enabled'] = aci_data['preferred']
            if 'scope' in aci_data:
                scope = aci_data['scope'] or ''
                subnet_params['advertised_externally_enabled'] = 'public' in scope
                subnet_params['shared_enabled'] = 'shared' in scope
            if 'virtual' in aci_data:
                subnet_params['virtual_ip_enabled'] = aci_data['virtual']
            if 'ctrl' in aci_data:
                ctrl = aci_data['ctrl'] or ''
                subnet_params['no_default_svi_gateway'] = 'no-default-gateway' in ctrl
                subnet_params['nd_ra_enabled'] = 'nd' in ctrl
                subnet_params['igmp_querier_enabled'] = 'querier' in ctrl

            subnet, created = self.netbox.get_or_create_subnet(
                bd_id=bd_id,
                gateway_ip=ip_obj.id,  # Pass the IP address object ID
                **subnet_params
            )

            if created:
                self.result.created += 1
                logger.info(f"Created Subnet: {subnet_ip} in BD {bd_name}")
            else:
                updates = {}
                for key, value in subnet_params.items():
                    current = getattr(subnet, key, None)
                    if not values_equal(current, value):
                        logger.debug(f"Subnet {subnet_ip} field {key}: current={current!r}, new={value!r}")
                        updates[key] = value

                if updates:
                    changed, verified = self.netbox.update_subnet(
                        subnet, updates, self.settings.verify_updates
                    )
                    if changed:
                        self.result.updated += 1
                        if verified:
                            self.result.verified += 1
                        logger.info(f"Updated Subnet: {subnet_ip}")
                    else:
                        self.result.unchanged += 1
                else:
                    self.result.unchanged += 1

            return True

        except Exception as e:
            logger.error(f"Failed to sync Subnet {aci_data.get('ip')}: {e}")
            self.result.failed += 1
            self.result.errors.append(str(e))
            return False
