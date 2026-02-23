"""
ACI Client - Cobra SDK wrapper with connection management.
Provides efficient data retrieval from Cisco ACI using Cobra SDK.
"""

import logging
from typing import Any, Dict, List, Optional, Generator
from functools import lru_cache
import urllib3

# Disable SSL warnings if needed
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

# Cobra SDK imports
COBRA_AVAILABLE = False
COBRA_IMPORT_ERROR = None

try:
    from cobra.mit.access import MoDirectory
    from cobra.mit.session import LoginSession
    from cobra.mit.request import DnQuery, ClassQuery
    COBRA_AVAILABLE = True
except ImportError as e:
    COBRA_IMPORT_ERROR = str(e)
    MoDirectory = None
    LoginSession = None
    DnQuery = None
    ClassQuery = None
    logger.warning(f"Cobra SDK (acicobra) not available: {e}")

# Model imports are optional - we use class queries by string name
# so we don't strictly need the model classes imported
try:
    from cobra.model import fv, vz, fabric, infra
    MODELS_AVAILABLE = True
except ImportError as e:
    MODELS_AVAILABLE = False
    logger.debug(f"Cobra model classes not imported (this is OK): {e}")


class ACIClient:
    """
    ACI Client using Cobra SDK for efficient data retrieval.
    Supports connection pooling and retry logic.
    """

    def __init__(self, host: str, username: str, password: str, 
                 verify_ssl: bool = False, timeout: int = 30):
        self.host = host
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.timeout = timeout
        self._session = None  # LoginSession instance
        self._modir = None    # MoDirectory instance
        self._connected = False

    def connect(self) -> bool:
        """Establish connection to APIC."""
        if not COBRA_AVAILABLE:
            logger.error(f"Cobra SDK not available. Import error: {COBRA_IMPORT_ERROR}")
            logger.error("Please install acicobra package from your APIC or Cisco DevNet")
            return False

        try:
            url = f"https://{self.host}"
            self._session = LoginSession(
                url, self.username, self.password,
                secure=self.verify_ssl, timeout=self.timeout
            )
            self._modir = MoDirectory(self._session)
            self._modir.login()
            self._connected = True
            logger.info(f"Connected to ACI APIC at {self.host}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to ACI: {e}")
            self._connected = False
            return False

    def disconnect(self) -> None:
        """Close connection to APIC."""
        if self._modir and self._connected:
            try:
                self._modir.logout()
            except Exception as e:
                logger.warning(f"Error during logout: {e}")
            finally:
                self._connected = False
                logger.info("Disconnected from ACI APIC")

    def __enter__(self) -> "ACIClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.disconnect()

    def _query_class(self, class_name: str, subtree: Optional[str] = None, 
                     prop_filter: Optional[str] = None) -> List[Any]:
        """Execute a class query and return results."""
        if not self._connected or not self._modir:
            raise RuntimeError("Not connected to ACI")

        query = ClassQuery(class_name)
        # Only set subtree if explicitly requested - many classes don't support it
        if subtree:
            query.subtree = subtree
        if prop_filter:
            query.propFilter = prop_filter
        
        try:
            return list(self._modir.query(query))
        except Exception as e:
            logger.error(f"Query failed for class {class_name}: {e}")
            return []

    def _query_dn(self, dn: str, subtree: Optional[str] = None) -> Optional[Any]:
        """Execute a DN query and return result."""
        if not self._connected or not self._modir:
            raise RuntimeError("Not connected to ACI")

        query = DnQuery(dn)
        if subtree:
            query.subtree = subtree
        
        try:
            result = self._modir.query(query)
            return result[0] if result else None
        except Exception as e:
            logger.error(f"Query failed for DN {dn}: {e}")
            return None

    # Fabric Information Methods
    def get_fabric_settings(self) -> Dict[str, Any]:
        """Get fabric-wide settings including fabric ID, infra VLAN, GIPO pool."""
        fabric_data = {
            'fabric_id': 1,  # Default fabric ID
            'infra_vlan_id': None,
            'gipo_pool': None,
            'name': 'ACI Fabric',  # Default name
        }

        try:
            # Fabric settings from infraSetPol
            infra_set = self._query_class("infraSetPol")
            if infra_set:
                for pol in infra_set:
                    if hasattr(pol, 'fabricId') and pol.fabricId:
                        fabric_data['fabric_id'] = int(pol.fabricId)

            # Infra VLAN from infraProvAcc or infraCont
            infra_vlan = self._query_class("infraProvAcc")
            if infra_vlan:
                for acc in infra_vlan:
                    if hasattr(acc, 'vid') and acc.vid:
                        fabric_data['infra_vlan_id'] = int(acc.vid)
                        break
            
            # If not found, try infraCont
            if not fabric_data['infra_vlan_id']:
                infra_cont = self._query_class("infraCont")
                if infra_cont:
                    for cont in infra_cont:
                        if hasattr(cont, 'infraVlan') and cont.infraVlan:
                            fabric_data['infra_vlan_id'] = int(cont.infraVlan)
                            break

            # Get fabric name - try different approaches
            # First try fabricSetupP
            setup_pol = self._query_class("fabricSetupP")
            if setup_pol:
                for pol in setup_pol:
                    if hasattr(pol, 'name') and pol.name:
                        fabric_data['name'] = str(pol.name)
                        break
            
            # Get GIPO pool from fvFabricExtConnP or similar
            # GIPO is used for external multicast routing
            try:
                gipo_pol = self._query_class("fvFabricExtConnP")
                if gipo_pol:
                    for pol in gipo_pol:
                        if hasattr(pol, 'gipoPool') and pol.gipoPool:
                            fabric_data['gipo_pool'] = str(pol.gipoPool)
                            break
            except Exception as e:
                logger.debug(f"Could not fetch GIPO pool: {e}")

        except Exception as e:
            logger.error(f"Error retrieving fabric settings: {e}")

        return fabric_data

    def get_fabric_pods(self) -> List[Dict[str, Any]]:
        """Get all fabric pods with TEP pool information."""
        pods = []
        try:
            pod_objs = self._query_class("fabricPod")
            for pod in pod_objs:
                pod_data = {
                    'pod_id': int(pod.id) if hasattr(pod, 'id') else None,
                    'name': f"pod-{pod.id}" if hasattr(pod, 'id') else None,
                    'dn': str(pod.dn) if hasattr(pod, 'dn') else None,
                    'tep_pool': None,
                }
                
                # Try to get TEP pool from the pod
                if hasattr(pod, 'tepPool') and pod.tepPool:
                    pod_data['tep_pool'] = str(pod.tepPool)
                
                pods.append(pod_data)
            
            # If TEP pool not found on pod objects, try fabricSetupP
            if pods and not any(p.get('tep_pool') for p in pods):
                try:
                    setup_pol = self._query_class("fabricSetupP")
                    if setup_pol:
                        for pol in setup_pol:
                            if hasattr(pol, 'tepPool') and pol.tepPool:
                                # Apply to all pods (usually same TEP pool)
                                for pod in pods:
                                    if not pod.get('tep_pool'):
                                        pod['tep_pool'] = str(pol.tepPool)
                                break
                except Exception as e:
                    logger.debug(f"Could not fetch TEP pool from fabricSetupP: {e}")
                    
        except Exception as e:
            logger.error(f"Error retrieving fabric pods: {e}")
        return pods

    def get_fabric_nodes(self) -> List[Dict[str, Any]]:
        """Get all fabric nodes (spines, leaves, controllers)."""
        nodes = []
        try:
            node_objs = self._query_class("fabricNode")
            for node in node_objs:
                # Extract pod_id from DN (topology/pod-1/node-101)
                dn = str(node.dn) if hasattr(node, 'dn') else ''
                pod_id = None
                if 'pod-' in dn:
                    try:
                        pod_part = dn.split('pod-')[1].split('/')[0]
                        pod_id = int(pod_part)
                    except (IndexError, ValueError):
                        pod_id = 1  # Default to pod 1
                else:
                    pod_id = 1  # Default to pod 1
                
                nodes.append({
                    'node_id': int(node.id) if hasattr(node, 'id') else None,
                    'name': str(node.name) if hasattr(node, 'name') else None,
                    'serial': str(node.serial) if hasattr(node, 'serial') and node.serial else None,
                    'model': str(node.model) if hasattr(node, 'model') and node.model else None,
                    'role': str(node.role) if hasattr(node, 'role') else None,
                    'pod_id': pod_id,
                    'fabric_st': str(node.fabricSt) if hasattr(node, 'fabricSt') else None,
                    'address': str(node.address) if hasattr(node, 'address') else None,
                    'version': str(node.version) if hasattr(node, 'version') else None,
                    'dn': dn,
                })
        except Exception as e:
            logger.error(f"Error retrieving fabric nodes: {e}")
        return nodes

    # Tenant Methods
    def get_tenants(self) -> List[Dict[str, Any]]:
        """Get all tenants with their attributes."""
        tenants = []
        try:
            tenant_objs = self._query_class("fvTenant")
            for tn in tenant_objs:
                tenants.append({
                    'name': str(tn.name),
                    'dn': str(tn.dn),
                    'name_alias': str(tn.nameAlias) if hasattr(tn, 'nameAlias') and tn.nameAlias else None,
                    'description': str(tn.descr) if hasattr(tn, 'descr') and tn.descr else None,
                })
        except Exception as e:
            logger.error(f"Error retrieving tenants: {e}")
        return tenants

    # VRF Methods
    def get_vrfs(self) -> List[Dict[str, Any]]:
        """Get all VRFs (Contexts) with their attributes."""
        vrfs = []
        try:
            vrf_objs = self._query_class("fvCtx")
            for vrf in vrf_objs:
                # Extract tenant name from DN
                dn_parts = str(vrf.dn).split('/')
                tenant_name = dn_parts[1].replace('tn-', '') if len(dn_parts) > 1 else None

                vrfs.append({
                    'name': str(vrf.name),
                    'dn': str(vrf.dn),
                    'tenant': tenant_name,
                    'name_alias': str(vrf.nameAlias) if hasattr(vrf, 'nameAlias') and vrf.nameAlias else None,
                    'description': str(vrf.descr) if hasattr(vrf, 'descr') and vrf.descr else None,
                    'bd_enforced_enabled': str(vrf.bdEnforcedEnable) == 'yes' if hasattr(vrf, 'bdEnforcedEnable') else False,
                    'ip_data_plane_learning': str(vrf.ipDataPlaneLearning) if hasattr(vrf, 'ipDataPlaneLearning') else 'enabled',
                    'pc_enf_dir': str(vrf.pcEnfDir) if hasattr(vrf, 'pcEnfDir') else 'ingress',
                    'pc_enf_pref': str(vrf.pcEnfPref) if hasattr(vrf, 'pcEnfPref') else 'enforced',
                    'pim_v4_enabled': str(vrf.knwMcastAct) == 'permit' if hasattr(vrf, 'knwMcastAct') else False,
                    'pim_v6_enabled': False,  # Requires additional query
                    'preferred_group': str(vrf.vrfPref) == 'enabled' if hasattr(vrf, 'vrfPref') else False,
                })
        except Exception as e:
            logger.error(f"Error retrieving VRFs: {e}")
        return vrfs

    # Bridge Domain Methods
    def get_bridge_domains(self) -> List[Dict[str, Any]]:
        """Get all Bridge Domains with their attributes."""
        bds = []
        try:
            bd_objs = self._query_class("fvBD", subtree="children")
            for bd in bd_objs:
                # Extract tenant and VRF from DN
                dn_parts = str(bd.dn).split('/')
                tenant_name = dn_parts[1].replace('tn-', '') if len(dn_parts) > 1 else None

                # Get associated VRF - extract both VRF name and its tenant
                vrf_name = None
                vrf_tenant = None
                if hasattr(bd, 'children'):
                    for child in bd.children:
                        if child.__class__.__name__ == 'RsCtx':
                            vrf_dn = str(child.tDn) if hasattr(child, 'tDn') else None
                            if vrf_dn:
                                # Parse DN like "uni/tn-common/ctx-default"
                                vrf_dn_parts = vrf_dn.split('/')
                                for part in vrf_dn_parts:
                                    if part.startswith('tn-'):
                                        vrf_tenant = part.replace('tn-', '')
                                    elif part.startswith('ctx-'):
                                        vrf_name = part.replace('ctx-', '')

                bds.append({
                    'name': str(bd.name),
                    'dn': str(bd.dn),
                    'tenant': tenant_name,
                    'vrf': vrf_name,
                    'vrf_tenant': vrf_tenant,  # The tenant where the VRF actually lives
                    'name_alias': str(bd.nameAlias) if hasattr(bd, 'nameAlias') and bd.nameAlias else None,
                    'description': str(bd.descr) if hasattr(bd, 'descr') and bd.descr else None,
                    'arp_flood': str(bd.arpFlood) == 'yes' if hasattr(bd, 'arpFlood') else False,
                    'ep_move_detect': str(bd.epMoveDetectMode) if hasattr(bd, 'epMoveDetectMode') else None,
                    'ip_learning': str(bd.ipLearning) == 'yes' if hasattr(bd, 'ipLearning') else True,
                    'limit_ip_learn': str(bd.limitIpLearnToSubnets) == 'yes' if hasattr(bd, 'limitIpLearnToSubnets') else True,
                    'mac': str(bd.mac) if hasattr(bd, 'mac') else '00:22:BD:F8:19:FF',
                    'multi_dest_pkt_act': str(bd.multiDstPktAct) if hasattr(bd, 'multiDstPktAct') else 'bd-flood',
                    'unicast_route': str(bd.unicastRoute) == 'yes' if hasattr(bd, 'unicastRoute') else True,
                    'unk_mac_ucast_act': str(bd.unkMacUcastAct) if hasattr(bd, 'unkMacUcastAct') else 'proxy',
                    'unk_mcast_act': str(bd.unkMcastAct) if hasattr(bd, 'unkMcastAct') else 'flood',
                    'v6_unk_mcast_act': str(bd.v6unkMcastAct) if hasattr(bd, 'v6unkMcastAct') else 'flood',
                    'vmac': str(bd.vmac) if hasattr(bd, 'vmac') and bd.vmac else None,
                    'pim_v4_enabled': str(bd.mcastAllow) == 'yes' if hasattr(bd, 'mcastAllow') else False,
                    'host_route_adv': str(bd.hostBasedRouting) == 'yes' if hasattr(bd, 'hostBasedRouting') else False,
                })
        except Exception as e:
            logger.error(f"Error retrieving Bridge Domains: {e}")
        return bds

    # Subnet Methods
    def get_subnets(self) -> List[Dict[str, Any]]:
        """Get all Bridge Domain Subnets."""
        subnets = []
        try:
            subnet_objs = self._query_class("fvSubnet")
            for subnet in subnet_objs:
                # Extract BD and tenant from DN
                dn_parts = str(subnet.dn).split('/')
                tenant_name = dn_parts[1].replace('tn-', '') if len(dn_parts) > 1 else None
                bd_name = None
                for part in dn_parts:
                    if part.startswith('BD-'):
                        bd_name = part.replace('BD-', '')
                        break

                subnets.append({
                    'ip': str(subnet.ip),
                    'dn': str(subnet.dn),
                    'tenant': tenant_name,
                    'bridge_domain': bd_name,
                    'name': str(subnet.name) if hasattr(subnet, 'name') and subnet.name else None,
                    'name_alias': str(subnet.nameAlias) if hasattr(subnet, 'nameAlias') and subnet.nameAlias else None,
                    'description': str(subnet.descr) if hasattr(subnet, 'descr') and subnet.descr else None,
                    'preferred': str(subnet.preferred) == 'yes' if hasattr(subnet, 'preferred') else False,
                    'scope': str(subnet.scope) if hasattr(subnet, 'scope') else 'private',
                    'virtual': str(subnet.virtual) == 'yes' if hasattr(subnet, 'virtual') else False,
                    'ctrl': str(subnet.ctrl) if hasattr(subnet, 'ctrl') else None,
                })
        except Exception as e:
            logger.error(f"Error retrieving Subnets: {e}")
        return subnets

    # Application Profile Methods
    def get_app_profiles(self) -> List[Dict[str, Any]]:
        """Get all Application Profiles."""
        aps = []
        try:
            ap_objs = self._query_class("fvAp")
            for ap in ap_objs:
                # Extract tenant from DN
                dn_parts = str(ap.dn).split('/')
                tenant_name = dn_parts[1].replace('tn-', '') if len(dn_parts) > 1 else None

                aps.append({
                    'name': str(ap.name),
                    'dn': str(ap.dn),
                    'tenant': tenant_name,
                    'name_alias': str(ap.nameAlias) if hasattr(ap, 'nameAlias') and ap.nameAlias else None,
                    'description': str(ap.descr) if hasattr(ap, 'descr') and ap.descr else None,
                })
        except Exception as e:
            logger.error(f"Error retrieving Application Profiles: {e}")
        return aps

    # EPG Methods
    def get_epgs(self) -> List[Dict[str, Any]]:
        """Get all Endpoint Groups with their attributes."""
        epgs = []
        try:
            epg_objs = self._query_class("fvAEPg", subtree="children")
            for epg in epg_objs:
                # Extract tenant and AP from DN
                dn_parts = str(epg.dn).split('/')
                tenant_name = dn_parts[1].replace('tn-', '') if len(dn_parts) > 1 else None
                ap_name = None
                for part in dn_parts:
                    if part.startswith('ap-'):
                        ap_name = part.replace('ap-', '')
                        break

                # Get associated BD
                bd_name = None
                if hasattr(epg, 'children'):
                    for child in epg.children:
                        if child.__class__.__name__ == 'RsBd':
                            bd_name = str(child.tnFvBDName) if hasattr(child, 'tnFvBDName') else None

                epgs.append({
                    'name': str(epg.name),
                    'dn': str(epg.dn),
                    'tenant': tenant_name,
                    'app_profile': ap_name,
                    'bridge_domain': bd_name,
                    'name_alias': str(epg.nameAlias) if hasattr(epg, 'nameAlias') and epg.nameAlias else None,
                    'description': str(epg.descr) if hasattr(epg, 'descr') and epg.descr else None,
                    'pref_gr_memb': str(epg.prefGrMemb) if hasattr(epg, 'prefGrMemb') else 'exclude',
                    'prio': str(epg.prio) if hasattr(epg, 'prio') else 'unspecified',
                    'pc_enf_pref': str(epg.pcEnfPref) if hasattr(epg, 'pcEnfPref') else 'unenforced',
                    'flood_on_encap': str(epg.floodOnEncap) == 'enabled' if hasattr(epg, 'floodOnEncap') else False,
                    'is_attr_based_epg': str(epg.isAttrBasedEPg) == 'yes' if hasattr(epg, 'isAttrBasedEPg') else False,
                    'shutdown': str(epg.shutdown) == 'yes' if hasattr(epg, 'shutdown') else False,
                })
        except Exception as e:
            logger.error(f"Error retrieving EPGs: {e}")
        return epgs

    # ESG Methods
    def get_esgs(self) -> List[Dict[str, Any]]:
        """Get all Endpoint Security Groups."""
        esgs = []
        try:
            esg_objs = self._query_class("fvESg", subtree="children")
            for esg in esg_objs:
                # Extract tenant and AP from DN
                dn_parts = str(esg.dn).split('/')
                tenant_name = dn_parts[1].replace('tn-', '') if len(dn_parts) > 1 else None
                ap_name = None
                for part in dn_parts:
                    if part.startswith('ap-'):
                        ap_name = part.replace('ap-', '')
                        break

                # Get associated VRF
                vrf_name = None
                if hasattr(esg, 'children'):
                    for child in esg.children:
                        if child.__class__.__name__ == 'RsScope':
                            vrf_dn = str(child.tDn) if hasattr(child, 'tDn') else None
                            if vrf_dn:
                                vrf_name = vrf_dn.split('/')[-1].replace('ctx-', '')

                esgs.append({
                    'name': str(esg.name),
                    'dn': str(esg.dn),
                    'tenant': tenant_name,
                    'app_profile': ap_name,
                    'vrf': vrf_name,
                    'name_alias': str(esg.nameAlias) if hasattr(esg, 'nameAlias') and esg.nameAlias else None,
                    'description': str(esg.descr) if hasattr(esg, 'descr') and esg.descr else None,
                    'pref_gr_memb': str(esg.prefGrMemb) if hasattr(esg, 'prefGrMemb') else 'exclude',
                    'prio': str(esg.prio) if hasattr(esg, 'prio') else 'unspecified',
                    'shutdown': str(esg.shutdown) == 'yes' if hasattr(esg, 'shutdown') else False,
                })
        except Exception as e:
            logger.error(f"Error retrieving ESGs: {e}")
        return esgs

    # Contract Methods
    def get_contracts(self) -> List[Dict[str, Any]]:
        """Get all Contracts with subjects and filters."""
        contracts = []
        try:
            contract_objs = self._query_class("vzBrCP", subtree="children")
            for contract in contract_objs:
                # Extract tenant from DN
                dn_parts = str(contract.dn).split('/')
                tenant_name = dn_parts[1].replace('tn-', '') if len(dn_parts) > 1 else None

                # Get subjects
                subjects = []
                if hasattr(contract, 'children'):
                    for child in contract.children:
                        if child.__class__.__name__ == 'Subj':
                            subjects.append({
                                'name': str(child.name),
                                'description': str(child.descr) if hasattr(child, 'descr') and child.descr else None,
                            })

                contracts.append({
                    'name': str(contract.name),
                    'dn': str(contract.dn),
                    'tenant': tenant_name,
                    'name_alias': str(contract.nameAlias) if hasattr(contract, 'nameAlias') and contract.nameAlias else None,
                    'description': str(contract.descr) if hasattr(contract, 'descr') and contract.descr else None,
                    'scope': str(contract.scope) if hasattr(contract, 'scope') else 'context',
                    'prio': str(contract.prio) if hasattr(contract, 'prio') else 'unspecified',
                    'target_dscp': str(contract.targetDscp) if hasattr(contract, 'targetDscp') else 'unspecified',
                    'subjects': subjects,
                })
        except Exception as e:
            logger.error(f"Error retrieving Contracts: {e}")
        return contracts

    def get_contract_relationships(self) -> Dict[str, Any]:
        """Get contract provider/consumer relationships from EPGs and vzAny."""
        relationships = {
            'providers': [],  # List of {contract, epg/vzany, tenant, ap}
            'consumers': [],
        }
        try:
            # Get EPG contract providers (fvRsProv)
            prov_objs = self._query_class("fvRsProv")
            for prov in prov_objs:
                dn = str(prov.dn)
                # DN format: uni/tn-{tenant}/ap-{ap}/epg-{epg}/rsprov-{contract}
                dn_parts = dn.split('/')
                tenant_name = None
                ap_name = None
                epg_name = None
                
                for part in dn_parts:
                    if part.startswith('tn-'):
                        tenant_name = part[3:]
                    elif part.startswith('ap-'):
                        ap_name = part[3:]
                    elif part.startswith('epg-'):
                        epg_name = part[4:]
                
                contract_name = str(prov.tnVzBrCPName) if hasattr(prov, 'tnVzBrCPName') else None
                
                if contract_name and epg_name:
                    relationships['providers'].append({
                        'contract': contract_name,
                        'tenant': tenant_name,
                        'ap': ap_name,
                        'epg': epg_name,
                        'vrf': None,
                        'is_vzany': False,
                    })

            # Get EPG contract consumers (fvRsCons)
            cons_objs = self._query_class("fvRsCons")
            for cons in cons_objs:
                dn = str(cons.dn)
                dn_parts = dn.split('/')
                tenant_name = None
                ap_name = None
                epg_name = None
                
                for part in dn_parts:
                    if part.startswith('tn-'):
                        tenant_name = part[3:]
                    elif part.startswith('ap-'):
                        ap_name = part[3:]
                    elif part.startswith('epg-'):
                        epg_name = part[4:]
                
                contract_name = str(cons.tnVzBrCPName) if hasattr(cons, 'tnVzBrCPName') else None
                
                if contract_name and epg_name:
                    relationships['consumers'].append({
                        'contract': contract_name,
                        'tenant': tenant_name,
                        'ap': ap_name,
                        'epg': epg_name,
                        'vrf': None,
                        'is_vzany': False,
                    })

            # Get vzAny contract providers (vzRsAnyToProv)
            try:
                vzany_prov_objs = self._query_class("vzRsAnyToProv")
                for prov in vzany_prov_objs:
                    dn = str(prov.dn)
                    # DN format: uni/tn-{tenant}/ctx-{vrf}/any/rsanyToProv-{contract}
                    dn_parts = dn.split('/')
                    tenant_name = None
                    vrf_name = None
                    
                    for part in dn_parts:
                        if part.startswith('tn-'):
                            tenant_name = part[3:]
                        elif part.startswith('ctx-'):
                            vrf_name = part[4:]
                    
                    contract_name = str(prov.tnVzBrCPName) if hasattr(prov, 'tnVzBrCPName') else None
                    
                    if contract_name and vrf_name:
                        relationships['providers'].append({
                            'contract': contract_name,
                            'tenant': tenant_name,
                            'ap': None,
                            'epg': None,
                            'vrf': vrf_name,
                            'is_vzany': True,
                        })
                        logger.debug(f"Found vzAny provider: VRF {vrf_name} -> {contract_name}")
            except Exception as e:
                logger.debug(f"Could not query vzRsAnyToProv: {e}")

            # Get vzAny contract consumers (vzRsAnyToCons)
            try:
                vzany_cons_objs = self._query_class("vzRsAnyToCons")
                for cons in vzany_cons_objs:
                    dn = str(cons.dn)
                    # DN format: uni/tn-{tenant}/ctx-{vrf}/any/rsanyToCons-{contract}
                    dn_parts = dn.split('/')
                    tenant_name = None
                    vrf_name = None
                    
                    for part in dn_parts:
                        if part.startswith('tn-'):
                            tenant_name = part[3:]
                        elif part.startswith('ctx-'):
                            vrf_name = part[4:]
                    
                    contract_name = str(cons.tnVzBrCPName) if hasattr(cons, 'tnVzBrCPName') else None
                    
                    if contract_name and vrf_name:
                        relationships['consumers'].append({
                            'contract': contract_name,
                            'tenant': tenant_name,
                            'ap': None,
                            'epg': None,
                            'vrf': vrf_name,
                            'is_vzany': True,
                        })
                        logger.debug(f"Found vzAny consumer: VRF {vrf_name} -> {contract_name}")
            except Exception as e:
                logger.debug(f"Could not query vzRsAnyToCons: {e}")

        except Exception as e:
            logger.error(f"Error retrieving contract relationships: {e}")
        return relationships

    def get_contract_filters(self) -> List[Dict[str, Any]]:
        """Get all Contract Filters with entries."""
        filters = []
        try:
            filter_objs = self._query_class("vzFilter", subtree="children")
            for flt in filter_objs:
                # Extract tenant from DN
                dn_parts = str(flt.dn).split('/')
                tenant_name = dn_parts[1].replace('tn-', '') if len(dn_parts) > 1 else None

                # Get filter entries
                entries = []
                if hasattr(flt, 'children'):
                    for child in flt.children:
                        if child.__class__.__name__ == 'Entry':
                            entries.append({
                                'name': str(child.name),
                                'etherT': str(child.etherT) if hasattr(child, 'etherT') else 'unspecified',
                                'prot': str(child.prot) if hasattr(child, 'prot') else 'unspecified',
                                'dFromPort': str(child.dFromPort) if hasattr(child, 'dFromPort') else 'unspecified',
                                'dToPort': str(child.dToPort) if hasattr(child, 'dToPort') else 'unspecified',
                                'sFromPort': str(child.sFromPort) if hasattr(child, 'sFromPort') else 'unspecified',
                                'sToPort': str(child.sToPort) if hasattr(child, 'sToPort') else 'unspecified',
                            })

                filters.append({
                    'name': str(flt.name),
                    'dn': str(flt.dn),
                    'tenant': tenant_name,
                    'name_alias': str(flt.nameAlias) if hasattr(flt, 'nameAlias') and flt.nameAlias else None,
                    'description': str(flt.descr) if hasattr(flt, 'descr') and flt.descr else None,
                    'entries': entries,
                })
        except Exception as e:
            logger.error(f"Error retrieving Contract Filters: {e}")
        return filters

    # =========================================================================
    # Firmware Detail Methods
    # =========================================================================
    #
    # Queries multiple APIC firmware classes to build a comprehensive mapping
    # of version -> {filename, checksum, type}. On real fabrics with staged
    # firmware images, these classes contain image filenames and checksums.
    #
    # Queried classes:
    #   firmwareRunning       - Running firmware on switch nodes
    #   firmwareCtrlrRunning  - Running firmware on APIC controllers
    #   firmwareFirmware      - Firmware images in the APIC repository
    #   firmwareOSource       - Firmware download sources
    #   firmwareCompRunning   - Component-level firmware (BIOS, CIMC, etc.)
    #
    # On simulators/sandboxes, most of these return minimal data. On real
    # hardware, firmwareFirmware will have image files with names/checksums
    # if firmware has been staged via the APIC firmware repository.
    # =========================================================================

    def get_firmware_details(self) -> Dict[str, Dict[str, Any]]:
        """
        Get detailed firmware information from ACI, including filenames
        and checksums when available.

        Returns a dict keyed by version string, where each value contains:
            {
                'version': str,          # e.g. '6.1(4h)'
                'filename': str or None, # e.g. 'aci-n9000-dk9.16.1.4h.bin'
                'checksum': str or None, # MD5 or SHA hash if available
                'type': str,             # 'switch', 'controller', or 'unknown'
                'internal_label': str or None,  # Build label from APIC
                'node_dn': str or None,  # DN of the node running this version
            }
        """
        firmware_map: Dict[str, Dict[str, Any]] = {}

        # --- 1. Query firmwareRunning (switch nodes) ---
        try:
            running_objs = self._query_class("firmwareRunning")
            for fw in running_objs:
                version = str(fw.version) if hasattr(fw, 'version') and fw.version else None
                if not version:
                    continue

                entry = firmware_map.setdefault(version, {
                    'version': version,
                    'filename': None,
                    'checksum': None,
                    'type': 'switch',
                    'internal_label': None,
                    'node_dn': None,
                })

                # Extract whatever attributes are available
                for attr in ['fwName', 'fileName', 'fullVersion']:
                    if hasattr(fw, attr):
                        val = str(getattr(fw, attr))
                        if val and val not in ('', 'None', 'none'):
                            entry['filename'] = val
                            break

                for attr in ['checksum', 'md5sum', 'md5']:
                    if hasattr(fw, attr):
                        val = str(getattr(fw, attr))
                        if val and val not in ('', 'None', 'none'):
                            entry['checksum'] = val
                            break

                if hasattr(fw, 'internalLabel') and fw.internalLabel:
                    entry['internal_label'] = str(fw.internalLabel)

                if hasattr(fw, 'dn'):
                    entry['node_dn'] = str(fw.dn)

                entry['type'] = 'switch'

            logger.debug(f"firmwareRunning: found {len(running_objs)} entries")
        except Exception as e:
            logger.debug(f"Could not query firmwareRunning: {e}")

        # --- 2. Query firmwareCtrlrRunning (APIC controllers) ---
        try:
            ctrl_objs = self._query_class("firmwareCtrlrRunning")
            for fw in ctrl_objs:
                version = str(fw.version) if hasattr(fw, 'version') and fw.version else None
                if not version:
                    continue

                entry = firmware_map.setdefault(version, {
                    'version': version,
                    'filename': None,
                    'checksum': None,
                    'type': 'controller',
                    'internal_label': None,
                    'node_dn': None,
                })

                # Controller entries often have internalLabel (SHA1 build hash)
                if hasattr(fw, 'internalLabel') and fw.internalLabel:
                    entry['internal_label'] = str(fw.internalLabel)

                for attr in ['fwName', 'fileName', 'fullVersion']:
                    if hasattr(fw, attr):
                        val = str(getattr(fw, attr))
                        if val and val not in ('', 'None', 'none'):
                            entry['filename'] = val
                            break

                for attr in ['checksum', 'md5sum', 'md5']:
                    if hasattr(fw, attr):
                        val = str(getattr(fw, attr))
                        if val and val not in ('', 'None', 'none'):
                            entry['checksum'] = val
                            break

                if hasattr(fw, 'dn'):
                    entry['node_dn'] = str(fw.dn)

                # Only set type if not already set by switch
                if entry['type'] == 'unknown':
                    entry['type'] = 'controller'

            logger.debug(f"firmwareCtrlrRunning: found {len(ctrl_objs)} entries")
        except Exception as e:
            logger.debug(f"Could not query firmwareCtrlrRunning: {e}")

        # --- 3. Query firmwareFirmware (staged images in APIC repo) ---
        # This is the most likely source of filenames and checksums on
        # real fabrics where firmware has been downloaded to the APIC.
        try:
            repo_objs = self._query_class("firmwareFirmware")
            for fw in repo_objs:
                version = None
                filename = None
                checksum = None

                # Try to extract version
                for attr in ['version', 'fwVersion', 'name']:
                    if hasattr(fw, attr):
                        val = str(getattr(fw, attr))
                        if val and val not in ('', 'None', 'none'):
                            version = val
                            break

                # Try to extract filename
                for attr in ['fileName', 'fwName', 'name', 'fullName']:
                    if hasattr(fw, attr):
                        val = str(getattr(fw, attr))
                        if val and val not in ('', 'None', 'none') and ('.' in val or 'aci' in val.lower()):
                            filename = val
                            break

                # Try to extract checksum
                for attr in ['checksum', 'md5sum', 'md5', 'digest']:
                    if hasattr(fw, attr):
                        val = str(getattr(fw, attr))
                        if val and val not in ('', 'None', 'none'):
                            checksum = val
                            break

                if version and version in firmware_map:
                    # Enrich existing entry from running firmware
                    if filename:
                        firmware_map[version]['filename'] = filename
                    if checksum:
                        firmware_map[version]['checksum'] = checksum
                elif version:
                    # New version only in repo (not currently running)
                    firmware_map[version] = {
                        'version': version,
                        'filename': filename,
                        'checksum': checksum,
                        'type': 'staged',
                        'internal_label': None,
                        'node_dn': None,
                    }

            logger.debug(f"firmwareFirmware: found {len(repo_objs)} entries")
        except Exception as e:
            logger.debug(f"Could not query firmwareFirmware: {e}")

        # --- 4. Query firmwareCompRunning for additional component details ---
        # This can have BIOS/CIMC versions with more detail
        try:
            comp_objs = self._query_class("firmwareCompRunning")
            for fw in comp_objs:
                version = str(fw.version) if hasattr(fw, 'version') and fw.version else None
                if not version or version not in firmware_map:
                    continue

                # Only fill in missing data
                entry = firmware_map[version]
                if not entry.get('checksum'):
                    for attr in ['checksum', 'md5sum', 'md5']:
                        if hasattr(fw, attr):
                            val = str(getattr(fw, attr))
                            if val and val not in ('', 'None', 'none'):
                                entry['checksum'] = val
                                break

            logger.debug(f"firmwareCompRunning: found {len(comp_objs)} entries")
        except Exception as e:
            logger.debug(f"Could not query firmwareCompRunning: {e}")

        # --- 5. Try firmwareOSource for download source info ---
        try:
            src_objs = self._query_class("firmwareOSource")
            for src in src_objs:
                # May contain URL/path to firmware image
                for attr in ['url', 'source', 'path']:
                    if hasattr(src, attr):
                        val = str(getattr(src, attr))
                        if val and val not in ('', 'None', 'none'):
                            logger.debug(f"firmwareOSource {attr}: {val}")
            logger.debug(f"firmwareOSource: found {len(src_objs)} entries")
        except Exception as e:
            logger.debug(f"Could not query firmwareOSource: {e}")

        logger.info(
            f"Firmware details: found metadata for {len(firmware_map)} version(s), "
            f"{sum(1 for v in firmware_map.values() if v.get('filename'))} with filename, "
            f"{sum(1 for v in firmware_map.values() if v.get('checksum'))} with checksum"
        )

        return firmware_map