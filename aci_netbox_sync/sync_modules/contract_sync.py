"""
Contract Sync Module - Synchronize ACI Contracts, Subjects, and Filters to NetBox.
"""

import logging
from typing import Any, Dict, List, Optional

from .base import BaseSyncModule

logger = logging.getLogger(__name__)


class ContractFilterSyncModule(BaseSyncModule):
    """Sync ACI Contract Filters to NetBox."""

    @property
    def object_type(self) -> str:
        return "ContractFilter"

    def fetch_from_aci(self) -> List[Dict[str, Any]]:
        """Fetch Contract Filters from ACI."""
        return self.aci.get_contract_filters()

    def sync_object(self, aci_data: Dict[str, Any]) -> bool:
        """Sync Contract Filter to NetBox."""
        try:
            tenant_name = aci_data.get('tenant')
            if not tenant_name:
                logger.warning(f"Skipping filter without tenant: {aci_data}")
                return False

            tenant_map = self.context.get('tenant_map', {})
            tenant_id = tenant_map.get(tenant_name)
            
            if not tenant_id:
                logger.warning(f"Tenant {tenant_name} not found for filter")
                return False

            filter_name = aci_data.get('name')
            if not filter_name:
                logger.warning(f"Skipping filter without name: {aci_data}")
                return False

            # Prepare filter parameters
            filter_params = {}
            
            if aci_data.get('name_alias'):
                filter_params['name_alias'] = aci_data['name_alias']
            if aci_data.get('description'):
                filter_params['description'] = aci_data['description']

            flt, created = self.netbox.get_or_create_contract_filter(
                tenant_id=tenant_id,
                name=filter_name,
                **filter_params
            )

            if created:
                self.result.created += 1
                logger.info(f"Created Contract Filter: {tenant_name}/{filter_name}")
            else:
                updates = {}
                if aci_data.get('name_alias'):
                    if getattr(flt, 'name_alias', None) != aci_data['name_alias']:
                        updates['name_alias'] = aci_data['name_alias']
                if aci_data.get('description'):
                    if getattr(flt, 'description', None) != aci_data['description']:
                        updates['description'] = aci_data['description']

                if updates:
                    changed, verified = self.netbox.update_contract_filter(
                        flt, updates, self.settings.verify_updates
                    )
                    if changed:
                        self.result.updated += 1
                        if verified:
                            self.result.verified += 1
                        logger.info(f"Updated Contract Filter: {tenant_name}/{filter_name}")
                    else:
                        self.result.unchanged += 1
                else:
                    self.result.unchanged += 1

            # Store filter mapping in context
            filter_map = self.context.setdefault('filter_map', {})
            filter_map[f"{tenant_name}/{filter_name}"] = flt.id

            # Sync filter entries
            entries = aci_data.get('entries', [])
            for entry_data in entries:
                self._sync_filter_entry(flt.id, tenant_name, filter_name, entry_data)

            return True

        except Exception as e:
            logger.error(f"Failed to sync Contract Filter {aci_data.get('name')}: {e}")
            self.result.failed += 1
            self.result.errors.append(str(e))
            return False

    def _sync_filter_entry(self, filter_id: int, tenant_name: str,
                           filter_name: str, entry_data: Dict) -> bool:
        """Sync a filter entry."""
        try:
            entry_name = entry_data.get('name')
            if not entry_name:
                return False

            # Map ACI entry attributes to NetBox fields
            entry_params = {}
            
            # Ether type
            if entry_data.get('etherT') and entry_data['etherT'] != 'unspecified':
                entry_params['ether_type'] = entry_data['etherT']
            
            # IP Protocol
            if entry_data.get('prot') and entry_data['prot'] != 'unspecified':
                entry_params['ip_protocol'] = entry_data['prot']
            
            # Destination ports
            if entry_data.get('dFromPort') and entry_data['dFromPort'] != 'unspecified':
                entry_params['destination_port_from'] = entry_data['dFromPort']
            if entry_data.get('dToPort') and entry_data['dToPort'] != 'unspecified':
                entry_params['destination_port_to'] = entry_data['dToPort']
            
            # Source ports
            if entry_data.get('sFromPort') and entry_data['sFromPort'] != 'unspecified':
                entry_params['source_port_from'] = entry_data['sFromPort']
            if entry_data.get('sToPort') and entry_data['sToPort'] != 'unspecified':
                entry_params['source_port_to'] = entry_data['sToPort']

            entry, created = self.netbox.get_or_create_filter_entry(
                filter_id=filter_id,
                name=entry_name,
                **entry_params
            )

            if created:
                logger.info(f"Created Filter Entry: {filter_name}/{entry_name}")

            return True

        except Exception as e:
            logger.debug(f"Failed to sync Filter Entry {entry_data.get('name')}: {e}")
            return False


