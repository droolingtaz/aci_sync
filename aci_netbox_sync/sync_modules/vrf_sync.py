"""
VRF Sync Module - Synchronize ACI VRFs (Contexts) to NetBox.

Optimized with:
- FIELD_MAP / CONVERTERS for DRY field comparison
- Per-tenant pre-fetch caching
"""

import logging
from typing import Any, Dict, List

from .base import BaseSyncModule

logger = logging.getLogger(__name__)


class VRFSyncModule(BaseSyncModule):
    """Sync ACI VRFs to NetBox."""

    FIELD_MAP = {
        'name_alias': 'name_alias',
        'description': 'description',
        'bd_enforced_enabled': 'bd_enforcement_enabled',
        'ip_data_plane_learning': 'ip_data_plane_learning_enabled',
        'pc_enf_dir': 'pc_enforcement_direction',
        'pc_enf_pref': 'pc_enforcement_preference',
        'pim_v4_enabled': 'pim_ipv4_enabled',
        'pim_v6_enabled': 'pim_ipv6_enabled',
        'preferred_group': 'preferred_group_enabled',
    }

    CONVERTERS = {
        'ip_data_plane_learning': lambda v: v == 'enabled',
    }

    @property
    def object_type(self) -> str:
        return "VRF"

    def pre_sync(self) -> None:
        """Pre-fetch existing VRFs per tenant."""
        self._tenant_vrf_caches: Dict[int, Dict] = {}
        tenant_map = self.context.get('tenant_map', {})
        for tenant_name, tenant_id in tenant_map.items():
            cache = self.netbox.fetch_all_vrfs(tenant_id)
            self._tenant_vrf_caches[tenant_id] = cache
            logger.debug(f"Pre-fetched {len(cache)} VRFs for tenant {tenant_name}")

    def fetch_from_aci(self) -> List[Dict[str, Any]]:
        return self.aci.get_vrfs()

    def sync_object(self, aci_data: Dict[str, Any]) -> bool:
        try:
            tenant_name = aci_data.get('tenant')
            if not tenant_name:
                logger.warning(f"Skipping VRF without tenant: {aci_data}")
                return False

            tenant_map = self.context.get('tenant_map', {})
            tenant_id = tenant_map.get(tenant_name)
            if not tenant_id:
                logger.warning(f"Tenant {tenant_name} not found for VRF {aci_data.get('name')}")
                return False

            vrf_name = aci_data.get('name')
            if not vrf_name:
                logger.warning(f"Skipping VRF without name: {aci_data}")
                return False

            # Build params using field map + converters
            vrf_params = self._build_params(aci_data)

            # Use per-tenant cache
            cache = self._tenant_vrf_caches.get(tenant_id, {})
            vrf, created = self.netbox.get_or_create_vrf_cached(
                cache, vrf_name,
                tenant_id=tenant_id, **vrf_params
            )

            if created:
                self.result.created += 1
                logger.info(f"Created VRF: {tenant_name}/{vrf_name}")
            else:
                updates = self._build_updates(vrf, aci_data)
                self._apply_updates(
                    vrf, updates,
                    f"{tenant_name}/{vrf_name}",
                    self.netbox.update_vrf,
                )

            # Store VRF mapping
            vrf_map = self.context.setdefault('vrf_map', {})
            vrf_map[f"{tenant_name}/{vrf_name}"] = vrf.id

            return True

        except Exception as e:
            logger.error(f"Failed to sync VRF {aci_data.get('name')}: {e}")
            self.result.failed += 1
            self.result.errors.append(str(e))
            return False