"""
ESG Sync Module - Synchronize ACI Endpoint Security Groups to NetBox.
"""

import logging
from typing import Any, Dict, List, Optional

from .base import BaseSyncModule

logger = logging.getLogger(__name__)


class ESGSyncModule(BaseSyncModule):
    """Sync ACI Endpoint Security Groups to NetBox."""

    @property
    def object_type(self) -> str:
        return "EndpointSecurityGroup"

    def fetch_from_aci(self) -> List[Dict[str, Any]]:
        """Fetch ESGs from ACI."""
        return self.aci.get_esgs()

    def sync_object(self, aci_data: Dict[str, Any]) -> bool:
        """Sync ESG to NetBox."""
        try:
            tenant_name = aci_data.get('tenant')
            ap_name = aci_data.get('app_profile')
            
            if not tenant_name or not ap_name:
                logger.warning(f"Skipping ESG without tenant/AP: {aci_data}")
                return False

            # Get AP ID from context
            ap_map = self.context.get('ap_map', {})
            ap_id = ap_map.get(f"{tenant_name}/{ap_name}")
            
            if not ap_id:
                logger.warning(f"AP {tenant_name}/{ap_name} not found for ESG")
                return False

            # Get VRF ID from context
            vrf_name = aci_data.get('vrf')
            vrf_map = self.context.get('vrf_map', {})
            vrf_id = vrf_map.get(f"{tenant_name}/{vrf_name}") if vrf_name else None
            
            if not vrf_id and vrf_name:
                logger.warning(f"VRF {vrf_name} not found for ESG {aci_data.get('name')}")

            esg_name = aci_data.get('name')
            if not esg_name:
                logger.warning(f"Skipping ESG without name: {aci_data}")
                return False

            # Prepare ESG parameters
            esg_params = {}
            
            if aci_data.get('name_alias'):
                esg_params['name_alias'] = aci_data['name_alias']
            if aci_data.get('description'):
                esg_params['description'] = aci_data['description']
            
            # ESG-specific attributes
            if aci_data.get('pref_gr_memb'):
                esg_params['preferred_group_member_enabled'] = aci_data['pref_gr_memb'] == 'include'
            if aci_data.get('prio'):
                esg_params['qos_class'] = aci_data['prio']
            if 'shutdown' in aci_data:
                esg_params['admin_shutdown'] = aci_data['shutdown']

            esg, created = self.netbox.get_or_create_esg(
                ap_id=ap_id,
                vrf_id=vrf_id,
                name=esg_name,
                **esg_params
            )

            if created:
                self.result.created += 1
                logger.info(f"Created ESG: {tenant_name}/{ap_name}/{esg_name}")
            else:
                updates = {}
                if aci_data.get('name_alias'):
                    if getattr(esg, 'name_alias', None) != aci_data['name_alias']:
                        updates['name_alias'] = aci_data['name_alias']
                if aci_data.get('description'):
                    if getattr(esg, 'description', None) != aci_data['description']:
                        updates['description'] = aci_data['description']
                if aci_data.get('pref_gr_memb'):
                    expected = aci_data['pref_gr_memb'] == 'include'
                    if getattr(esg, 'preferred_group_member_enabled', None) != expected:
                        updates['preferred_group_member_enabled'] = expected
                if 'shutdown' in aci_data:
                    if getattr(esg, 'admin_shutdown', None) != aci_data['shutdown']:
                        updates['admin_shutdown'] = aci_data['shutdown']

                if updates:
                    changed, verified = self.netbox.update_esg(
                        esg, updates, self.settings.verify_updates
                    )
                    if changed:
                        self.result.updated += 1
                        if verified:
                            self.result.verified += 1
                        logger.info(f"Updated ESG: {tenant_name}/{ap_name}/{esg_name}")
                    else:
                        self.result.unchanged += 1
                else:
                    self.result.unchanged += 1

            # Store ESG mapping in context
            esg_map = self.context.setdefault('esg_map', {})
            esg_map[f"{tenant_name}/{ap_name}/{esg_name}"] = esg.id

            return True

        except Exception as e:
            logger.error(f"Failed to sync ESG {aci_data.get('name')}: {e}")
            self.result.failed += 1
            self.result.errors.append(str(e))
            return False
