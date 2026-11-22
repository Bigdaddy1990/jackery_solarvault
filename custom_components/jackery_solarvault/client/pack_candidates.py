"""Task-local battery-pack candidates shared by transport handlers."""

from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
import sys
import time
from typing import TYPE_CHECKING, Any, Final, cast

from ..ingest import merge_selected_state
from ..util import utc_now as _default_utc_now

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine, Hashable
    from datetime import datetime

    from ..coordinator import JackerySolarVaultCoordinator


def utc_now() -> datetime:
    """Preserve the coordinator clock used for candidate commits."""
    clock: Callable[[], datetime] = getattr(
        sys.modules.get("custom_components.jackery_solarvault.coordinator"),
        "utc_now",
        _default_utc_now,
    )
    return clock()


@dataclass(frozen=True, slots=True)
class _HttpPackFieldCandidate:
    """One HTTP pack/list field with its actual request and receive metadata."""

    generation: int
    received_at: float
    value: object


_PACK_TRANSACTION: ContextVar[object | None] = ContextVar(
    "jackery_pack_candidate_transaction", default=None
)


_PACK_STATE_DELETED: Final = object()


def _pack_state_owner_key(name: str, key: Hashable) -> tuple[str, str] | None:
    """Resolve the physical pack owner of one staged state entry."""
    if not isinstance(key, tuple) or len(key) < 2:  # ruff: ignore[magic-value-comparison]
        return None
    if name == "_battery_pack_identity_observations":
        return None
    if name == "_accessory_source_state" and len(key) >= 3:  # ruff: ignore[magic-value-comparison]
        return (str(key[0]), str(key[2]))
    return (str(key[0]), str(key[1]))


