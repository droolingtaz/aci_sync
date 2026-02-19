"""
Bridge Domain Sync Module - Synchronize ACI Bridge Domains and Subnets to NetBox.

Optimized with:
- FIELD_MAP / CONVERTERS for DRY field comparison
- Per-tenant pre-fetch caching
- MAC validation in converters
"""

import logging
from typing import Any, Callable, Dict, List, Optional

from .base import BaseSyncModule, values_equal

logger = logging.getLogger(__name__)


def _valid_mac(value: Any) -> Optional[str]:
    """Return MAC string if valid, else None."""
    if not value:
        return None
    s = str(value)
    if s.lower() in ('not-applicable', 'n/a', 'none', ''):
        return None
    if ':' not in s and '-' not in s:
        return None
    return s


class BridgeDomainSyncModule(BaseSyncModule):
    """Sync ACI Bridge Domains to NetBox."""

    FIELD_MAP = {
        'name_alias': 'name_alias',
        'description': 'description',
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
        'ep_move_detect': 'ep_move_detection_enabled',
    }

    CONVERTERS = {
        'mac': _valid_mac,
        'vmac': _valid_mac,
        'ep_move_detect': lambda v: v == 'garp',
    }

    @property
    def object_type(self) -> str:
        return "BridgeDomain"

    def pre_sync(self) -> None:
        """Pre-fetch existing BDs per tenant."""
        self._tenant_bd_caches: Dict[int, Dict] = {}
        tenant_map = self.context.get('tenant_map', {})
        for tenant_name, tenant_id in tenant_map.items():
            cache = self.netbox.fetch_all_bridge_domains(tenant_id)
            self._tenant_bd_caches[tenant_id] = cache
            logger.debug(f"Pre-fetched {len(cache)} BDs for tenant {tenant_name}")

    def fetch_from_aci(self) -> List[Dict[str, Any]]:
        return self.aci.get_bridge_domains()

    def _build_params(self, aci_data, field_map=None, converters=None):
        """Override to skip None values from MAC converter."""
        params = super()._build_params(aci_data, field_map, converters)
        # Remove keys where converter returned None (invalid MACs)
        return {k: v for k, v in params.items() if v is not None}

    def _build_updates(self, existing_obj, aci_data, field_map=None,
                       converters=None, extra_updates=None):
        """Override to skip None values from MAC converter."""
        updates = super()._build_updates(existing_obj, aci_data, field_map,
                                         converters, extra_updates)
        return {k: v for k, v in updates.items() if v is not None}

    def sync_object(self, aci_data: Dict[str, Any]) -> bool:
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

            # Resolve VRF (may be in different tenant, e.g. common)
            vrf_name = aci_data.get('vrf')
            vrf_tenant = aci_data.get('vrf_tenant', tenant_name)
            vrf_map = self.context.get('vrf_map', {})
            vrf_id = vrf_map.get(f"{vrf_tenant}/{vrf_name}") if vrf_name else None

            if not vrf_id:
                bd_name = aci_data.get('name')
                if vrf_name:
                    logger.warning(f"VRF {vrf_tenant}/{vrf_name} not found for BD {tenant_name}/{bd_name} - skipping")
                else:
                    logger.warning(f"BD {tenant_name}/{bd_name} has no VRF assigned - skipping")
                return False

            bd_name = aci_data.get('name')
            if not bd_name:
                logger.warning(f"Skipping BD without name: {aci_data}")
                return False

            bd_params = self._build_params(aci_data)

            # Use per-tenant cache
            cache = self._tenant_bd_caches.get(tenant_id, {})
            bd, created = self.netbox.get_or_create_bd_cached(
                cache, bd_name,
                tenant_id=tenant_id, vrf_id=vrf_id, **bd_params
            )

            if created:
                self.result.created += 1
                logger.info(f"Created Bridge Domain: {tenant_name}/{bd_name}")
            else:
                # Check VRF change (cross-tenant reference)
                extra = {}
                current_vrf = getattr(bd, 'aci_vrf', None)
                current_vrf_id = current_vrf.id if hasattr(current_vrf, 'id') else current_vrf
                if current_vrf_id != vrf_id:
                    extra['aci_vrf'] = vrf_id

                updates = self._build_updates(bd, aci_data, extra_updates=extra)
                self._apply_updates(
                    bd, updates,
                    f"{tenant_name}/{bd_name}",
                    self.netbox.update_bridge_domain,
                )

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

    FIELD_MAP = {
        'name_alias': 'name_alias',
        'description': 'description',
        'preferred': 'preferred_ip_address_enabled',
        'virtual': 'virtual_ip_enabled',
    }

    @property
    def object_type(self) -> str:
        return "Subnet"

    def fetch_from_aci(self) -> List[Dict[str, Any]]:
        return self.aci.get_subnets()

    def _build_scope_and_ctrl_params(self, aci_data: Dict) -> Dict[str, Any]:
        """Extract scope and ctrl flags into NetBox fields."""
        params = {}
        if 'scope' in aci_data:
            scope = aci_data['scope'] or ''
            params['advertised_externally_enabled'] = 'public' in scope
            params['shared_enabled'] = 'shared' in scope
        if 'ctrl' in aci_data:
            ctrl = aci_data['ctrl'] or ''
            params['no_default_svi_gateway'] = 'no-default-gateway' in ctrl
            params['nd_ra_enabled'] = 'nd' in ctrl
            params['igmp_querier_enabled'] = 'querier' in ctrl
        return params

    def sync_object(self, aci_data: Dict[str, Any]) -> bool:
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

            # Create/get gateway IP in IPAM
            ip_obj, ip_created = self.netbox.get_or_create_ip_address(
                address=subnet_ip,
                description=f"BD Subnet Gateway - {bd_name}"
            )
            if ip_created:
                logger.debug(f"Created IP address in IPAM: {subnet_ip}")

            subnet_name = aci_data.get('name') or f"{bd_name}-{subnet_ip.replace('/', '_')}"

            # Build params
            subnet_params = self._build_params(aci_data)
            subnet_params['name'] = subnet_name
            subnet_params.update(self._build_scope_and_ctrl_params(aci_data))

            subnet, created = self.netbox.get_or_create_subnet(
                bd_id=bd_id,
                gateway_ip=ip_obj.id,
                **subnet_params
            )

            if created:
                self.result.created += 1
                logger.info(f"Created Subnet: {subnet_ip} in BD {bd_name}")
            else:
                # Check BD change
                extra = {}
                current_bd = getattr(subnet, 'aci_bridge_domain', None)
                current_bd_id = current_bd.id if hasattr(current_bd, 'id') else current_bd
                if current_bd_id != bd_id:
                    extra['aci_bridge_domain'] = bd_id

                updates = self._build_updates(subnet, aci_data, extra_updates=extra)
                # Also check scope/ctrl derived fields
                scope_ctrl = self._build_scope_and_ctrl_params(aci_data)
                for key, value in scope_ctrl.items():
                    current = getattr(subnet, key, None)
                    if not values_equal(current, value):
                        updates[key] = value

                self._apply_updates(subnet, updates, subnet_ip, self.netbox.update_subnet)

            return True

        except Exception as e:
            logger.error(f"Failed to sync Subnet {aci_data.get('ip')}: {e}")
            self.result.failed += 1
            self.result.errors.append(str(e))
            return False