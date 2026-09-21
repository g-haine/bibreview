"""Generic resumable campaign and stable batching primitives.

This module deliberately contains no audit-, initialization-, DOI-, provider-,
or project-specific policy. It models only the mechanics shared by long-running
BibReview workflows: a fixed ordered item universe, one resumable open batch,
per-item progress, retryable failures, and deterministic serialization.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, replace
from typing import Any, Iterable, Mapping


CAMPAIGN_SCHEMA_VERSION = 1
_ITEM_STATES = frozenset({"pending", "active", "completed", "retryable", "failed"})
_RESULT_STATES = frozenset({"completed", "retryable", "failed"})


class CampaignError(ValueError):
    """Raised when campaign state or a campaign transition is invalid."""


@dataclass(frozen=True)
class CampaignItem:
    """One stable item tracked by a resumable campaign."""

    key: str
    state: str = "pending"
    attempts: int = 0
    detail: str = ""


@dataclass(frozen=True)
class CampaignBatch:
    """One stable bounded batch in campaign history."""

    id: str
    keys: tuple[str, ...]
    closed: bool = False


@dataclass(frozen=True)
class Campaign:
    """Complete generic campaign state.

    Items preserve the original campaign ordering. That snapshot is the stable
    source for all future batch selection and therefore does not shift when
    external project state changes.
    """

    kind: str
    batch_size: int
    items: tuple[CampaignItem, ...]
    batches: tuple[CampaignBatch, ...] = ()


@dataclass(frozen=True)
class CampaignProgress:
    """Compact progress counters for human or machine reporting."""

    total: int
    pending: int
    active: int
    completed: int
    retryable: int
    failed: int
    batches_opened: int
    batches_closed: int
    open_batch: str | None

    @property
    def exhausted(self) -> bool:
        """Whether no more pending, active, or retryable work remains."""
        return self.pending == 0 and self.active == 0 and self.retryable == 0

    @property
    def successful(self) -> bool:
        """Whether the campaign is exhausted without terminal failures."""
        return self.exhausted and self.failed == 0

    def data(self) -> dict[str, object]:
        """Return a JSON-safe machine-readable representation."""
        return asdict(self)

    def summary(self) -> str:
        """Return a compact human-readable progress summary."""
        return (
            f"total: {self.total}; pending: {self.pending}; active: {self.active}; "
            f"completed: {self.completed}; retryable: {self.retryable}; "
            f"failed: {self.failed}; batches: {self.batches_closed}/"
            f"{self.batches_opened}"
        )


def _validate_kind(kind: str) -> str:
    if not isinstance(kind, str):
        raise CampaignError("campaign kind must be a string")
    value = kind.strip()
    if not value:
        raise CampaignError("campaign kind must not be empty")
    if value != kind:
        raise CampaignError("campaign kind must not contain surrounding whitespace")
    return value


def _validate_batch_size(batch_size: int) -> int:
    if (
        not isinstance(batch_size, int)
        or isinstance(batch_size, bool)
        or batch_size <= 0
    ):
        raise CampaignError("campaign batch_size must be a positive integer")
    return batch_size


def _validate_key(key: str, *, name: str = "campaign item key") -> str:
    if not isinstance(key, str):
        raise CampaignError(f"{name} must be a string")
    if not key:
        raise CampaignError(f"{name} must not be empty")
    if key.strip() != key:
        raise CampaignError(f"{name} must not contain surrounding whitespace")
    return key


def create_campaign(
    kind: str,
    item_keys: Iterable[str],
    *,
    batch_size: int = 50,
) -> Campaign:
    """Create one deterministic campaign snapshot from stable ordered item keys."""
    normalized_kind = _validate_kind(kind)
    normalized_batch_size = _validate_batch_size(batch_size)

    items: list[CampaignItem] = []
    seen: set[str] = set()
    for index, raw_key in enumerate(item_keys, 1):
        key = _validate_key(raw_key, name=f"campaign item {index} key")
        if key in seen:
            raise CampaignError(f"duplicate campaign item key: {key}")
        seen.add(key)
        items.append(CampaignItem(key=key))

    return Campaign(
        kind=normalized_kind,
        batch_size=normalized_batch_size,
        items=tuple(items),
    )


def _item_index(campaign: Campaign) -> dict[str, int]:
    return {item.key: index for index, item in enumerate(campaign.items)}


def _open_batch(campaign: Campaign) -> CampaignBatch | None:
    opened = [batch for batch in campaign.batches if not batch.closed]
    if len(opened) > 1:
        raise CampaignError("campaign contains more than one open batch")
    return opened[0] if opened else None


def _batch_id(sequence: int) -> str:
    return f"batch-{sequence:04d}"


def open_next_batch(campaign: Campaign) -> tuple[Campaign, CampaignBatch | None]:
    """Open the next stable batch, or return the existing open batch on resume.

    Unseen pending items are always selected before retryable items. A batch
    never mixes retryable work into a partially filled first-pass batch; retries
    begin only after all pending work has been visited.
    """
    validate_campaign(campaign)

    current = _open_batch(campaign)
    if current is not None:
        return campaign, current

    source = [item for item in campaign.items if item.state == "pending"]
    if not source:
        source = [item for item in campaign.items if item.state == "retryable"]
    if not source:
        return campaign, None

    selected = source[: campaign.batch_size]
    selected_keys = tuple(item.key for item in selected)
    selected_set = set(selected_keys)

    updated_items = tuple(
        replace(
            item,
            state="active",
            attempts=item.attempts + 1,
            detail="",
        )
        if item.key in selected_set
        else item
        for item in campaign.items
    )
    batch = CampaignBatch(
        id=_batch_id(len(campaign.batches) + 1),
        keys=selected_keys,
    )
    updated = replace(
        campaign,
        items=updated_items,
        batches=campaign.batches + (batch,),
    )
    validate_campaign(updated)
    return updated, batch


def record_item_result(
    campaign: Campaign,
    *,
    batch_id: str,
    key: str,
    state: str,
    detail: str = "",
) -> Campaign:
    """Record one item result in the currently open batch.

    The states are intentionally mechanical rather than domain-specific:
    completed means command-specific work finished, retryable means it may be
    selected again after the first pass, and failed is terminal for this
    campaign.
    """
    validate_campaign(campaign)
    key = _validate_key(key)
    if not isinstance(state, str) or state not in _RESULT_STATES:
        allowed = ", ".join(sorted(_RESULT_STATES))
        raise CampaignError(f"campaign result state must be one of: {allowed}")
    if not isinstance(detail, str):
        raise CampaignError("campaign item detail must be a string")
    # Detail is persisted verbatim. Command-specific callers must pass only
    # already-sanitized diagnostics and must never store credentials here.

    batch = _open_batch(campaign)
    if batch is None:
        raise CampaignError("campaign has no open batch")
    if batch.id != batch_id:
        raise CampaignError(
            f"{batch_id}: is not the current open campaign batch ({batch.id})"
        )
    if key not in batch.keys:
        raise CampaignError(f"{key}: is not part of open batch {batch.id}")

    positions = _item_index(campaign)
    if key not in positions:
        raise CampaignError(f"{key}: is not a campaign item")
    position = positions[key]
    item = campaign.items[position]
    if item.state != "active":
        raise CampaignError(
            f"{key}: campaign item is {item.state}, expected active"
        )

    items = list(campaign.items)
    items[position] = replace(item, state=state, detail=detail)
    updated = replace(campaign, items=tuple(items))
    validate_campaign(updated)
    return updated


def close_batch(campaign: Campaign, *, batch_id: str) -> Campaign:
    """Close the current batch after every selected item has a recorded result."""
    validate_campaign(campaign)
    batch = _open_batch(campaign)
    if batch is None:
        raise CampaignError("campaign has no open batch")
    if batch.id != batch_id:
        raise CampaignError(
            f"{batch_id}: is not the current open campaign batch ({batch.id})"
        )

    by_key = {item.key: item for item in campaign.items}
    active = [key for key in batch.keys if by_key[key].state == "active"]
    if active:
        raise CampaignError(
            f"{batch.id}: cannot close with {len(active)} active item(s)"
        )

    batches = tuple(
        replace(candidate, closed=True)
        if candidate.id == batch.id
        else candidate
        for candidate in campaign.batches
    )
    updated = replace(campaign, batches=batches)
    validate_campaign(updated)
    return updated


def campaign_progress(campaign: Campaign) -> CampaignProgress:
    """Return deterministic progress counters for one campaign."""
    validate_campaign(campaign)
    counts = Counter(item.state for item in campaign.items)
    current = _open_batch(campaign)
    return CampaignProgress(
        total=len(campaign.items),
        pending=counts["pending"],
        active=counts["active"],
        completed=counts["completed"],
        retryable=counts["retryable"],
        failed=counts["failed"],
        batches_opened=len(campaign.batches),
        batches_closed=sum(batch.closed for batch in campaign.batches),
        open_batch=current.id if current is not None else None,
    )


def campaign_data(campaign: Campaign) -> dict[str, Any]:
    """Convert validated campaign state to deterministic JSON-compatible data."""
    validate_campaign(campaign)
    return {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "kind": campaign.kind,
        "batch_size": campaign.batch_size,
        "items": [
            {
                "key": item.key,
                "state": item.state,
                "attempts": item.attempts,
                "detail": item.detail,
            }
            for item in campaign.items
        ],
        "batches": [
            {
                "id": batch.id,
                "keys": list(batch.keys),
                "closed": batch.closed,
            }
            for batch in campaign.batches
        ],
    }


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CampaignError(f"{name} must be an object")
    return value


def campaign_from_data(value: Mapping[str, Any]) -> Campaign:
    """Build and strictly validate one campaign from persisted JSON data."""
    root = _mapping(value, "campaign")
    required = {"schema_version", "kind", "batch_size", "items", "batches"}
    missing = required - root.keys()
    unknown = root.keys() - required
    if missing:
        raise CampaignError(
            "campaign missing fields: " + ", ".join(sorted(missing))
        )
    if unknown:
        raise CampaignError(
            "campaign has unknown fields: " + ", ".join(sorted(unknown))
        )

    schema_version = root["schema_version"]
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != CAMPAIGN_SCHEMA_VERSION
    ):
        raise CampaignError(
            "unsupported campaign schema version: "
            f"{schema_version!r}; expected {CAMPAIGN_SCHEMA_VERSION}"
        )

    kind = _validate_kind(root["kind"])
    batch_size = _validate_batch_size(root["batch_size"])

    raw_items = root["items"]
    if not isinstance(raw_items, list):
        raise CampaignError("campaign.items must be a list")
    items: list[CampaignItem] = []
    for index, raw_item in enumerate(raw_items, 1):
        item = _mapping(raw_item, f"campaign.items[{index}]")
        if set(item) != {"key", "state", "attempts", "detail"}:
            raise CampaignError(
                f"campaign.items[{index}] must contain key, state, attempts, and detail"
            )
        key = _validate_key(
            item["key"],
            name=f"campaign.items[{index}].key",
        )
        state = item["state"]
        if not isinstance(state, str) or state not in _ITEM_STATES:
            raise CampaignError(
                f"campaign.items[{index}].state is invalid: {state!r}"
            )
        attempts = item["attempts"]
        if (
            not isinstance(attempts, int)
            or isinstance(attempts, bool)
            or attempts < 0
        ):
            raise CampaignError(
                f"campaign.items[{index}].attempts must be a non-negative integer"
            )
        detail = item["detail"]
        if not isinstance(detail, str):
            raise CampaignError(
                f"campaign.items[{index}].detail must be a string"
            )
        items.append(
            CampaignItem(
                key=key,
                state=state,
                attempts=attempts,
                detail=detail,
            )
        )

    raw_batches = root["batches"]
    if not isinstance(raw_batches, list):
        raise CampaignError("campaign.batches must be a list")
    batches: list[CampaignBatch] = []
    for index, raw_batch in enumerate(raw_batches, 1):
        batch = _mapping(raw_batch, f"campaign.batches[{index}]")
        if set(batch) != {"id", "keys", "closed"}:
            raise CampaignError(
                f"campaign.batches[{index}] must contain id, keys, and closed"
            )
        batch_id = batch["id"]
        expected_id = _batch_id(index)
        if batch_id != expected_id:
            raise CampaignError(
                f"campaign.batches[{index}].id must be {expected_id!r}"
            )
        raw_keys = batch["keys"]
        if (
            not isinstance(raw_keys, list)
            or not raw_keys
            or any(not isinstance(key, str) for key in raw_keys)
        ):
            raise CampaignError(
                f"campaign.batches[{index}].keys must be a non-empty list of strings"
            )
        keys = tuple(
            _validate_key(key, name=f"campaign.batches[{index}].keys")
            for key in raw_keys
        )
        closed = batch["closed"]
        if not isinstance(closed, bool):
            raise CampaignError(
                f"campaign.batches[{index}].closed must be a boolean"
            )
        batches.append(CampaignBatch(id=batch_id, keys=keys, closed=closed))

    campaign = Campaign(
        kind=kind,
        batch_size=batch_size,
        items=tuple(items),
        batches=tuple(batches),
    )
    validate_campaign(campaign)
    return campaign


def validate_campaign(campaign: Campaign) -> None:
    """Validate structural and transition invariants for one campaign."""
    if not isinstance(campaign, Campaign):
        raise CampaignError("value must be a Campaign")
    _validate_kind(campaign.kind)
    _validate_batch_size(campaign.batch_size)

    item_keys: list[str] = []
    for index, item in enumerate(campaign.items, 1):
        if not isinstance(item, CampaignItem):
            raise CampaignError(f"campaign item {index} must be CampaignItem")
        key = _validate_key(item.key, name=f"campaign item {index} key")
        item_keys.append(key)
        if not isinstance(item.state, str) or item.state not in _ITEM_STATES:
            raise CampaignError(f"{key}: invalid campaign item state {item.state!r}")
        if (
            not isinstance(item.attempts, int)
            or isinstance(item.attempts, bool)
            or item.attempts < 0
        ):
            raise CampaignError(f"{key}: attempts must be a non-negative integer")
        if not isinstance(item.detail, str):
            raise CampaignError(f"{key}: detail must be a string")

    if len(set(item_keys)) != len(item_keys):
        raise CampaignError("campaign item keys must be unique")
    known = set(item_keys)

    open_count = 0
    appearances: Counter[str] = Counter()
    open_batch_keys: set[str] = set()
    for index, batch in enumerate(campaign.batches, 1):
        if not isinstance(batch, CampaignBatch):
            raise CampaignError(f"campaign batch {index} must be CampaignBatch")
        expected_id = _batch_id(index)
        if batch.id != expected_id:
            raise CampaignError(
                f"campaign batch {index} id must be {expected_id!r}"
            )
        if not batch.keys:
            raise CampaignError(f"{batch.id}: batch must not be empty")
        if len(batch.keys) > campaign.batch_size:
            raise CampaignError(
                f"{batch.id}: batch exceeds campaign batch_size"
            )
        if len(set(batch.keys)) != len(batch.keys):
            raise CampaignError(f"{batch.id}: batch item keys must be unique")
        unknown = set(batch.keys) - known
        if unknown:
            raise CampaignError(
                f"{batch.id}: unknown campaign item key(s): "
                + ", ".join(sorted(unknown))
            )
        appearances.update(batch.keys)

        if not isinstance(batch.closed, bool):
            raise CampaignError(f"{batch.id}: closed must be a boolean")
        if not batch.closed:
            open_count += 1
            if index != len(campaign.batches):
                raise CampaignError("only the final campaign batch may be open")
            open_batch_keys = set(batch.keys)

    if open_count > 1:
        raise CampaignError("campaign contains more than one open batch")

    for item in campaign.items:
        expected_attempts = appearances[item.key]
        if item.attempts != expected_attempts:
            raise CampaignError(
                f"{item.key}: attempts {item.attempts} do not match "
                f"{expected_attempts} batch appearance(s)"
            )
        if item.state == "pending" and item.attempts != 0:
            raise CampaignError(
                f"{item.key}: pending item must not have batch attempts"
            )
        if item.state == "active":
            if open_count != 1 or item.key not in open_batch_keys:
                raise CampaignError(
                    f"{item.key}: active item must belong to the open batch"
                )

    if open_count == 0 and any(item.state == "active" for item in campaign.items):
        raise CampaignError("campaign has active items without an open batch")
