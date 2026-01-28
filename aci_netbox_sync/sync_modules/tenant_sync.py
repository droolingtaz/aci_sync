"""
Tenant Sync Module - Synchronize ACI Tenants to NetBox.
"""

import logging
from typing import Any, Dict, List, Optional

from .base import BaseSyncModule

logger = logging.getLogger(__name__)


class TenantSyncModule(BaseSyncModule):
    """Sync ACI Tenants to NetBox."""

    @property
    def object_type(self) -> str:
        return "Tenant"

    def fetch_from_aci(self) -> List[Dict[str, Any]]:
        """Fetch tenants from ACI."""
        return self.aci.get_tenants()

    def sync_object(self, aci_data: Dict[str, Any]) -> bool:
        """Sync tenant to NetBox."""
        try:
            fabric_id = self.context.get('fabric_id')
            if not fabric_id:
                logger.error("Fabric ID not found in context")
                return False

            tenant_name = aci_data.get('name')
            if not tenant_name:
                logger.warning(f"Skipping tenant without name: {aci_data}")
                return False

            # Prepare tenant parameters
            tenant_params = {}
            
            if aci_data.get('name_alias'):
                tenant_params['name_alias'] = aci_data['name_alias']
            if aci_data.get('description'):
                tenant_params['description'] = aci_data['description']

            # Get or create tenant
            tenant, created = self.netbox.get_or_create_tenant(
                fabric_id=fabric_id,
                name=tenant_name,
                **tenant_params
            )

            if created:
                self.result.created += 1
                logger.info(f"Created tenant: {tenant_name}")
            else:
                # Update existing tenant
                updates = {}
                if aci_data.get('name_alias'):
                    current = getattr(tenant, 'name_alias', None)
                    if current != aci_data['name_alias']:
                        updates['name_alias'] = aci_data['name_alias']
                if aci_data.get('description'):
                    current = getattr(tenant, 'description', None)
                    if current != aci_data['description']:
                        updates['description'] = aci_data['description']

                if updates:
                    changed, verified = self.netbox.update_tenant(
                        tenant, updates, self.settings.verify_updates
                    )
                    if changed:
                        self.result.updated += 1
                        if verified:
                            self.result.verified += 1
                        logger.info(f"Updated tenant: {tenant_name}")
                    else:
                        self.result.unchanged += 1
                else:
                    self.result.unchanged += 1

            # Store tenant mapping in context
            tenant_map = self.context.setdefault('tenant_map', {})
            tenant_map[tenant_name] = tenant.id

            return True

        except Exception as e:
            logger.error(f"Failed to sync tenant {aci_data.get('name')}: {e}")
            self.result.failed += 1
            self.result.errors.append(str(e))
            return False
