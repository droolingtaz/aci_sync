"""
Tenant Sync Module - Synchronize ACI Tenants to NetBox.

Optimized with:
- FIELD_MAP / _build_updates for DRY field comparison
- Pre-fetched tenant cache to avoid per-object API lookups
"""

import logging
from typing import Any, Dict, List

from .base import BaseSyncModule

logger = logging.getLogger(__name__)


class TenantSyncModule(BaseSyncModule):
    """Sync ACI Tenants to NetBox."""

    FIELD_MAP = {
        'name_alias': 'name_alias',
        'description': 'description',
    }

    @property
    def object_type(self) -> str:
        return "Tenant"

    def pre_sync(self) -> None:
        """Pre-fetch existing tenants to avoid per-object lookups."""
        fabric_id = self.context.get('fabric_id')
        if fabric_id:
            self._existing_cache = self.netbox.fetch_all_tenants(fabric_id)
            logger.debug(f"Pre-fetched {len(self._existing_cache)} existing tenants")

    def fetch_from_aci(self) -> List[Dict[str, Any]]:
        return self.aci.get_tenants()

    def sync_object(self, aci_data: Dict[str, Any]) -> bool:
        try:
            fabric_id = self.context.get('fabric_id')
            if not fabric_id:
                logger.error("Fabric ID not found in context")
                return False

            tenant_name = aci_data.get('name')
            if not tenant_name:
                logger.warning(f"Skipping tenant without name: {aci_data}")
                return False

            # Build create params from field map
            tenant_params = self._build_params(aci_data)

            # Use cache for lookup, fall back to API create
            tenant, created = self.netbox.get_or_create_tenant_cached(
                self._existing_cache, tenant_name,
                fabric_id=fabric_id, **tenant_params
            )

            if created:
                self.result.created += 1
                logger.info(f"Created tenant: {tenant_name}")
            else:
                updates = self._build_updates(tenant, aci_data)
                self._apply_updates(tenant, updates, tenant_name, self.netbox.update_tenant)

            # Store tenant mapping in context
            tenant_map = self.context.setdefault('tenant_map', {})
            tenant_map[tenant_name] = tenant.id

            return True

        except Exception as e:
            logger.error(f"Failed to sync tenant {aci_data.get('name')}: {e}")
            self.result.failed += 1
            self.result.errors.append(str(e))
            return False