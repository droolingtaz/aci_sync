"""
Base Sync Module - Common synchronization logic and patterns.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    """

    def __init__(self, aci_client: ACIClient, netbox_client: NetBoxClient,
                 settings: SyncSettings, context: Optional[Dict] = None):
        self.aci = aci_client
        self.netbox = netbox_client
        self.settings = settings
        self.context = context if context is not None else {}
        self.result = SyncResult(object_type=self.object_type)

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
        """Hook called before sync starts. Override for setup logic."""
        pass

    def post_sync(self) -> None:
        """Hook called after sync completes. Override for cleanup logic."""
        pass

    def sync(self) -> SyncResult:
        """
        Execute the sync operation.
        """
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
                # Sync each object
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

    def sync_parallel(self, max_workers: Optional[int] = None) -> SyncResult:
        """
        Execute sync operation with parallel processing.
        """
        start_time = time.time()
        max_workers = max_workers or self.settings.max_workers
        logger.info(f"Starting parallel sync for {self.object_type} with {max_workers} workers")

        try:
            self.pre_sync()

            # Fetch data from ACI
            aci_objects = self.fetch_from_aci()
            logger.info(f"Fetched {len(aci_objects)} {self.object_type} from ACI")

            if self.settings.dry_run:
                logger.info(f"DRY RUN: Would sync {len(aci_objects)} {self.object_type}")
                self.result.unchanged = len(aci_objects)
            else:
                # Note: Parallel sync needs careful handling of shared state
                # For now, we use sequential processing to avoid race conditions
                # with NetBox API. Enable parallel only for read operations.
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
        logger.info(f"Completed parallel sync for {self.object_type}: {self.result}")
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
