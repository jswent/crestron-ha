"""Preserve provider catalogs while keeping provider-specific selections scoped."""

import base64
import json
from copy import copy

from homeassistant.components.media_player import BrowseMedia
from homeassistant.exceptions import ServiceValidationError

_SCHEME = "crestron-source://"
_PREFIX = f"{_SCHEME}v1/"


def unwrap_media_id(
    entity_id: str, media_type: str | None, media_id: str | None
) -> tuple[str | None, str | None]:
    """Restore exact provider arguments; native/global IDs remain unchanged."""
    if media_id is None or not media_id.startswith(_SCHEME):
        return media_type, media_id
    try:
        if not media_id.startswith(_PREFIX):
            raise ValueError
        payload = json.loads(
            base64.b64decode(media_id[len(_PREFIX) :], altchars=b"-_", validate=True)
        )
        if (
            not isinstance(payload, list)
            or len(payload) != 3
            or not all(isinstance(item, str) for item in payload)
        ):
            raise ValueError
        provider, original_type, original_id = payload
        if original_type != media_type:
            raise ValueError
    except (ValueError, UnicodeError) as err:
        raise ServiceValidationError(
            "Invalid source media selection; browse the source again"
        ) from err
    if provider != entity_id:
        raise ServiceValidationError(
            "This media belongs to a different source; select that source or browse again"
        )
    return original_type, original_id


def scope_browse_media(
    node: BrowseMedia | dict, entity_id: str, *, can_play: bool
) -> BrowseMedia:
    """Copy the tree, preserving native HA media-source UI and provider artwork."""
    if isinstance(node, dict):
        # HA still accepts legacy dictionary browse responses.
        node = BrowseMedia(
            **{
                key: node[key]
                for key in (
                    "title",
                    "media_class",
                    "media_content_type",
                    "media_content_id",
                    "can_play",
                    "can_expand",
                )
            },
            **{
                key: node[key]
                for key in (
                    "children",
                    "children_media_class",
                    "thumbnail",
                    "not_shown",
                    "can_search",
                    "search_media_classes",
                )
                if key in node
            },
        )
    if (
        not isinstance(node, BrowseMedia)
        or not isinstance(node.media_content_id, str)
        or not isinstance(node.media_content_type, str)
    ):
        raise ValueError("Invalid provider browse response")
    result = copy(node)
    result.can_play = node.can_play and can_play
    if not node.media_content_id.startswith("media-source://"):
        payload = json.dumps(
            [entity_id, node.media_content_type, node.media_content_id],
            separators=(",", ":"),
        ).encode()
        result.media_content_id = _PREFIX + base64.urlsafe_b64encode(payload).decode(
            "ascii"
        )
        result.can_search = False
        result.search_media_classes = None
    if node.children is not None:
        result.children = [
            scope_browse_media(child, entity_id, can_play=can_play)
            for child in node.children
        ]
    return result
