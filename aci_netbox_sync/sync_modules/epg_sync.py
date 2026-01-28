"""
EPG Sync Module - Synchronize ACI Endpoint Groups to NetBox.
"""

import logging
from typing import Any, Dict, List, Optional

from .base import BaseSyncModule, values_equal

logger = logging.getLogger(__name__)


class EPGSyncModule(BaseSyncModule):
    """Sync ACI Endpoint Groups to NetBox."""

    @property
    def object_type(self) -> str:
        return "EndpointGroup"

    def fetch_from_aci(self) -> List[Dict[str, Any]]:
        """Fetch EPGs from ACI."""
        return self.aci.get_epgs()

    def sync_object(self, aci_data: Dict[str, Any]) -> bool:
        """Sync EPG to NetBox."""
        try:
            tenant_name = aci_data.get('tenant')
            ap_name = aci_data.get('app_profile')
            
            if not tenant_name or not ap_name:
                logger.warning(f"Skipping EPG without tenant/AP: {aci_data}")
                return False

            # Get AP ID from context
            ap_map = self.context.get('ap_map', {})
            ap_id = ap_map.get(f"{tenant_name}/{ap_name}")
            
            if not ap_id:
                logger.warning(f"AP {tenant_name}/{ap_name} not found for EPG")
                return False

            # Get BD ID from context
            bd_name = aci_data.get('bridge_domain')
            bd_map = self.context.get('bd_map', {})
            bd_id = bd_map.get(f"{tenant_name}/{bd_name}") if bd_name else None
            
            # NetBox ACI plugin requires BD for EPGs
            if not bd_id:
                epg_name = aci_data.get('name')
                if bd_name:
                    logger.warning(f"BD {bd_name} not found for EPG {epg_name} - skipping")
                else:
                    logger.warning(f"EPG {tenant_name}/{ap_name}/{epg_name} has no BD assigned - NetBox ACI plugin requires BD")
                return False

            epg_name = aci_data.get('name')
            if not epg_name:
                logger.warning(f"Skipping EPG without name: {aci_data}")
                return False

            # Skip uSeg EPGs - they're handled separately
            if aci_data.get('is_attr_based_epg'):
                logger.debug(f"Skipping uSeg EPG: {epg_name}")
                return True

            # Prepare EPG parameters
            epg_params = {}
            
            if aci_data.get('name_alias'):
                epg_params['name_alias'] = aci_data['name_alias']
            if aci_data.get('description'):
                epg_params['description'] = aci_data['description']
            
            # EPG-specific attributes
            epg_field_mapping = {
                'pref_gr_memb': 'preferred_group_member_enabled',
                'prio': 'qos_class',
                'flood_on_encap': 'flood_in_encapsulation_enabled',
                'shutdown': 'admin_shutdown',
            }

            for aci_field, nb_field in epg_field_mapping.items():
                if aci_field in aci_data:
                    value = aci_data[aci_field]
                    # Handle special conversions
                    if nb_field == 'preferred_group_member_enabled':
                        value = value == 'include'
                    epg_params[nb_field] = value

            # Handle intra-EPG isolation
            if aci_data.get('pc_enf_pref') == 'enforced':
                epg_params['intra_epg_isolation_enabled'] = True

            epg, created = self.netbox.get_or_create_epg(
                ap_id=ap_id,
                bd_id=bd_id,
                name=epg_name,
                **epg_params
            )

            if created:
                self.result.created += 1
                logger.info(f"Created EPG: {tenant_name}/{ap_name}/{epg_name}")
            else:
                updates = {}
                for aci_field, nb_field in epg_field_mapping.items():
                    if aci_field in aci_data:
                        aci_value = aci_data[aci_field]
                        if nb_field == 'preferred_group_member_enabled':
                            aci_value = aci_value == 'include'
                        current = getattr(epg, nb_field, None)
                        
                        if not values_equal(current, aci_value):
                            logger.debug(f"EPG {epg_name} field {nb_field}: current={current!r}, aci={aci_value!r}")
                            updates[nb_field] = aci_value

                # Check name_alias - only update if ACI has a value and it differs
                if aci_data.get('name_alias'):
                    current_alias = getattr(epg, 'name_alias', None) or ''
                    if current_alias != aci_data['name_alias']:
                        updates['name_alias'] = aci_data['name_alias']
                        
                # Check description - only update if ACI has a value and it differs
                if aci_data.get('description'):
                    current_desc = getattr(epg, 'description', None) or ''
                    if current_desc != aci_data['description']:
                        updates['description'] = aci_data['description']

                if updates:
                    logger.debug(f"EPG {epg_name} updates: {updates}")
                    changed, verified = self.netbox.update_epg(
                        epg, updates, self.settings.verify_updates
                    )
                    if changed:
                        self.result.updated += 1
                        if verified:
                            self.result.verified += 1
                        logger.info(f"Updated EPG: {tenant_name}/{ap_name}/{epg_name}")
                    else:
                        self.result.unchanged += 1
                else:
                    self.result.unchanged += 1

            # Store EPG mapping in context
            epg_map = self.context.setdefault('epg_map', {})
            epg_map[f"{tenant_name}/{ap_name}/{epg_name}"] = epg.id

            return True

        except Exception as e:
            logger.error(f"Failed to sync EPG {aci_data.get('name')}: {e}")
            self.result.failed += 1
            self.result.errors.append(str(e))
            return False