class ContractSyncModule(BaseSyncModule):
    """Sync ACI Contracts to NetBox."""

    @property
    def object_type(self) -> str:
        return "Contract"

    def fetch_from_aci(self) -> List[Dict[str, Any]]:
        """Fetch Contracts from ACI."""
        return self.aci.get_contracts()

    def sync_object(self, aci_data: Dict[str, Any]) -> bool:
        """Sync Contract to NetBox."""
        try:
            tenant_name = aci_data.get('tenant')
            if not tenant_name:
                logger.warning(f"Skipping contract without tenant: {aci_data}")
                return False

            tenant_map = self.context.get('tenant_map', {})
            tenant_id = tenant_map.get(tenant_name)
            
            if not tenant_id:
                logger.warning(f"Tenant {tenant_name} not found for contract")
                return False

            contract_name = aci_data.get('name')
            if not contract_name:
                logger.warning(f"Skipping contract without name: {aci_data}")
                return False

            # Prepare contract parameters
            contract_params = {}
            
            if aci_data.get('name_alias'):
                contract_params['name_alias'] = aci_data['name_alias']
            if aci_data.get('description'):
                contract_params['description'] = aci_data['description']
            if aci_data.get('scope'):
                contract_params['scope'] = aci_data['scope']
            if aci_data.get('prio'):
                contract_params['qos_class'] = aci_data['prio']
            if aci_data.get('target_dscp'):
                contract_params['target_dscp'] = aci_data['target_dscp']

            contract, created = self.netbox.get_or_create_contract(
                tenant_id=tenant_id,
                name=contract_name,
                **contract_params
            )

            if created:
                self.result.created += 1
                logger.info(f"Created Contract: {tenant_name}/{contract_name}")
            else:
                updates = {}
                field_checks = [
                    ('name_alias', 'name_alias'),
                    ('description', 'description'),
                    ('scope', 'scope'),
                    ('prio', 'qos_class'),
                    ('target_dscp', 'target_dscp'),
                ]
                
                for aci_field, nb_field in field_checks:
                    if aci_data.get(aci_field):
                        current = getattr(contract, nb_field, None)
                        if current != aci_data[aci_field]:
                            updates[nb_field] = aci_data[aci_field]

                if updates:
                    changed, verified = self.netbox.update_contract(
                        contract, updates, self.settings.verify_updates
                    )
                    if changed:
                        self.result.updated += 1
                        if verified:
                            self.result.verified += 1
                        logger.info(f"Updated Contract: {tenant_name}/{contract_name}")
                    else:
                        self.result.unchanged += 1
                else:
                    self.result.unchanged += 1

            # Store contract mapping in context
            contract_map = self.context.setdefault('contract_map', {})
            contract_map[f"{tenant_name}/{contract_name}"] = contract.id

            # Sync subjects
            subjects = aci_data.get('subjects', [])
            for subject_data in subjects:
                self._sync_subject(contract.id, tenant_name, contract_name, subject_data)

            return True

        except Exception as e:
            logger.error(f"Failed to sync Contract {aci_data.get('name')}: {e}")
            self.result.failed += 1
            self.result.errors.append(str(e))
            return False

    def _sync_subject(self, contract_id: int, tenant_name: str, 
                      contract_name: str, subject_data: Dict) -> bool:
        """Sync a contract subject."""
        try:
            subject_name = subject_data.get('name')
            if not subject_name:
                return False

            subject_params = {}
            if subject_data.get('description'):
                subject_params['description'] = subject_data['description']

            subject, created = self.netbox.get_or_create_contract_subject(
                contract_id=contract_id,
                name=subject_name,
                **subject_params
            )

            if created:
                logger.info(f"Created Contract Subject: {contract_name}/{subject_name}")
            else:
                # Check for updates
                updates = {}
                if subject_data.get('description'):
                    if getattr(subject, 'description', None) != subject_data['description']:
                        updates['description'] = subject_data['description']

                if updates:
                    self.netbox.update_contract_subject(subject, updates, self.settings.verify_updates)
                    logger.info(f"Updated Contract Subject: {contract_name}/{subject_name}")

            # Store subject mapping
            subject_map = self.context.setdefault('subject_map', {})
            subject_map[f"{tenant_name}/{contract_name}/{subject_name}"] = subject.id

            return True

        except Exception as e:
            logger.error(f"Failed to sync Contract Subject {subject_data.get('name')}: {e}")
            return False


