"""Home Assistant's own service model, read live and used only where it helps.

``GET /api/services`` publishes every service the installation has registered:
each field, which are required, the selector each one takes, whether the service
answers with a response payload, and -- for the services where it matters -- the
capability a target entity must have before the service can reach it.

The model is deliberately **not** used to generate commands. A generated surface
would be maximally confident and capability-blind, and would duplicate
``service call`` once per service; see AGENTS.md, "The command contract". It is
used for three narrow things instead: explaining a refusal, rendering one
service's field table on request, and checking a capability before dispatch.

Nothing here is cached. An integration added or removed rewrites the model and
nothing signals when, so a stale copy would turn a valid call into a hard
failure -- which is worse than not knowing. Every function below is pure: the
caller does the fetching and decides whether it is worth paying for.
"""

from __future__ import annotations

import difflib

#: Keys Home Assistant reads as targeting rather than as service data. They are
#: never declared as fields, so an unknown-field check has to allow them.
TARGET_KEYS = ("entity_id", "device_id", "area_id", "floor_id", "label_id")

RESPONSE_NONE = "none"
RESPONSE_OPTIONAL = "optional"
RESPONSE_REQUIRED = "required"


def domains(model) -> list:
    """Every service domain the installation has registered, sorted."""
    return sorted(entry.get("domain", "") for entry in _entries(model) if entry.get("domain"))


def find_domain(model, domain: str):
    """The `{"domain": ..., "services": {...}}` entry for one domain, or None."""
    for entry in _entries(model):
        if entry.get("domain") == domain:
            return entry
    return None


def service_names(model, domain: str) -> list:
    entry = find_domain(model, domain)
    services = (entry or {}).get("services")
    return sorted(services) if isinstance(services, dict) else []


def find_service(model, domain: str, service: str):
    """The published description of one service, or None if it is not registered."""
    entry = find_domain(model, domain)
    services = (entry or {}).get("services")
    if not isinstance(services, dict):
        return None
    spec = services.get(service)
    return spec if isinstance(spec, dict) else None


def near(needle: str, candidates, limit: int = 6) -> list:
    """The candidates a wrong name plausibly meant, or nothing.

    Deliberately allowed to come back empty. A caller that pads this out with
    whatever else exists turns "did you mean" into a claim it cannot support;
    listing the rest is a separate sentence, and reads as one.
    """
    pool = [candidate for candidate in candidates if candidate]
    close = difflib.get_close_matches(needle, pool, n=limit, cutoff=0.6)
    prefix = needle[:4]
    for candidate in pool:
        if len(close) >= limit:
            break
        if candidate not in close and len(prefix) >= 3 and candidate.startswith(prefix):
            close.append(candidate)
    return close


def fields(spec) -> list:
    """Every declared field as ``(name, field, section)``, sections flattened.

    A section is a display grouping only: Home Assistant validates the service
    data flat, so a field inside one is passed exactly like an ungrouped one.
    Reporting the section as though it were itself a field would invite an
    agent to send it.
    """
    out = []
    declared = spec.get("fields")
    if not isinstance(declared, dict):
        return out
    for name, field in declared.items():
        field = field if isinstance(field, dict) else {}
        inner = field.get("fields")
        if isinstance(inner, dict):
            for inner_name, inner_field in inner.items():
                out.append((inner_name, inner_field if isinstance(inner_field, dict) else {}, name))
        else:
            out.append((name, field, ""))
    return out


def field_names(spec) -> list:
    return [name for name, _, _ in fields(spec)]


def required_field_names(spec) -> list:
    return [name for name, field, _ in fields(spec) if field.get("required")]


def selector_kind(field) -> str:
    """The one-word type a field's selector declares, or '' when it declares none."""
    selector = field.get("selector")
    if isinstance(selector, dict) and selector:
        return next(iter(selector))
    return ""


def selector_options(field) -> list:
    """The values a `select` selector accepts, so a miss can list them."""
    selector = field.get("selector")
    if not isinstance(selector, dict):
        return []
    config = selector.get("select")
    options = config.get("options") if isinstance(config, dict) else None
    if not isinstance(options, list):
        return []
    return [o.get("value", "") if isinstance(o, dict) else str(o) for o in options]


def response_mode(spec) -> str:
    """Whether the service answers with a payload: none, optional, or required.

    Home Assistant includes the ``response`` key only for a service that can
    answer, and ``optional: false`` means it answers with one or not at all.
    """
    response = spec.get("response")
    if not isinstance(response, dict):
        return RESPONSE_NONE
    return RESPONSE_OPTIONAL if response.get("optional") else RESPONSE_REQUIRED


def target_domains(spec) -> list:
    """The entity domains a service says it can be aimed at.

    Empty means "no published restriction", which is the honest reading of both
    a service that declares no entity target and one whose entity filter names
    no domain -- `homeassistant.turn_on` reaches every domain by design. Filter
    a resolved target through this rather than through the service's own domain
    name: an integration is free to register a service that acts on another
    domain's entities, and several do.
    """
    target = spec.get("target")
    if not isinstance(target, dict):
        return []
    entries = target.get("entity")
    if not isinstance(entries, list):
        return []
    out: list = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        declared = entry.get("domain")
        if declared is None:
            return []
        declared = [declared] if isinstance(declared, str) else list(declared)
        out.extend(name for name in declared if isinstance(name, str))
    return sorted(set(out))


def feature_masks(spec, domain: str) -> list:
    """The capability masks a target entity must satisfy, if any are published.

    Home Assistant resolves the enum names in ``services.yaml`` to integers
    before publishing them, and treats the list as alternatives: an entity
    qualifies when it satisfies **any** one mask. That is what encodes an
    upstream fallback -- ``media_player.volume_up`` publishes both VOLUME_SET
    and VOLUME_STEP because core backs a player that cannot step with one that
    can set, so a speaker with only the first still qualifies. Reading the list
    as a conjunction would gate exactly the behaviour that works today.

    Read only when the entity filter names exactly this service's own domain. A
    service that targets another domain's entities publishes that other domain's
    capability names, which say nothing about the entity it will reach; and a
    value that did not resolve to an integer disables the gate rather than
    guessing at it.
    """
    target = spec.get("target")
    if not isinstance(target, dict):
        return []
    entries = target.get("entity")
    if not isinstance(entries, list) or len(entries) != 1:
        return []
    entry = entries[0] if isinstance(entries[0], dict) else {}
    declared = entry.get("domain")
    declared = [declared] if isinstance(declared, str) else list(declared or [])
    if declared != [domain]:
        return []
    masks = entry.get("supported_features")
    if not isinstance(masks, list) or not masks:
        return []
    resolved = [m for m in masks if isinstance(m, int) and not isinstance(m, bool) and m > 0]
    return resolved if len(resolved) == len(masks) else []


def satisfies(features, masks) -> bool:
    """Home Assistant's own rule: any one mask fully present in the entity's bits."""
    if not masks:
        return True
    if not isinstance(features, int) or isinstance(features, bool):
        return False
    return any(features & mask == mask for mask in masks)


def entity_features(state) -> int:
    """The `supported_features` bits a state reports, absent counting as none."""
    value = (state.get("attributes") or {}).get("supported_features")
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def _entries(model) -> list:
    return [entry for entry in model if isinstance(entry, dict)] if isinstance(model, list) else []
