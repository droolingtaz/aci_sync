"""
ESG Sync Module - Synchronize ACI Endpoint Security Groups to NetBox.

Optimized with:
- FIELD_MAP / CONVERTERS for DRY field comparison
- Per-AP pre-fetch caching
"""

import logging
from typing import Any, Dict, List

from .base import BaseSyncModule

logger = logging.getLogger(__name__)


class ESGSyncModule(BaseSyncModule):
    """Sync ACI Endpoint Security Groups to NetBox."""

    FIELD_MAP = {
        'name_alias': 'name_alias',
        'description': 'description',
        'pref_gr_memb': 'preferred_group_member_enabled',
        'prio': 'qos_class',
        'shutdown': 'admin_shutdown',
    }

    CONVERTERS = {
        'pref_gr_memb': lambda v: v == 'include',
    }

    @property
    def object_type(self) -> str:
        return "EndpointSecurityGroup"

    def pre_sync(self) -> None:
        """Pre-fetch existing ESGs per AP."""
        self._ap_esg_caches: Dict[int, Dict] = {}
        ap_map = self.context.get('ap_map', {})
        for ap_key, ap_id in ap_map.items():
            cache = self.netbox.fetch_all_esgs(ap_id)
            self._ap_esg_caches[ap_id] = cache
            logger.debug(f"Pre-fetched {len(cache)} ESGs for AP {ap_key}")

    def fetch_from_aci(self) -> List[Dict[str, Any]]:
        return self.aci.get_esgs()

    def sync_object(self, aci_data: Dict[str, Any]) -> bool:
        try:
            tenant_name = aci_data.get('tenant')
            ap_name = aci_data.get('app_profile')
            if not tenant_name or not ap_name:
                logger.warning(f"Skipping ESG without tenant/AP: {aci_data}")
                return False

            ap_map = self.context.get('ap_map', {})
            ap_id = ap_map.get(f"{tenant_name}/{ap_name}")
            if not ap_id:
                logger.warning(f"AP {tenant_name}/{ap_name} not found for ESG")
                return False

            vrf_name = aci_data.get('vrf')
            vrf_map = self.context.get('vrf_map', {})
            vrf_id = vrf_map.get(f"{tenant_name}/{vrf_name}") if vrf_name else None
            if not vrf_id and vrf_name:
                logger.warning(f"VRF {vrf_name} not found for ESG {aci_data.get('name')}")

            esg_name = aci_data.get('name')
            if not esg_name:
                logger.warning(f"Skipping ESG without name: {aci_data}")
                return False

            esg_params = self._build_params(aci_data)

            # Use per-AP cache
            cache = self._ap_esg_caches.get(ap_id, {})
            esg, created = self.netbox.get_or_create_esg_cached(
                cache, esg_name,
                ap_id=ap_id, vrf_id=vrf_id, **esg_params
            )

            if created:
                self.result.created += 1
                logger.info(f"Created ESG: {tenant_name}/{ap_name}/{esg_name}")
            else:
                updates = self._build_updates(esg, aci_data)
                self._apply_updates(
                    esg, updates,
                    f"{tenant_name}/{ap_name}/{esg_name}",
                    self.netbox.update_esg,
                )

            esg_map = self.context.setdefault('esg_map', {})
            esg_map[f"{tenant_name}/{ap_name}/{esg_name}"] = esg.id
            return True

        except Exception as e:
            logger.error(f"Failed to sync ESG {aci_data.get('name')}: {e}")
            self.result.failed += 1
            self.result.errors.append(str(e))
            return False