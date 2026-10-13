"""Target parsing and selection for scheduled price image notifications."""

from __future__ import annotations

import re


def parse_notification_targets(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple, set)):
        values = [str(item).strip() for item in value]
    elif isinstance(value, str):
        values = re.split(r"[\s,，;；]+", value)
    else:
        values = []
    return tuple(dict.fromkeys(item for item in values if item))


def group_umo_parts(umo: str) -> tuple[str, str, str] | None:
    """Return (platform_id, message_type, group_id) for a group UMO."""
    parts = umo.split(":", 2)
    if len(parts) != 3 or not all(part.strip() for part in parts):
        return None
    platform_id, message_type, group_id = (part.strip() for part in parts)
    if message_type.casefold() not in {"group", "groupmessage"}:
        return None
    return platform_id, message_type, group_id


def _canonical_group_umo(umo: str) -> str | None:
    parts = group_umo_parts(umo)
    if not parts:
        return None
    platform_id, _, group_id = parts
    return f"{platform_id.casefold()}:group:{group_id}"


def _normalized_group_umo(umo: str) -> str | None:
    parts = group_umo_parts(umo)
    if not parts:
        return None
    platform_id, _, group_id = parts
    return f"{platform_id}:GroupMessage:{group_id}"


def _target_matches(umo: str, targets: tuple[str, ...]) -> bool:
    parts = group_umo_parts(umo)
    canonical = _canonical_group_umo(umo)
    if not parts or canonical is None:
        return False
    group_id = parts[2]
    for target in targets:
        target_parts = group_umo_parts(target)
        if target_parts and _canonical_group_umo(target) == canonical:
            return True
        if re.fullmatch(r"\d+", target) and target == group_id:
            return True
    return False


def select_notification_targets(
    mode: object,
    configured_targets: object,
    known_group_umos: object,
    proactive_platform_ids: object = (),
) -> list[str]:
    """Select group UMO destinations for whitelist or known-group blacklist mode.

    A numeric group ID resolves to an already observed group UMO. If none has
    been observed, it resolves only when exactly one proactive platform exists.
    """
    targets = parse_notification_targets(configured_targets)
    known_values = (
        known_group_umos
        if isinstance(known_group_umos, (list, tuple, set))
        else ()
    )
    known: dict[str, str] = {}
    for value in known_values:
        umo = str(value).strip()
        canonical = _canonical_group_umo(umo)
        normalized = _normalized_group_umo(umo)
        if canonical and normalized:
            known.setdefault(canonical, normalized)

    selected_mode = str(mode or "whitelist").strip().casefold()
    if selected_mode not in {"whitelist", "blacklist"}:
        selected_mode = "whitelist"

    if selected_mode == "blacklist":
        return sorted(
            umo
            for umo in known.values()
            if not _target_matches(umo, targets)
        )

    selected: dict[str, str] = {}
    platform_ids = tuple(
        dict.fromkeys(
            str(item).strip()
            for item in proactive_platform_ids
            if str(item).strip()
        )
    ) if isinstance(proactive_platform_ids, (list, tuple, set)) else ()

    for target in targets:
        target_parts = group_umo_parts(target)
        if target_parts:
            canonical = _canonical_group_umo(target)
            normalized = _normalized_group_umo(target)
            if canonical and normalized:
                selected.setdefault(canonical, normalized)
            continue

        if not re.fullmatch(r"\d+", target):
            continue

        matches = {
            canonical: umo
            for canonical, umo in known.items()
            if group_umo_parts(umo) and group_umo_parts(umo)[2] == target
        }
        if len(matches) == 1:
            selected.update(matches)
        elif not matches and len(platform_ids) == 1:
            canonical = f"{platform_ids[0].casefold()}:group:{target}"
            selected.setdefault(canonical, f"{platform_ids[0]}:GroupMessage:{target}")

    return sorted(selected.values())
