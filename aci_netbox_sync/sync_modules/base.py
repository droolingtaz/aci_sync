"""
Base Sync Module - Common synchronization logic and patterns.

Optimized with:
- _build_updates() to eliminate duplicated field-comparison logic
- Pre-fetch caching to reduce per-object API lookups
- Bulk create support for new objects
- Removed fake sync_parallel (was running sequentially)
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
import time

from ..utils.aci_client import ACIClient
from ..utils.netbox_client import NetBoxClient
from ..config.settings import SyncSettings

logger = logging.getLogger(__name__)


def values_equal(current: Any, new: Any) -> bool:
    """
    Compare two values for equality, handling common type mismatches.

    Handles:
    - None vs empty string
    - Boolean comparisons
    - Nested objects with .id attribute
    """
    # Handle None vs empty string
    if current is None and new == '':
        return True
    if current == '' and new is None:
        return True
    if current is None and new is None:
        return True

    # Handle nested objects (foreign keys)
    if hasattr(current, 'id'):
        current = current.id
    if hasattr(new, 'id'):
        new = new.id

    # Handle boolean comparisons
    if isinstance(new, bool):
        current = bool(current) if current is not None else False
        return current == new

    # Handle string comparisons
    if isinstance(current, str) and isinstance(new, str):
        return current == new

    # Default comparison
    return current == new


@dataclass
class SyncResult:
    """Result of a sync operation."""
    object_type: str
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    failed: int = 0
    verified: int = 0
    errors: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    def __str__(self) -> str:
        return (
            f"{self.object_type}: created={self.created}, updated={self.updated}, "
            f"unchanged={self.unchanged}, failed={self.failed}, verified={self.verified}"
        )


@dataclass
class SyncStats:
    """Aggregate statistics for all sync operations."""
    results: List[SyncResult] = field(default_factory=list)
    total_duration: float = 0.0

    def add_result(self, result: SyncResult) -> None:
        self.results.append(result)
        self.total_duration += result.duration_seconds

    @property
    def total_created(self) -> int:
        return sum(r.created for r in self.results)

    @property
    def total_updated(self) -> int:
        return sum(r.updated for r in self.results)

    @property
    def total_unchanged(self) -> int:
        return sum(r.unchanged for r in self.results)

    @property
    def total_failed(self) -> int:
        return sum(r.failed for r in self.results)

    @property
    def total_errors(self) -> List[str]:
        errors = []
        for r in self.results:
            errors.extend(r.errors)
        return errors

    def summary(self) -> str:
        lines = [
            "=" * 60,
            "SYNC SUMMARY",
            "=" * 60,
        ]
        for r in self.results:
            lines.append(str(r))
        lines.extend([
            "-" * 60,
            f"Total: created={self.total_created}, updated={self.total_updated}, "
            f"unchanged={self.total_unchanged}, failed={self.total_failed}",
            f"Duration: {self.total_duration:.2f} seconds",
            "=" * 60,
        ])
        return "\n".join(lines)


class BaseSyncModule(ABC):
    """
    Abstract base class for sync modules.
    Each module handles synchronization of a specific object type.

    Subclasses should define:
    - FIELD_MAP: Dict mapping ACI field names to NetBox field names
    - CONVERTERS: Dict mapping ACI field names to converter functions
    - fetch_from_aci(): Retrieve objects from ACI
    - sync_object(): Sync a single object to NetBox
    """

    # Override in subclasses: maps ACI field names -> NetBox field names
    FIELD_MAP: Dict[str, str] = {}

    # Override in subclasses: maps ACI field names -> conversion functions
    CONVERTERS: Dict[str, Callable] = {}

    def __init__(self, aci_client: ACIClient, netbox_client: NetBoxClient,
                 settings: SyncSettings, context: Optional[Dict] = None):
        self.aci = aci_client
        self.netbox = netbox_client
        self.settings = settings
        self.context = context if context is not None else {}
        self.result = SyncResult(object_type=self.object_type)
        self._existing_cache: Dict[str, Any] = {}

    @property
    @abstractmethod
    def object_type(self) -> str:
        """Return the name of the object type being synced."""
        pass

    @abstractmethod
    def fetch_from_aci(self) -> List[Dict[str, Any]]:
        """Fetch objects from ACI."""
        pass

    @abstractmethod
    def sync_object(self, aci_data: Dict[str, Any]) -> bool:
        """
        Sync a single object to NetBox.
        Returns True if successful, False otherwise.
        """
        pass

    def pre_sync(self) -> None:
        """Hook called before sync starts. Override for setup logic like pre-fetching."""
        pass

    def post_sync(self) -> None:
        """Hook called after sync completes. Override for cleanup logic."""
        pass

    def _build_updates(
        self,
        existing_obj: Any,
        aci_data: Dict[str, Any],
        field_map: Optional[Dict[str, str]] = None,
        converters: Optional[Dict[str, Callable]] = None,
        extra_updates: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Build an update dict by comparing ACI data to an existing NetBox object.

        Uses the class-level FIELD_MAP and CONVERTERS by default, but accepts
        overrides for one-off fields.

        Args:
            existing_obj: The existing NetBox object to compare against.
            aci_data: Raw ACI data dictionary.
            field_map: Optional override for ACI->NetBox field mapping.
            converters: Optional override for value converters.
            extra_updates: Additional updates to merge in (e.g., foreign key changes).

        Returns:
            Dict of {netbox_field: new_value} for fields that differ.
        """
        field_map = field_map or self.FIELD_MAP
        converters = converters or self.CONVERTERS
        updates = {}

        for aci_field, nb_field in field_map.items():
            if aci_field not in aci_data:
                continue
            value = aci_data[aci_field]
            if value is None:
                continue
            if aci_field in converters:
                value = converters[aci_field](value)
            current = getattr(existing_obj, nb_field, None)
            if not values_equal(current, value):
                updates[nb_field] = value

        if extra_updates:
            updates.update(extra_updates)

        return updates

    def _build_params(
        self,
        aci_data: Dict[str, Any],
        field_map: Optional[Dict[str, str]] = None,
        converters: Optional[Dict[str, Callable]] = None,
    ) -> Dict[str, Any]:
        """
        Build a create/update params dict from ACI data using field mappings.

        Args:
            aci_data: Raw ACI data dictionary.
            field_map: Optional override for ACI->NetBox field mapping.
            converters: Optional override for value converters.

        Returns:
            Dict of {netbox_field: value} for all mapped fields with values.
        """
        field_map = field_map or self.FIELD_MAP
        converters = converters or self.CONVERTERS
        params = {}

        for aci_field, nb_field in field_map.items():
            if aci_field not in aci_data:
                continue
            value = aci_data[aci_field]
            if value is None:
                continue
            if aci_field in converters:
                value = converters[aci_field](value)
            params[nb_field] = value

        return params

    def _apply_updates(
        self,
        obj: Any,
        updates: Dict[str, Any],
        obj_label: str,
        update_fn: Callable,
    ) -> None:
        """
        Apply updates to an existing object and track results.

        Args:
            obj: The NetBox object to update.
            updates: Dict of field changes.
            obj_label: Human-readable label for logging.
            update_fn: Callable(obj, updates, verify) -> (changed, verified).
        """
        if updates:
            logger.debug(f"{self.object_type} {obj_label} updates: {updates}")
            changed, verified = update_fn(obj, updates, self.settings.verify_updates)
            if changed:
                self.result.updated += 1
                if verified:
                    self.result.verified += 1
                logger.info(f"Updated {self.object_type}: {obj_label}")
            else:
                self.result.unchanged += 1
        else:
            self.result.unchanged += 1

    def sync(self) -> SyncResult:
        """Execute the sync operation."""
        start_time = time.time()
        logger.info(f"Starting sync for {self.object_type}")

        try:
            self.pre_sync()

            # Fetch data from ACI
            aci_objects = self.fetch_from_aci()
            logger.info(f"Fetched {len(aci_objects)} {self.object_type} from ACI")

            if self.settings.dry_run:
                logger.info(f"DRY RUN: Would sync {len(aci_objects)} {self.object_type}")
                self.result.unchanged = len(aci_objects)
            else:
                for obj in aci_objects:
                    try:
                        success = self.sync_object(obj)
                        if not success and not self.settings.continue_on_error:
                            break
                    except Exception as e:
                        self.result.failed += 1
                        self.result.errors.append(f"Error syncing {obj}: {e}")
                        logger.error(f"Error syncing {self.object_type}: {e}")
                        if not self.settings.continue_on_error:
                            raise

            self.post_sync()

        except Exception as e:
            logger.error(f"Sync failed for {self.object_type}: {e}")
            self.result.errors.append(str(e))

        self.result.duration_seconds = time.time() - start_time
        logger.info(f"Completed sync for {self.object_type}: {self.result}")
        return self.result


class SyncOrchestrator:
    """
    Orchestrates the execution of multiple sync modules in the correct order.
    """

    def __init__(self, aci_client: ACIClient, netbox_client: NetBoxClient,
                 settings: SyncSettings):
        self.aci = aci_client
        self.netbox = netbox_client
        self.settings = settings
        self.stats = SyncStats()
        self.context: Dict[str, Any] = {}

    def register_context(self, key: str, value: Any) -> None:
        """Register a value in the shared context."""
        self.context[key] = value

    def get_context(self, key: str) -> Optional[Any]:
        """Get a value from the shared context."""
        return self.context.get(key)

    def run_module(self, module_class: type) -> SyncResult:
        """Run a single sync module."""
        module = module_class(self.aci, self.netbox, self.settings, self.context)
        result = module.sync()
        self.stats.add_result(result)
        return result

    def run_all(self, modules: List[type]) -> SyncStats:
        """Run all sync modules in order."""
        logger.info(f"Starting sync orchestration with {len(modules)} modules")

        for module_class in modules:
            try:
                self.run_module(module_class)
            except Exception as e:
                logger.error(f"Module {module_class.__name__} failed: {e}")
                if not self.settings.continue_on_error:
                    break

        logger.info(self.stats.summary())
        return self.stats