class _PackCandidateTransaction:
    """Stage selectable pack candidates until their route actually commits."""

    def __init__(self, owner: JackerySolarVaultCoordinator) -> None:
        self.owner = owner
        self.active = True
        self.writes: dict[tuple[str, Hashable], Any] = {}
        self.bases: dict[tuple[str, Hashable], Any] = {}
        self.epochs: dict[tuple[str, str], int] = {}
        self.observation_epochs: dict[Hashable, int] = {}
        self.observation_tombstones: dict[Hashable, int] = {}
        self.receipt_bindings: dict[
            tuple[tuple[str, str, str], tuple[str, str, str]],
            tuple[dict[str, Any], int, int],
        ] = {}

    def get(self, name: str, key: Hashable, default: object = None) -> Any:  # ruff: ignore[any-type]
        """Read this task's staged value or the confirmed store."""
        slot = (name, key)
        if slot in self.writes:
            value = self.writes[slot]
            return default if value is _PACK_STATE_DELETED else value
        return getattr(self.owner, name, {}).get(key, default)

    def set(self, name: str, key: Hashable, value: object) -> None:
        """Stage a value with its confirmed predecessor and owner epoch."""
        slot = (name, key)
        if slot not in self.bases:
            self.bases[slot] = getattr(self.owner, name, {}).get(key)
            if name == "_battery_pack_identity_observations" and isinstance(key, tuple):
                self.observation_epochs.setdefault(
                    key,
                    getattr(self.owner, "_battery_pack_observation_revisions", {}).get(
                        key, 0
                    ),
                )
                self.observation_tombstones.setdefault(
                    key,
                    getattr(self.owner, "_battery_pack_observation_tombstones", {}).get(
                        key, 0
                    ),
                )
            owner_key = _pack_state_owner_key(name, key)
            if owner_key is not None:
                self.epochs.setdefault(
                    owner_key,
                    getattr(self.owner, "_http_pack_owner_epochs", {}).get(
                        owner_key, 0
                    ),
                )
        self.writes[slot] = value

    def pack_fields(
        self, key: tuple[str, str]
    ) -> dict[tuple[str, ...], _HttpPackFieldCandidate]:
        """Copy only this pack's mutable field index into the transaction."""
        name = "_http_pack_field_candidates"
        slot = (name, key)
        if slot not in self.writes:
            value = dict(self.get(name, key, {}))
            self.set(name, key, value)
        return cast("dict[tuple[str, ...], _HttpPackFieldCandidate]", self.writes[slot])

    def stage_receipt_binding(
        self, old_key: tuple[str, str, str], new_key: tuple[str, str, str]
    ) -> None:
        """Defer identity association until this route has committed."""
        receipts = getattr(self.owner, "_layer_source_payloads", {})
        old_sources = receipts.get(old_key)
        if not isinstance(old_sources, dict):
            old_sources = {}
        owner_key = (new_key[0], new_key[2])
        epoch = getattr(self.owner, "_http_pack_owner_epochs", {}).get(owner_key, 0)
        index = int(old_key[2].removeprefix("index_"))
        revision = getattr(self.owner, "_battery_pack_identity_revisions", {}).get(
            (old_key[0], index), 0
        )
        self.receipt_bindings[old_key, new_key] = (dict(old_sources), epoch, revision)

    def discard_device(self, device_id: str) -> None:
        """Discard one uncommitted device without disturbing peer devices."""
        for slot in tuple(self.writes):
            _name, key = slot
            if key == device_id or (
                isinstance(key, tuple) and key and key[0] == device_id
            ):
                self.writes.pop(slot)
                self.bases.pop(slot, None)
        for owner in tuple(self.epochs):
            if owner[0] == device_id:
                self.epochs.pop(owner)
        for binding in tuple(self.receipt_bindings):
            if binding[0][0] == device_id:
                self.receipt_bindings.pop(binding)

    def commit(self) -> None:  # ruff: ignore[complex-structure, too-many-branches, too-many-locals, too-many-statements]
        """Publish only this task's changes without reversing newer owners."""
        if not self.active:
            return
        observation_revisions = getattr(
            self.owner, "_battery_pack_observation_revisions", None
        )
        if observation_revisions is None:
            observation_revisions = self.owner._battery_pack_observation_revisions = {}  # ruff: ignore[private-member-access]
        tombstones = getattr(self.owner, "_battery_pack_observation_tombstones", None)
        if tombstones is None:
            tombstones = self.owner._battery_pack_observation_tombstones = {}  # ruff: ignore[private-member-access]
        stale_observation_keys = {
            key
            for key, epoch in self.observation_epochs.items()
            if observation_revisions.get(key, 0) != epoch
        }
        changed_observation_keys: set[Hashable] = set()
        applied_selected: set[Hashable] = set()
        invalid_bindings: set[tuple[str, str, str]] = set()
        for (old_key, new_key), (
            _sources,
            epoch,
            revision,
        ) in self.receipt_bindings.items():
            owner_key = (new_key[0], new_key[2])
            index_key = (old_key[0], int(old_key[2].removeprefix("index_")))
            current_epoch = getattr(self.owner, "_http_pack_owner_epochs", {}).get(
                owner_key, 0
            )
            current_revision = getattr(
                self.owner, "_battery_pack_identity_revisions", {}
            ).get(index_key, 0)
            override = getattr(self.owner, "_battery_pack_identity_overrides", {}).get(
                index_key
            )
            old_state = getattr(self.owner, "_accessory_source_state", {}).get(old_key)
            old_base = self.bases.get(("_accessory_source_state", old_key))
            if (
                current_epoch != epoch
                or current_revision != revision
                or override != new_key[2]
                or old_state is not old_base
            ):
                invalid_bindings.update((old_key, new_key))
        for (name, key), staged in self.writes.items():
            is_observation = name == "_battery_pack_identity_observations"
            if is_observation and key in stale_observation_keys:
                store = getattr(self.owner, name, {})
                current = store.get(key)
                if not (
                    staged is not _PACK_STATE_DELETED
                    and current is not None
                    and tombstones.get(key, 0)
                    == self.observation_tombstones.get(key, 0)
                    and staged[0] >= current[0]
                ):
                    continue
            if name == "_accessory_source_state" and key in invalid_bindings:
                continue
            candidate_owner = _pack_state_owner_key(name, key)
            if candidate_owner is not None:
                current_epoch = getattr(self.owner, "_http_pack_owner_epochs", {}).get(
                    candidate_owner, 0
                )
                if current_epoch != self.epochs.get(candidate_owner, current_epoch):
                    continue
            store = getattr(self.owner, name, None)
            if store is None:
                store = {}
                setattr(self.owner, name, store)
            current = store.get(key)
            base = self.bases[name, key]
            if staged is _PACK_STATE_DELETED:
                if current is base:
                    store.pop(key, None)
                    if is_observation and current is not None:
                        changed_observation_keys.add(key)
                        tombstones[key] = tombstones.get(key, 0) + 1
                continue
            if name == "_accessory_source_state":
                if base is not None and current is None:
                    continue
                if current is not None and current is not base:
                    staged = merge_selected_state(  # ruff: ignore[redefined-loop-name]
                        current,
                        staged,
                        freshness_window_seconds=self.owner._transport_source_freshness_window(),  # ruff: ignore[private-member-access]
                    )
                applied_selected.add(key)
            elif name == "_pack_validated_source_candidates" and current is not None:
                if current is not base:
                    staged = merge_selected_state(  # ruff: ignore[redefined-loop-name]
                        current, staged, freshness_window_seconds=0.0
                    )
            elif name == "_http_pack_field_candidates":
                current = dict(current or {})
                for path, candidate in staged.items():
                    if base is not None and base.get(path) is candidate:
                        continue
                    overlaps = [
                        prior
                        for prior in current
                        if prior[: len(path)] == path or path[: len(prior)] == prior
                    ]
                    if any(
                        current[prior].generation > candidate.generation
                        for prior in overlaps
                    ):
                        continue
                    for prior in overlaps:
                        current.pop(prior)
                    current[path] = candidate
                staged = current  # ruff: ignore[redefined-loop-name]
            elif name in {
                "_http_pack_original_generations",
                "_http_pack_original_by_serial",
                "_battery_pack_http_cache_seen",
            }:
                staged = max(current if current is not None else staged, staged)  # ruff: ignore[redefined-loop-name]
            elif name == "_battery_pack_identity_observations" and current is not None:
                if current[0] > staged[0]:
                    continue
                if current[0] == staged[0]:
                    staged = (staged[0], current[1] and staged[1])  # ruff: ignore[redefined-loop-name]
            if is_observation and staged[1] and isinstance(key, tuple):
                now = utc_now()
                if any(
                    other_owner != key[0]
                    and other_source == key[1]
                    and other_serial == key[2]
                    and other_valid
                    and 0
                    <= (now - other_at).total_seconds()
                    <= self.owner._transport_source_freshness_window()  # ruff: ignore[private-member-access]
                    for (other_owner, other_source, other_serial), (
                        other_at,
                        other_valid,
                    ) in store.items()
                ):
                    staged = (staged[0], False)  # ruff: ignore[redefined-loop-name]
            store[key] = staged
            if is_observation:
                changed_observation_keys.add(key)
        for key in changed_observation_keys:
            observation_revisions[key] = observation_revisions.get(key, 0) + 1
        for (old_key, new_key), (
            sources,
            _epoch,
            _revision,
        ) in self.receipt_bindings.items():
            if new_key in applied_selected and new_key not in invalid_bindings:
                self.owner._join_pack_source_receipts(old_key, new_key, sources)  # ruff: ignore[private-member-access]
        self.writes.clear()
        self.bases.clear()
        self.epochs.clear()
        self.observation_epochs.clear()
        self.observation_tombstones.clear()
        self.receipt_bindings.clear()


