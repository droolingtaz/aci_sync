"""
Contract Sync Module - Synchronize ACI Contracts, Subjects, and Filters to NetBox.

Optimized with:
- FIELD_MAP / _build_updates for DRY field comparison
- Pre-fetched contract relations cache (avoids O(n²) lookups)
- Per-tenant pre-fetch caching for contracts and filters
"""

import logging
from typing import Any, Dict, List

from .base import BaseSyncModule

logger = logging.getLogger(__name__)


class ContractFilterSyncModule(BaseSyncModule):
    """Sync ACI Contract Filters to NetBox."""

    FIELD_MAP = {
        'name_alias': 'name_alias',
        'description': 'description',
    }

    @property
    def object_type(self) -> str:
        return "ContractFilter"

    def pre_sync(self) -> None:
        """Pre-fetch existing filters per tenant."""
        self._tenant_filter_caches: Dict[int, Dict] = {}
        tenant_map = self.context.get('tenant_map', {})
        for tenant_name, tenant_id in tenant_map.items():
            cache = self.netbox.fetch_all_contract_filters(tenant_id)
            self._tenant_filter_caches[tenant_id] = cache
            logger.debug(f"Pre-fetched {len(cache)} filters for tenant {tenant_name}")

    def fetch_from_aci(self) -> List[Dict[str, Any]]:
        return self.aci.get_contract_filters()

    def sync_object(self, aci_data: Dict[str, Any]) -> bool:
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

            filter_params = self._build_params(aci_data)

            cache = self._tenant_filter_caches.get(tenant_id, {})
            flt, created = self.netbox.get_or_create_filter_cached(
                cache, filter_name,
                tenant_id=tenant_id, **filter_params
            )

            if created:
                self.result.created += 1
                logger.info(f"Created Contract Filter: {tenant_name}/{filter_name}")
            else:
                updates = self._build_updates(flt, aci_data)
                self._apply_updates(
                    flt, updates,
                    f"{tenant_name}/{filter_name}",
                    self.netbox.update_contract_filter,
                )

            filter_map = self.context.setdefault('filter_map', {})
            filter_map[f"{tenant_name}/{filter_name}"] = flt.id

            # Sync filter entries
            for entry_data in aci_data.get('entries', []):
                self._sync_filter_entry(flt.id, tenant_name, filter_name, entry_data)

            return True

        except Exception as e:
            logger.error(f"Failed to sync Contract Filter {aci_data.get('name')}: {e}")
            self.result.failed += 1
            self.result.errors.append(str(e))
            return False

    def _sync_filter_entry(self, filter_id: int, tenant_name: str,
                           filter_name: str, entry_data: Dict) -> bool:
        try:
            entry_name = entry_data.get('name')
            if not entry_name:
                return False

            ENTRY_FIELDS = {
                'etherT': 'ether_type',
                'prot': 'ip_protocol',
                'dFromPort': 'destination_port_from',
                'dToPort': 'destination_port_to',
                'sFromPort': 'source_port_from',
                'sToPort': 'source_port_to',
            }

            entry_params = {}
            for aci_field, nb_field in ENTRY_FIELDS.items():
                val = entry_data.get(aci_field)
                if val and val != 'unspecified':
                    entry_params[nb_field] = val

            entry, created = self.netbox.get_or_create_filter_entry(
                filter_id=filter_id, name=entry_name, **entry_params
            )
            if created:
                logger.info(f"Created Filter Entry: {filter_name}/{entry_name}")
            return True

        except Exception as e:
            logger.debug(f"Failed to sync Filter Entry {entry_data.get('name')}: {e}")
            return False


