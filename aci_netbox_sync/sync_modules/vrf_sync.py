"""
VRF Sync Module - Synchronize ACI VRFs (Contexts) to NetBox.
"""

import logging
from typing import Any, Dict, List, Optional

from .base import BaseSyncModule

logger = logging.getLogger(__name__)


class VRFSyncModule(BaseSyncModule):
    """Sync ACI VRFs to NetBox."""

    @property
    def object_type(self) -> str:
        return "VRF"

    def fetch_from_aci(self) -> List[Dict[str, Any]]:
        """Fetch VRFs from ACI."""
        return self.aci.get_vrfs()

    def sync_object(self, aci_data: Dict[str, Any]) -> bool:
        """Sync VRF to NetBox."""
        try:
            # Get tenant ID from context
            tenant_name = aci_data.get('tenant')
            if not tenant_name:
                logger.warning(f"Skipping VRF without tenant: {aci_data}")
                return False

            tenant_map = self.context.get('tenant_map', {})
            tenant_id = tenant_map.get(tenant_name)
            
            if not tenant_id:
                logger.warning(f"Tenant {tenant_name} not found in context for VRF {aci_data.get('name')}")
                return False

            vrf_name = aci_data.get('name')
            if not vrf_name:
                logger.warning(f"Skipping VRF without name: {aci_data}")
                return False

            # Prepare VRF parameters matching NetBox ACI plugin model
            vrf_params = {}
            
            if aci_data.get('name_alias'):
                vrf_params['name_alias'] = aci_data['name_alias']
            if aci_data.get('description'):
                vrf_params['description'] = aci_data['description']
            
            # VRF-specific attributes
            if 'bd_enforced_enabled' in aci_data:
                vrf_params['bd_enforcement_enabled'] = aci_data['bd_enforced_enabled']
            if 'ip_data_plane_learning' in aci_data:
                vrf_params['ip_data_plane_learning_enabled'] = aci_data['ip_data_plane_learning'] == 'enabled'
            if 'pc_enf_dir' in aci_data:
                vrf_params['pc_enforcement_direction'] = aci_data['pc_enf_dir']
            if 'pc_enf_pref' in aci_data:
                vrf_params['pc_enforcement_preference'] = aci_data['pc_enf_pref']
            if 'pim_v4_enabled' in aci_data:
                vrf_params['pim_ipv4_enabled'] = aci_data['pim_v4_enabled']
            if 'pim_v6_enabled' in aci_data:
                vrf_params['pim_ipv6_enabled'] = aci_data['pim_v6_enabled']
            if 'preferred_group' in aci_data:
                vrf_params['preferred_group_enabled'] = aci_data['preferred_group']

            # Get or create VRF
            vrf, created = self.netbox.get_or_create_vrf(
                tenant_id=tenant_id,
                name=vrf_name,
                **vrf_params
            )

            if created:
                self.result.created += 1
                logger.info(f"Created VRF: {tenant_name}/{vrf_name}")
            else:
                # Update existing VRF
                updates = {}
                update_fields = [
                    ('name_alias', 'name_alias'),
                    ('description', 'description'),
                    ('bd_enforcement_enabled', 'bd_enforced_enabled'),
                    ('ip_data_plane_learning_enabled', 'ip_data_plane_learning'),
                    ('pc_enforcement_direction', 'pc_enf_dir'),
                    ('pc_enforcement_preference', 'pc_enf_pref'),
                    ('pim_ipv4_enabled', 'pim_v4_enabled'),
                    ('pim_ipv6_enabled', 'pim_v6_enabled'),
                    ('preferred_group_enabled', 'preferred_group'),
                ]

                for nb_field, aci_field in update_fields:
                    if aci_field in aci_data:
                        aci_value = aci_data[aci_field]
                        # Handle special conversions
                        if nb_field == 'ip_data_plane_learning_enabled':
                            aci_value = aci_value == 'enabled'
                        # Handle null strings - use empty string instead
                        if aci_value is None and nb_field in ('name_alias', 'description'):
                            aci_value = ''
                        
                        current = getattr(vrf, nb_field, None)
                        if current != aci_value:
                            updates[nb_field] = aci_value

                if updates:
                    changed, verified = self.netbox.update_vrf(
                        vrf, updates, self.settings.verify_updates
                    )
                    if changed:
                        self.result.updated += 1
                        if verified:
                            self.result.verified += 1
                        logger.info(f"Updated VRF: {tenant_name}/{vrf_name}")
                    else:
                        self.result.unchanged += 1
                else:
                    self.result.unchanged += 1

            # Store VRF mapping in context (keyed by tenant/vrf name)
            vrf_map = self.context.setdefault('vrf_map', {})
            vrf_map[f"{tenant_name}/{vrf_name}"] = vrf.id

            return True

        except Exception as e:
            logger.error(f"Failed to sync VRF {aci_data.get('name')}: {e}")
            self.result.failed += 1
            self.result.errors.append(str(e))
            return False
