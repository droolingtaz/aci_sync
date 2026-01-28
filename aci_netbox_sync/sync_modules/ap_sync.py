"""
Application Profile Sync Module - Synchronize ACI Application Profiles to NetBox.
"""

import logging
from typing import Any, Dict, List, Optional

from .base import BaseSyncModule

logger = logging.getLogger(__name__)


class AppProfileSyncModule(BaseSyncModule):
    """Sync ACI Application Profiles to NetBox."""

    @property
    def object_type(self) -> str:
        return "ApplicationProfile"

    def fetch_from_aci(self) -> List[Dict[str, Any]]:
        """Fetch Application Profiles from ACI."""
        return self.aci.get_app_profiles()

    def sync_object(self, aci_data: Dict[str, Any]) -> bool:
        """Sync Application Profile to NetBox."""
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

            # Prepare AP parameters
            ap_params = {}
            
            if aci_data.get('name_alias'):
                ap_params['name_alias'] = aci_data['name_alias']
            if aci_data.get('description'):
                ap_params['description'] = aci_data['description']

            ap, created = self.netbox.get_or_create_app_profile(
                tenant_id=tenant_id,
                name=ap_name,
                **ap_params
            )

            if created:
                self.result.created += 1
                logger.info(f"Created Application Profile: {tenant_name}/{ap_name}")
            else:
                updates = {}
                if aci_data.get('name_alias'):
                    if getattr(ap, 'name_alias', None) != aci_data['name_alias']:
                        updates['name_alias'] = aci_data['name_alias']
                if aci_data.get('description'):
                    if getattr(ap, 'description', None) != aci_data['description']:
                        updates['description'] = aci_data['description']

                if updates:
                    changed, verified = self.netbox.update_app_profile(
                        ap, updates, self.settings.verify_updates
                    )
                    if changed:
                        self.result.updated += 1
                        if verified:
                            self.result.verified += 1
                        logger.info(f"Updated Application Profile: {tenant_name}/{ap_name}")
                    else:
                        self.result.unchanged += 1
                else:
                    self.result.unchanged += 1

            # Store AP mapping in context
            ap_map = self.context.setdefault('ap_map', {})
            ap_map[f"{tenant_name}/{ap_name}"] = ap.id

            return True

        except Exception as e:
            logger.error(f"Failed to sync AP {aci_data.get('name')}: {e}")
            self.result.failed += 1
            self.result.errors.append(str(e))
            return False