def _transactional_pack_candidates[PackResult](
    handler: Callable[..., Awaitable[PackResult]],
) -> Callable[..., Coroutine[Any, Any, PackResult]]:
    """Keep uncommitted pack candidates task-local without locking transports."""

    @wraps(handler)
    async def wrapped(
        self: JackerySolarVaultCoordinator, *args: object, **kwargs: object
    ) -> PackResult:
        transaction = _PackCandidateTransaction(self)
        self._pack_observation_active_transactions = (
            getattr(self, "_pack_observation_active_transactions", 0) + 1
        )
        token = _PACK_TRANSACTION.set(transaction)
        try:
            return await handler(self, *args, **kwargs)
        finally:
            transaction.active = False
            _PACK_TRANSACTION.reset(token)
            self._pack_observation_active_transactions -= 1
            if self._pack_observation_active_transactions == 0:
                getattr(self, "_battery_pack_observation_revisions", {}).clear()
                getattr(self, "_battery_pack_observation_tombstones", {}).clear()
                now_mono = time.monotonic()
                if now_mono >= getattr(self, "_pack_observation_next_cleanup", 0):
                    now = utc_now()
                    window = self._transport_source_freshness_window()
                    observations = getattr(
                        self, "_battery_pack_identity_observations", {}
                    )
                    for key, (observed_at, _valid) in list(observations.items()):
                        if (now - observed_at).total_seconds() > window:
                            observations.pop(key)
                    self._pack_observation_next_cleanup = now_mono + window

    return wrapped


