"""
EPG Sync Module - Synchronize ACI Endpoint Groups to NetBox.

Optimized with:
- FIELD_MAP / CONVERTERS for DRY field comparison
- Per-AP pre-fetch caching
"""

import logging
from typing import Any, Dict, List

from .base import BaseSyncModule, values_equal

logger = logging.getLogger(__name__)


class EPGSyncModule(BaseSyncModule):
    """Sync ACI Endpoint Groups to NetBox."""

    FIELD_MAP = {
        'name_alias': 'name_alias',
        'description': 'description',
        'pref_gr_memb': 'preferred_group_member_enabled',
        'prio': 'qos_class',
        'flood_on_encap': 'flood_in_encapsulation_enabled',
        'shutdown': 'admin_shutdown',
    }

    CONVERTERS = {
        'pref_gr_memb': lambda v: v == 'include',
    }

    @property
    def object_type(self) -> str:
        return "EndpointGroup"

    def pre_sync(self) -> None:
        """Pre-fetch existing EPGs per AP."""
        self._ap_epg_caches: Dict[int, Dict] = {}
        ap_map = self.context.get('ap_map', {})
        for ap_key, ap_id in ap_map.items():
            cache = self.netbox.fetch_all_epgs(ap_id)
            self._ap_epg_caches[ap_id] = cache
            logger.debug(f"Pre-fetched {len(cache)} EPGs for AP {ap_key}")

    def fetch_from_aci(self) -> List[Dict[str, Any]]:
        return self.aci.get_epgs()

    def sync_object(self, aci_data: Dict[str, Any]) -> bool:
        try:
            tenant_name = aci_data.get('tenant')
            ap_name = aci_data.get('app_profile')
            if not tenant_name or not ap_name:
                logger.warning(f"Skipping EPG without tenant/AP: {aci_data}")
                return False

            ap_map = self.context.get('ap_map', {})
            ap_id = ap_map.get(f"{tenant_name}/{ap_name}")
            if not ap_id:
                logger.warning(f"AP {tenant_name}/{ap_name} not found for EPG")
                return False

            bd_name = aci_data.get('bridge_domain')
            bd_map = self.context.get('bd_map', {})
            bd_id = bd_map.get(f"{tenant_name}/{bd_name}") if bd_name else None
            if not bd_id:
                epg_name = aci_data.get('name')
                if bd_name:
                    logger.warning(f"BD {bd_name} not found for EPG {epg_name} - skipping")
                else:
                    logger.warning(f"EPG {tenant_name}/{ap_name}/{epg_name} has no BD - skipping")
                return False

            epg_name = aci_data.get('name')
            if not epg_name:
                logger.warning(f"Skipping EPG without name: {aci_data}")
                return False

            # Skip uSeg EPGs
            if aci_data.get('is_attr_based_epg'):
                logger.debug(f"Skipping uSeg EPG: {epg_name}")
                return True

            epg_params = self._build_params(aci_data)

            # Handle intra-EPG isolation (not in standard FIELD_MAP)
            if aci_data.get('pc_enf_pref') == 'enforced':
                epg_params['intra_epg_isolation_enabled'] = True

            # Use per-AP cache
            cache = self._ap_epg_caches.get(ap_id, {})
            epg, created = self.netbox.get_or_create_epg_cached(
                cache, epg_name,
                ap_id=ap_id, bd_id=bd_id, **epg_params
            )

            if created:
                self.result.created += 1
                logger.info(f"Created EPG: {tenant_name}/{ap_name}/{epg_name}")
            else:
                # Check BD change
                extra = {}
                current_bd = getattr(epg, 'aci_bridge_domain', None)
                current_bd_id = current_bd.id if hasattr(current_bd, 'id') else current_bd
                if current_bd_id != bd_id:
                    extra['aci_bridge_domain'] = bd_id

                updates = self._build_updates(epg, aci_data, extra_updates=extra)
                self._apply_updates(
                    epg, updates,
                    f"{tenant_name}/{ap_name}/{epg_name}",
                    self.netbox.update_epg,
                )

            epg_map = self.context.setdefault('epg_map', {})
            epg_map[f"{tenant_name}/{ap_name}/{epg_name}"] = epg.id
            return True

        except Exception as e:
            logger.error(f"Failed to sync EPG {aci_data.get('name')}: {e}")
            self.result.failed += 1
            self.result.errors.append(str(e))
            return False