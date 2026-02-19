"""
Application Profile Sync Module - Synchronize ACI Application Profiles to NetBox.

Optimized with:
- FIELD_MAP / _build_updates for DRY field comparison
- Per-tenant pre-fetch caching
"""

import logging
from typing import Any, Dict, List

from .base import BaseSyncModule

logger = logging.getLogger(__name__)


class AppProfileSyncModule(BaseSyncModule):
    """Sync ACI Application Profiles to NetBox."""

    FIELD_MAP = {
        'name_alias': 'name_alias',
        'description': 'description',
    }

    @property
    def object_type(self) -> str:
        return "ApplicationProfile"

    def pre_sync(self) -> None:
        """Pre-fetch existing APs per tenant."""
        self._tenant_ap_caches: Dict[int, Dict] = {}
        tenant_map = self.context.get('tenant_map', {})
        for tenant_name, tenant_id in tenant_map.items():
            cache = self.netbox.fetch_all_app_profiles(tenant_id)
            self._tenant_ap_caches[tenant_id] = cache
            logger.debug(f"Pre-fetched {len(cache)} APs for tenant {tenant_name}")

    def fetch_from_aci(self) -> List[Dict[str, Any]]:
        return self.aci.get_app_profiles()

    def sync_object(self, aci_data: Dict[str, Any]) -> bool:
        try:
            tenant_name = aci_data.get('tenant')
            if not tenant_name:
                logger.warning(f"Skipping AP without tenant: {aci_data}")
                return False

            tenant_map = self.context.get('tenant_map', {})
            tenant_id = tenant_map.get(tenant_name)
            if not tenant_id:
                logger.warning(f"Tenant {tenant_name} not found for AP {aci_data.get('name')}")
                return False

            ap_name = aci_data.get('name')
            if not ap_name:
                logger.warning(f"Skipping AP without name: {aci_data}")
                return False

            ap_params = self._build_params(aci_data)

            cache = self._tenant_ap_caches.get(tenant_id, {})
            ap, created = self.netbox.get_or_create_ap_cached(
                cache, ap_name,
                tenant_id=tenant_id, **ap_params
            )

            if created:
                self.result.created += 1
                logger.info(f"Created Application Profile: {tenant_name}/{ap_name}")
            else:
                updates = self._build_updates(ap, aci_data)
                self._apply_updates(
                    ap, updates,
                    f"{tenant_name}/{ap_name}",
                    self.netbox.update_app_profile,
                )

            ap_map = self.context.setdefault('ap_map', {})
            ap_map[f"{tenant_name}/{ap_name}"] = ap.id
            return True

        except Exception as e:
            logger.error(f"Failed to sync AP {aci_data.get('name')}: {e}")
            self.result.failed += 1
            self.result.errors.append(str(e))
            return False