def _pack_transaction(
    owner: object,
) -> _PackCandidateTransaction | None:
    """Return only the active transaction belonging to this coordinator."""
    transaction = _PACK_TRANSACTION.get()
    if (
        isinstance(transaction, _PackCandidateTransaction)
        and transaction.owner is owner
        and transaction.active
    ):
        return transaction
    return None


def _pack_state_get(
    owner: JackerySolarVaultCoordinator,
    name: str,
    key: Hashable,
    default: object = None,
) -> Any:  # ruff: ignore[any-type]
    """Read staged state within a transaction, confirmed state elsewhere."""
    transaction = _pack_transaction(owner)
    if transaction is not None:
        return transaction.get(name, key, default)
    return getattr(owner, name, {}).get(key, default)


def _pack_state_set(
    owner: JackerySolarVaultCoordinator, name: str, key: Hashable, value: object
) -> None:
    """Stage candidate state instead of exposing an uncommitted value."""
    transaction = _pack_transaction(owner)
    if transaction is not None:
        transaction.set(name, key, value)
        return
    store = getattr(owner, name, None)
    if store is None:
        store = {}
        setattr(owner, name, store)
    store[key] = value
    if name == "_battery_pack_identity_observations" and isinstance(key, tuple):
        revisions = getattr(owner, "_battery_pack_observation_revisions", None)
        if revisions is None:
            revisions = owner._battery_pack_observation_revisions = {}  # ruff: ignore[private-member-access]
        revisions[key] = revisions.get(key, 0) + 1


def _pack_state_pop(
    owner: JackerySolarVaultCoordinator, name: str, key: Hashable
) -> None:
    """Remove confirmed pack state only when its transaction commits."""
    transaction = _pack_transaction(owner)
    if transaction is not None:
        transaction.set(name, key, _PACK_STATE_DELETED)
        return
    store = getattr(owner, name, {})
    existed = key in store
    store.pop(key, None)
    if (
        existed
        and name == "_battery_pack_identity_observations"
        and isinstance(key, tuple)
    ):
        revisions = getattr(owner, "_battery_pack_observation_revisions", None)
        if revisions is None:
            revisions = owner._battery_pack_observation_revisions = {}  # ruff: ignore[private-member-access]
        revisions[key] = revisions.get(key, 0) + 1
        tombstones = getattr(owner, "_battery_pack_observation_tombstones", None)
        if tombstones is None:
            tombstones = owner._battery_pack_observation_tombstones = {}  # ruff: ignore[private-member-access]
        tombstones[key] = tombstones.get(key, 0) + 1


def _pack_candidate_fields(
    owner: JackerySolarVaultCoordinator, key: tuple[str, str]
) -> dict[tuple[str, ...], _HttpPackFieldCandidate]:
    """Return a task-local copy of the mutable pack field index."""
    transaction = _pack_transaction(owner)
    if transaction is not None:
        return transaction.pack_fields(key)
    store: (
        dict[tuple[str, str], dict[tuple[str, ...], _HttpPackFieldCandidate]] | None
    ) = getattr(owner, "_http_pack_field_candidates", None)
    if store is None:
        store = owner._http_pack_field_candidates = {}  # ruff: ignore[private-member-access]
    return store.setdefault(key, {})


def _commit_pack_candidates(owner: object) -> None:
    """Commit candidates only at an existing successful data commit."""
    transaction = _pack_transaction(owner)
    if transaction is not None:
        transaction.commit()