class ContractRelationshipSyncModule(BaseSyncModule):
    """Sync ACI Contract Provider/Consumer relationships to NetBox."""

    @property
    def object_type(self) -> str:
        return "ContractRelationship"

    def fetch_from_aci(self) -> List[Dict[str, Any]]:
        """Fetch Contract relationships from ACI."""
        relationships = self.aci.get_contract_relationships()
        # Flatten providers and consumers into a single list for processing
        result = []
        vzany_count = 0
        epg_count = 0
        
        for prov in relationships.get('providers', []):
            prov['role'] = 'provider'
            result.append(prov)
            if prov.get('is_vzany'):
                vzany_count += 1
            else:
                epg_count += 1
                
        for cons in relationships.get('consumers', []):
            cons['role'] = 'consumer'
            result.append(cons)
            if cons.get('is_vzany'):
                vzany_count += 1
            else:
                epg_count += 1
        
        if vzany_count > 0:
            logger.info(f"Found {epg_count} EPG relationships and {vzany_count} vzAny relationships")
        
        return result

    def sync_object(self, aci_data: Dict[str, Any]) -> bool:
        """Sync contract relationship to NetBox."""
        try:
            contract_name = aci_data.get('contract')
            tenant_name = aci_data.get('tenant')
            role = aci_data.get('role')  # 'provider' or 'consumer'
            
            if not contract_name or not tenant_name or not role:
                return False
            
            # Map ACI role names to NetBox role values
            role_map = {
                'provider': 'prov',
                'consumer': 'cons'
            }
            netbox_role = role_map.get(role, role)

            # Find the tenant ID
            tenant_map = self.context.get('tenant_map', {})
            tenant_id = tenant_map.get(tenant_name)
            
            # Find the fabric ID
            fabric_map = self.context.get('fabric_map', {})
            fabric_id = None
            # Get first fabric if available
            if fabric_map:
                fabric_id = list(fabric_map.values())[0] if fabric_map else None

            # Find the contract ID - contracts may be in common tenant
            contract_map = self.context.get('contract_map', {})
            contract_id = contract_map.get(f"{tenant_name}/{contract_name}")
            
            # If not found in same tenant, try common tenant
            if not contract_id:
                contract_id = contract_map.get(f"common/{contract_name}")
                # If contract is in common tenant, use common tenant ID
                if contract_id:
                    tenant_id = tenant_map.get('common', tenant_id)
            
            if not contract_id:
                logger.debug(f"Contract {contract_name} not found for relationship")
                return False

            is_vzany = aci_data.get('is_vzany', False)
            
            if is_vzany:
                # vzAny relationship - uses VRF contract-relations endpoint
                vrf_name = aci_data.get('vrf')
                if not vrf_name:
                    return False
                
                vrf_map = self.context.get('vrf_map', {})
                vrf_id = vrf_map.get(f"{tenant_name}/{vrf_name}")
                
                if not vrf_id:
                    logger.debug(f"VRF {vrf_name} not found for vzAny relationship")
                    return False
                
                try:
                    # Create VRF contract relation
                    created = self.netbox.create_vrf_contract_relation(
                        vrf_id=vrf_id,
                        contract_id=contract_id,
                        role=netbox_role,
                        tenant_id=tenant_id
                    )
                    
                    if created:
                        self.result.created += 1
                        logger.info(f"Created vzAny {role}: VRF {vrf_name} -> {contract_name}")
                    else:
                        self.result.unchanged += 1
                except Exception as e:
                    logger.debug(f"Could not create VRF contract relation: {e}")
                    self.result.unchanged += 1
            else:
                # EPG relationship - uses contract relations endpoint
                ap_name = aci_data.get('ap')
                epg_name = aci_data.get('epg')
                
                if not ap_name or not epg_name:
                    return False
                
                # Find EPG ID
                epg_map = self.context.get('epg_map', {})
                epg_id = epg_map.get(f"{tenant_name}/{ap_name}/{epg_name}")
                
                if not epg_id:
                    logger.debug(f"EPG {epg_name} not found for contract relationship")
                    return False
                
                try:
                    # Create contract relation (EPG as provider/consumer)
                    created = self.netbox.create_contract_relation(
                        contract_id=contract_id,
                        epg_id=epg_id,
                        role=netbox_role,
                        tenant_id=tenant_id,
                        fabric_id=fabric_id
                    )
                    
                    if created:
                        self.result.created += 1
                        logger.info(f"Created {role}: EPG {epg_name} -> {contract_name}")
                    else:
                        self.result.unchanged += 1
                except Exception as e:
                    logger.debug(f"Could not create contract relation: {e}")
                    self.result.unchanged += 1

            return True

        except Exception as e:
            logger.debug(f"Failed to sync contract relationship: {e}")
            self.result.unchanged += 1
            return True  # Don't fail the whole sync