class ContractSyncModule(BaseSyncModule):
    """Sync ACI Contracts to NetBox."""

    FIELD_MAP = {
        'name_alias': 'name_alias',
        'description': 'description',
        'scope': 'scope',
        'prio': 'qos_class',
        'target_dscp': 'target_dscp',
    }

    @property
    def object_type(self) -> str:
        return "Contract"

    def pre_sync(self) -> None:
        """Pre-fetch existing contracts per tenant."""
        self._tenant_contract_caches: Dict[int, Dict] = {}
        tenant_map = self.context.get('tenant_map', {})
        for tenant_name, tenant_id in tenant_map.items():
            cache = self.netbox.fetch_all_contracts(tenant_id)
            self._tenant_contract_caches[tenant_id] = cache
            logger.debug(f"Pre-fetched {len(cache)} contracts for tenant {tenant_name}")

    def fetch_from_aci(self) -> List[Dict[str, Any]]:
        return self.aci.get_contracts()

    def sync_object(self, aci_data: Dict[str, Any]) -> bool:
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

            contract_params = self._build_params(aci_data)

            cache = self._tenant_contract_caches.get(tenant_id, {})
            contract, created = self.netbox.get_or_create_contract_cached(
                cache, contract_name,
                tenant_id=tenant_id, **contract_params
            )

            if created:
                self.result.created += 1
                logger.info(f"Created Contract: {tenant_name}/{contract_name}")
            else:
                updates = self._build_updates(contract, aci_data)
                self._apply_updates(
                    contract, updates,
                    f"{tenant_name}/{contract_name}",
                    self.netbox.update_contract,
                )

            contract_map = self.context.setdefault('contract_map', {})
            contract_map[f"{tenant_name}/{contract_name}"] = contract.id

            # Sync subjects
            for subject_data in aci_data.get('subjects', []):
                self._sync_subject(contract.id, tenant_name, contract_name, subject_data)

            return True

        except Exception as e:
            logger.error(f"Failed to sync Contract {aci_data.get('name')}: {e}")
            self.result.failed += 1
            self.result.errors.append(str(e))
            return False

    def _sync_subject(self, contract_id: int, tenant_name: str,
                      contract_name: str, subject_data: Dict) -> bool:
        try:
            subject_name = subject_data.get('name')
            if not subject_name:
                return False

            subject_params = {}
            if subject_data.get('description'):
                subject_params['description'] = subject_data['description']

            subject, created = self.netbox.get_or_create_contract_subject(
                contract_id=contract_id, name=subject_name, **subject_params
            )

            if created:
                logger.info(f"Created Contract Subject: {contract_name}/{subject_name}")
            else:
                if subject_data.get('description'):
                    if getattr(subject, 'description', None) != subject_data['description']:
                        self.netbox.update_contract_subject(
                            subject,
                            {'description': subject_data['description']},
                            self.settings.verify_updates,
                        )
                        logger.info(f"Updated Contract Subject: {contract_name}/{subject_name}")

            subject_map = self.context.setdefault('subject_map', {})
            subject_map[f"{tenant_name}/{contract_name}/{subject_name}"] = subject.id
            return True

        except Exception as e:
            logger.error(f"Failed to sync Contract Subject {subject_data.get('name')}: {e}")
            return False


class ContractRelationshipSyncModule(BaseSyncModule):
    """
    Sync ACI Contract Provider/Consumer relationships to NetBox.

    Optimized: pre-fetches all contract relations once in pre_sync()
    instead of querying per relationship (was O(n²), now O(n)).
    """

    @property
    def object_type(self) -> str:
        return "ContractRelationship"

    def pre_sync(self) -> None:
        """Pre-fetch all contract relations into the NetBox client cache."""
        # Force the cache to populate before we start syncing
        self.netbox._fetch_contract_relations()
        count = len(self.netbox._contract_relations_cache or [])
        logger.info(f"Pre-fetched {count} existing contract relations")

    def fetch_from_aci(self) -> List[Dict[str, Any]]:
        relationships = self.aci.get_contract_relationships()
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
        try:
            contract_name = aci_data.get('contract')
            tenant_name = aci_data.get('tenant')
            role = aci_data.get('role')
            if not contract_name or not tenant_name or not role:
                return False

            role_map = {'provider': 'prov', 'consumer': 'cons'}
            netbox_role = role_map.get(role, role)

            tenant_map = self.context.get('tenant_map', {})
            tenant_id = tenant_map.get(tenant_name)

            fabric_map = self.context.get('fabric_map', {})
            fabric_id = list(fabric_map.values())[0] if fabric_map else None

            contract_map = self.context.get('contract_map', {})
            contract_id = contract_map.get(f"{tenant_name}/{contract_name}")

            if not contract_id:
                contract_id = contract_map.get(f"common/{contract_name}")
                if contract_id:
                    tenant_id = tenant_map.get('common', tenant_id)

            if not contract_id:
                logger.debug(f"Contract {contract_name} not found for relationship")
                return False

            is_vzany = aci_data.get('is_vzany', False)

            if is_vzany:
                vrf_name = aci_data.get('vrf')
                if not vrf_name:
                    return False

                vrf_map = self.context.get('vrf_map', {})
                vrf_id = vrf_map.get(f"{tenant_name}/{vrf_name}")
                if not vrf_id:
                    logger.debug(f"VRF {vrf_name} not found for vzAny relationship")
                    return False

                try:
                    created = self.netbox.create_vrf_contract_relation(
                        vrf_id=vrf_id, contract_id=contract_id,
                        role=netbox_role, tenant_id=tenant_id,
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
                ap_name = aci_data.get('ap')
                epg_name = aci_data.get('epg')
                if not ap_name or not epg_name:
                    return False

                epg_map = self.context.get('epg_map', {})
                epg_id = epg_map.get(f"{tenant_name}/{ap_name}/{epg_name}")
                if not epg_id:
                    logger.debug(f"EPG {epg_name} not found for contract relationship")
                    return False

                try:
                    created = self.netbox.create_contract_relation(
                        contract_id=contract_id, epg_id=epg_id,
                        role=netbox_role, tenant_id=tenant_id,
                        fabric_id=fabric_id,
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