"""
Software Version Sync Module - Synchronize ACI firmware versions to NetBox
using the netbox-software-tracker plugin.

This module:
1. For each unique firmware version found on ACI nodes, creates a
   software-image entry in the netbox_software_tracker plugin
2. Optionally assigns golden images to device types

Plugin API:
  Base: /api/plugins/netbox_software_tracker/
  Endpoints:
    - software-image/ (version, filename, md5sum, comments)
    - golden-image/   (device type <-> software image assignment)

Requires: netbox-software-tracker plugin v0.3.7+
          (https://pypi.org/project/netbox-software-tracker/)
"""

import logging
from typing import Any, Dict, List, Optional

from .base import BaseSyncModule

logger = logging.getLogger(__name__)


class SoftwareVersionSyncModule(BaseSyncModule):
    """Sync ACI node firmware versions to NetBox Software Tracker."""

    @property
    def object_type(self) -> str:
        return "SoftwareVersion"

    def pre_sync(self) -> None:
        """Fetch firmware details (filenames, checksums) from APIC before syncing."""
        try:
            self._firmware_details = self.aci.get_firmware_details()
            logger.info(
                f"Loaded firmware details for {len(self._firmware_details)} version(s)"
            )
        except Exception as e:
            logger.warning(f"Could not fetch firmware details: {e}")
            self._firmware_details = {}

    def fetch_from_aci(self) -> List[Dict[str, Any]]:
        """
        Fetch unique firmware versions from ACI nodes.

        Returns a deduplicated list of version records. Each record includes
        the version string and the list of nodes running that version.
        """
        nodes = self.aci.get_fabric_nodes()

        # Group nodes by firmware version
        version_map: Dict[str, List[Dict[str, Any]]] = {}
        for node in nodes:
            version = node.get('version')
            if not version or version in ('unknown', 'n/a', ''):
                continue

            if version not in version_map:
                version_map[version] = []
            version_map[version].append(node)

        # Build deduplicated version records
        result = []
        for version, version_nodes in version_map.items():
            result.append({
                'version': version,
                'nodes': version_nodes,
                'node_count': len(version_nodes),
            })

        logger.info(
            f"Found {len(result)} unique ACI firmware version(s) "
            f"across {len(nodes)} node(s)"
        )
        return result

    def sync_object(self, aci_data: Dict[str, Any]) -> bool:
        """Sync a single firmware version to NetBox Software Tracker."""
        try:
            version = aci_data.get('version')
            if not version:
                logger.warning(f"Skipping entry without version: {aci_data}")
                return False

            nodes = aci_data.get('nodes', [])
            node_count = aci_data.get('node_count', 0)

            # Build comments from node summary
            node_names = [n.get('name', 'unknown') for n in nodes[:10]]
            suffix = f" (+{node_count - 10} more)" if node_count > 10 else ""
            comments = (
                f"ACI firmware running on {node_count} node(s): "
                f"{', '.join(node_names)}{suffix}"
            )

            # Truncate version to max 32 chars (plugin field limit)
            version_str = version[:32]

            # Look up firmware details from APIC (filename, checksum)
            fw_details = getattr(self, '_firmware_details', {}).get(version, {})
            
            # Build create/update kwargs with available firmware metadata
            sw_kwargs = {
                'comments': comments,
            }
            
            filename = fw_details.get('filename')
            if filename:
                # Truncate to max 256 chars (plugin field limit)
                sw_kwargs['filename'] = filename[:256]
            
            checksum = fw_details.get('checksum')
            if checksum:
                # Truncate to max 36 chars (plugin field limit)
                sw_kwargs['md5sum'] = checksum[:36]

            # Get or create software-image entry
            sw_image, created = self.netbox.get_or_create_software_image(
                version=version_str,
                **sw_kwargs,
            )

            if created:
                self.result.created += 1
                logger.info(f"Created software version: {version_str}")
            else:
                # Check if comments need updating (node list may have changed)
                updates = {}
                current_comments = getattr(sw_image, 'comments', None) or ''
                if current_comments != comments:
                    updates['comments'] = comments

                # Check if filename/checksum need updating (from APIC firmware repo)
                if filename:
                    current_fn = getattr(sw_image, 'filename', None) or ''
                    if current_fn != filename[:256]:
                        updates['filename'] = filename[:256]

                if checksum:
                    current_md5 = getattr(sw_image, 'md5sum', None) or ''
                    if current_md5 != checksum[:36]:
                        updates['md5sum'] = checksum[:36]

                if updates:
                    changed, verified = self.netbox.update_software_image(
                        sw_image, updates, self.settings.verify_updates
                    )
                    if changed:
                        self.result.updated += 1
                        if verified:
                            self.result.verified += 1
                        logger.info(f"Updated software version: {version_str}")
                    else:
                        self.result.unchanged += 1
                else:
                    self.result.unchanged += 1

            # Store version -> software-image ID mapping in context
            sw_version_map = self.context.setdefault('sw_version_map', {})
            sw_version_map[version] = sw_image.id

            return True

        except Exception as e:
            logger.error(f"Failed to sync software version {aci_data.get('version')}: {e}")
            self.result.failed += 1
            self.result.errors.append(str(e))
            return False

    def post_sync(self) -> None:
        """
        After syncing versions:
        1. Assign firmware version to each individual DCIM device (switch/node)
           via local_context_data so it's visible on the device page.
        2. Assign golden images to device types.
        """
        sw_version_map = self.context.get('sw_version_map', {})
        if not sw_version_map:
            return

        # Collect node data for both assignments
        try:
            nodes = self.aci.get_fabric_nodes()
        except Exception as e:
            logger.warning(f"Could not re-fetch nodes for software assignment: {e}")
            return

        # --- Step 1: Assign version to individual devices ---
        devices_updated = 0
        for node in nodes:
            version = node.get('version')
            node_name = node.get('name')
            if not version or not node_name:
                continue

            try:
                device = self.netbox.get_dcim_device_by_name(node_name)
                if not device:
                    logger.debug(f"DCIM device not found for node {node_name}")
                    continue

                # Build firmware context data
                firmware_context = {
                    'firmware': {
                        'version': version,
                        'model': node.get('model', ''),
                        'serial': node.get('serial', ''),
                    }
                }
                
                # Add filename and checksum if available from APIC
                fw_details = getattr(self, '_firmware_details', {}).get(version, {})
                if fw_details.get('filename'):
                    firmware_context['firmware']['filename'] = fw_details['filename']
                if fw_details.get('checksum'):
                    firmware_context['firmware']['checksum'] = fw_details['checksum']

                # Get current local_context_data and merge
                current_ctx = getattr(device, 'local_context_data', None) or {}
                current_firmware = current_ctx.get('firmware', {})

                if current_firmware != firmware_context['firmware']:
                    # Merge - preserve any other keys in local_context_data
                    merged_ctx = dict(current_ctx)
                    merged_ctx['firmware'] = firmware_context['firmware']

                    device.update({'local_context_data': merged_ctx})
                    devices_updated += 1
                    logger.debug(
                        f"Set firmware {version} on device {node_name}"
                    )

            except Exception as e:
                logger.debug(f"Could not set firmware on device {node_name}: {e}")

        if devices_updated:
            logger.info(
                f"Updated firmware version on {devices_updated} device(s)"
            )

        # --- Step 2: Assign golden images to device types ---
        assigned = 0
        seen_device_types = set()  # Avoid duplicate assignments per model
        for node in nodes:
            version = node.get('version')
            model = node.get('model')
            if not version or not model:
                continue

            # Skip if we already handled this model+version combo
            key = f"{model}:{version}"
            if key in seen_device_types:
                continue
            seen_device_types.add(key)

            sw_image_id = sw_version_map.get(version)
            if not sw_image_id:
                continue

            try:
                device_type = self.netbox.get_device_type_by_model(model)
                if not device_type:
                    continue

                success = self.netbox.assign_golden_image(
                    software_image_id=sw_image_id,
                    device_type_id=device_type.id,
                )
                if success:
                    assigned += 1

            except Exception as e:
                logger.debug(f"Could not assign golden image for {model}: {e}")

        if assigned:
            logger.info(f"Assigned golden images to {assigned} device type(s)")