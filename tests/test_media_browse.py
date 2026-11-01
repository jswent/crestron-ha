"""Browse and select through HA's real WebSocket, entity and service contracts."""

import asyncio
import base64
import json
from copy import deepcopy

import pytest
from homeassistant.components.media_player import DATA_COMPONENT, BrowseMedia
from homeassistant.components.media_player import MediaPlayerEntityFeature as Feature
from homeassistant.components.media_player.errors import BrowseError
from homeassistant.components.media_source.models import BrowseMediaSource
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from tests import test_media_bindings
from tests.test_media_bindings import OTHER, SONOS
from tests.test_media_controls import BASE, PLAYBACK, SourcePlayer

zone = test_media_bindings.zone
ALBUM = "A:ALBUMARTIST/Årtist/Album?x=1&y=two"
FAVORITE = "FV:2/42"
NATIVE = "media-source://media_source/local/My Music/track.mp3"
ART = b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"><rect width="20" height="20" fill="teal"/></svg>'


class BrowseSource(SourcePlayer):
    _attr_supported_features = PLAYBACK | Feature.BROWSE_MEDIA | Feature.SEARCH_MEDIA

    def __init__(self, entity_id):
        super().__init__(entity_id, "playing")
        self.browse_calls = []
        self.image_calls = []
        self.browse_error = None
        self.browse_started = None
        self.browse_release = None
        self.browse_loop = None
        self.legacy = False
        self.invalid = False
        art = self.get_browse_image_url("album", ALBUM, "front")
        self.favorite = BrowseMedia(
            media_class="music",
            media_content_type="favorite_item_id",
            media_content_id=FAVORITE,
            title="A favorite",
            can_play=True,
            can_expand=False,
            thumbnail=art,
        )
        self.album = BrowseMedia(
            media_class="album",
            media_content_type="album",
            media_content_id=ALBUM,
            title="An album",
            can_play=True,
            can_expand=True,
            children=[self.favorite],
            thumbnail=art,
            not_shown=3,
            can_search=True,
            search_media_classes=["music"],
        )
        self.native = BrowseMediaSource(
            domain="media_source",
            identifier="local/My Music/track.mp3",
            media_class="music",
            media_content_type="audio/mpeg",
            title="Local track",
            can_play=True,
            can_expand=False,
            can_search=True,
        )
        self.root = BrowseMedia(
            media_class="directory",
            media_content_type="library",
            media_content_id="",
            title="Provider catalog",
            can_play=False,
            can_expand=True,
            children=[self.album, self.native],
            children_media_class="directory",
        )

    async def async_browse_media(self, media_content_type=None, media_content_id=None):
        self.browse_calls.append((media_content_type, media_content_id))
        if self.browse_started:
            self.browse_started.set()
            await self.browse_release.wait()
        if self.browse_error:
            raise self.browse_error
        if self.browse_loop:
            return (
                await self.hass.data[DATA_COMPONENT]
                .get_entity(self.browse_loop)
                .async_browse_media()
            )
        if self.invalid:
            return {"invalid": "catalog"}
        if media_content_id is None or (media_content_type, media_content_id) == (
            "library",
            "",
        ):
            item = self.root
        elif (media_content_type, media_content_id) == ("album", ALBUM):
            item = self.album
        elif media_content_id and media_content_id.startswith("media-source://"):
            item = self.native
        else:
            raise BrowseError("Item not found")
        return item.as_dict() if self.legacy else item

    async def async_get_browse_image(
        self, media_content_type, media_content_id, media_image_id=None
    ):
        self.image_calls.append((media_content_type, media_content_id, media_image_id))
        return ART, "image/svg+xml"


@pytest.fixture
async def browsers(hass, zone):
    component = hass.data[DATA_COMPONENT]
    for entity_id in (SONOS, OTHER):
        hass.states.async_remove(entity_id)
    first, second = BrowseSource(SONOS), BrowseSource(OTHER)
    await component.async_add_entities([first, second])
    await hass.async_block_till_done()
    yield first, second
    for entity_id in (SONOS, OTHER):
        if component.get_entity(entity_id):
            await component.async_remove_entity(entity_id)


@pytest.fixture
async def browse_ws(hass, hass_ws_client, zone, browsers):
    client = await hass_ws_client(hass)
    sequence = 0

    async def request(command):
        nonlocal sequence
        sequence += 1
        await client.send_json({"id": sequence, **command})
        return await client.receive_json()

    return request


@pytest.mark.parametrize("zone", [False, True], indirect=True)
async def test_websocket_catalog_navigation_and_selected_item_playback(
    hass, zone, browsers, browse_ws
):
    _, _, entity_id, _, _ = zone
    first, second = browsers
    original = deepcopy(first.root.as_dict())
    assert (
        hass.states.get(entity_id).attributes["supported_features"]
        == BASE | PLAYBACK | Feature.BROWSE_MEDIA
    )
    response = await browse_ws(
        {"type": "media_player/browse_media", "entity_id": entity_id}
    )
    assert response["success"]
    root = response["result"]
    assert root["title"] == "Provider catalog"
    album = root["children"][0]
    assert album["media_content_id"].startswith("crestron-source://v1/")
    assert album["media_content_type"] == "album"
    assert album["thumbnail"] == first.album.thumbnail
    assert album["can_play"] and album["can_expand"]
    assert not album["can_search"]
    assert album["search_media_classes"] is None
    assert root["children"][1]["media_content_id"] == NATIVE
    assert root["children"][1]["can_search"] is True
    response = await browse_ws(
        {
            "type": "media_player/browse_media",
            "entity_id": entity_id,
            "media_content_type": "album",
            "media_content_id": album["media_content_id"],
        }
    )
    assert response["success"]
    assert response["result"]["not_shown"] == 3
    favorite = response["result"]["children"][0]
    assert favorite["media_content_type"] == "favorite_item_id"
    result = await browse_ws(
        {
            "type": "call_service",
            "domain": "media_player",
            "service": "play_media",
            "target": {"entity_id": entity_id},
            "service_data": {
                "media_content_type": favorite["media_content_type"],
                "media_content_id": favorite["media_content_id"],
                "enqueue": "add",
                "extra": {"title": "Favorite"},
            },
        }
    )
    assert result["success"]
    assert first.browse_calls == [(None, None), ("album", ALBUM)]
    assert first.calls[-1][1] == {
        "media_type": "favorite_item_id",
        "media_id": FAVORITE,
        "enqueue": "add",
        "extra": {"title": "Favorite"},
    }
    assert not second.calls and not second.browse_calls
    assert first.root.as_dict() == original
    # A playable album/folder is distinct from expanding that same node.
    await hass.services.async_call(
        "media_player",
        "play_media",
        {
            "entity_id": entity_id,
            "media_content_type": "album",
            "media_content_id": album["media_content_id"],
        },
        blocking=True,
    )
    assert first.calls[-1][1]["media_id"] == ALBUM


async def test_provider_artwork_proxy_is_preserved_and_fetchable(
    hass, zone, browsers, hass_client
):
    _, _, entity_id, _, _ = zone
    first, _ = browsers
    catalog = await hass.data[DATA_COMPONENT].get_entity(entity_id).async_browse_media()
    url = catalog.children[0].thumbnail
    assert url == first.album.thumbnail
    assert f"/api/media_player_proxy/{SONOS}/browse_media/" in url
    client = await hass_client()
    response = await client.get(url)
    assert response.status == 200
    assert await response.read() == ART
    assert first.image_calls == [("album", ALBUM, "front")]


async def test_browse_service_and_legacy_provider_responses(hass, zone, browsers):
    _, _, entity_id, _, _ = zone
    first, _ = browsers
    first.legacy = True
    result = await hass.services.async_call(
        "media_player",
        "browse_media",
        {"entity_id": entity_id},
        blocking=True,
        return_response=True,
    )
    root = result[entity_id]
    assert isinstance(root, BrowseMedia)
    assert root.children[0].media_content_type == "album"
    folder = (
        await hass.data[DATA_COMPONENT]
        .get_entity(entity_id)
        .async_browse_media("album", root.children[0].media_content_id)
    )
    assert folder.children[0].title == "A favorite"


async def test_scoped_selection_rejects_wrong_source_but_survives_entity_reload(
    hass, zone, browsers
):
    entry, _, entity_id, _, feedback_source = zone
    first, second = browsers
    entity = hass.data[DATA_COMPONENT].get_entity(entity_id)
    album = (await entity.async_browse_media()).children[0]
    feedback_source(2)
    with pytest.raises(BrowseError, match="different source"):
        await entity.async_browse_media("album", album.media_content_id)
    with pytest.raises(ServiceValidationError, match="different source"):
        await entity.async_play_media("album", album.media_content_id)
    assert not second.calls and not second.browse_calls
    feedback_source(1)
    mapping = entry.subentries["zone"]
    hass.config_entries.async_update_subentry(
        entry, mapping, data={**mapping.data, "name": "Renamed zone"}
    )
    await hass.async_block_till_done()
    replacement = hass.data[DATA_COMPONENT].get_entity(entity_id)
    assert replacement is not entity
    await replacement.async_play_media("album", album.media_content_id)
    assert first.calls[-1][1]["media_id"] == ALBUM
    assert (await replacement.async_browse_media()).children[
        0
    ].media_content_id == album.media_content_id


async def test_native_media_source_ids_keep_frontend_semantics_and_provider_resolution(
    hass, zone, browsers
):
    _, _, entity_id, _, feedback_source = zone
    first, second = browsers
    entity = hass.data[DATA_COMPONENT].get_entity(entity_id)
    native = (await entity.async_browse_media()).children[1]
    await entity.async_browse_media(native.media_content_type, native.media_content_id)
    assert first.browse_calls[-1] == ("audio/mpeg", NATIVE)
    feedback_source(2)
    await entity.async_play_media(native.media_content_type, native.media_content_id)
    assert second.calls[-1][1]["media_id"] == NATIVE
    tts = "media-source://tts/cloud?message=hello&language=en"
    await entity.async_play_media("audio/mp3", tts, announce=True)
    assert second.calls[-1][1] == {
        "media_type": "audio/mp3",
        "media_id": tts,
        "announce": True,
        "extra": {},
    }


@pytest.mark.parametrize(
    "condition",
    [
        "unsupported",
        "unavailable",
        "unknown",
        "unbound",
        "off",
        "disconnected",
        "pending",
        "missing",
    ],
)
async def test_browsing_is_only_exposed_for_eligible_supported_sources(
    hass, zone, browsers, browse_ws, condition
):
    _, bridge, entity_id, service, feedback_source = zone
    first, _ = browsers
    if condition == "unsupported":
        first._attr_supported_features = PLAYBACK
        first.async_write_ha_state()
    elif condition in ("unavailable", "unknown"):
        hass.states.async_set(SONOS, condition)
    elif condition == "unbound":
        feedback_source(3)
    elif condition == "off":
        bridge.feedback("digital", 1, False)
    elif condition == "disconnected":
        bridge.feedback("available", None, False)
    elif condition == "pending":
        await service("select_source", source="Sonos 2")
    else:
        await hass.data[DATA_COMPONENT].async_remove_entity(SONOS)
    await hass.async_block_till_done()
    assert (
        not hass.states.get(entity_id).attributes["supported_features"]
        & Feature.BROWSE_MEDIA
    )
    response = await browse_ws(
        {"type": "media_player/browse_media", "entity_id": entity_id}
    )
    assert not response["success"] and response["error"]["code"] == "not_supported"
    with pytest.raises(BrowseError):
        await hass.data[DATA_COMPONENT].get_entity(entity_id).async_browse_media()
    assert not first.browse_calls


async def test_browse_without_play_support_has_no_playable_items(hass, zone, browsers):
    _, _, entity_id, _, _ = zone
    first, _ = browsers
    first._attr_supported_features = Feature.BROWSE_MEDIA
    first.async_write_ha_state()
    await hass.async_block_till_done()
    root = await hass.data[DATA_COMPONENT].get_entity(entity_id).async_browse_media()
    assert (
        hass.states.get(entity_id).attributes["supported_features"]
        == BASE | Feature.BROWSE_MEDIA
    )
    assert not any(child.can_play for child in root.children)
    assert not root.children[0].children[0].can_play
    assert first.album.can_play


@pytest.mark.parametrize(
    "change",
    [
        "switch",
        "away_back",
        "off_on",
        "disconnect_reconnect",
        "pending",
        "unavailable_recover",
        "remove_binding",
        "capability_removed",
        "provider_replaced",
    ],
)
async def test_late_browse_results_are_discarded(hass, zone, browsers, change):
    entry, bridge, entity_id, service, feedback_source = zone
    first, _ = browsers
    entity = hass.data[DATA_COMPONENT].get_entity(entity_id)
    first.browse_started, first.browse_release = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(entity.async_browse_media())
    await first.browse_started.wait()
    try:
        if change in ("switch", "away_back"):
            feedback_source(2)
            if change == "away_back":
                feedback_source(1)
        elif change == "off_on":
            bridge.feedback("digital", 1, False)
            bridge.feedback("digital", 1, True)
        elif change == "disconnect_reconnect":
            bridge.feedback("available", None, False)
            bridge.feedback("available", None, True)
        elif change == "pending":
            await service("select_source", source="Sonos 2")
        elif change == "unavailable_recover":
            first._attr_available = False
            first.async_write_ha_state()
            first._attr_available = True
            first.async_write_ha_state()
            await asyncio.sleep(0)
        elif change == "remove_binding":
            mapping = entry.subentries["zone"]
            hass.config_entries.async_update_subentry(
                entry, mapping, data={**mapping.data, "source_bindings": {}}
            )
            # The browse task is a plain asyncio task, not HA-owned background work.
            await hass.async_block_till_done()
        elif change == "capability_removed":
            first._attr_supported_features = PLAYBACK
            first.async_write_ha_state()
        else:
            await hass.data[DATA_COMPONENT].async_remove_entity(SONOS)
            await hass.data[DATA_COMPONENT].async_add_entities([BrowseSource(SONOS)])
        first.browse_release.set()
        with pytest.raises(BrowseError, match="changed or became unavailable"):
            await task
    finally:
        first.browse_release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_volume_and_duplicate_source_feedback_do_not_cancel_browsing(
    hass, zone, browsers
):
    _, bridge, entity_id, _, feedback_source = zone
    first, _ = browsers
    first.browse_started, first.browse_release = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(
        hass.data[DATA_COMPONENT].get_entity(entity_id).async_browse_media()
    )
    await first.browse_started.wait()
    bridge.feedback("analog", 4, 20000)
    feedback_source(1)
    first.browse_release.set()
    assert (await task).title == "Provider catalog"


@pytest.mark.parametrize(
    "failure",
    [
        BrowseError("missing folder"),
        ServiceValidationError("invalid folder"),
        ConnectionError("offline"),
        HomeAssistantError("operation failed"),
        NotImplementedError(),
    ],
)
async def test_provider_errors_and_recovery(hass, zone, browsers, failure):
    _, _, entity_id, _, _ = zone
    first, _ = browsers
    entity = hass.data[DATA_COMPONENT].get_entity(entity_id)
    first.browse_error = failure
    with pytest.raises(BrowseError):
        await entity.async_browse_media()
    if isinstance(failure, (BrowseError, ServiceValidationError)):
        assert entity.supported_features & Feature.BROWSE_MEDIA
    else:
        assert not entity.supported_features & Feature.BROWSE_MEDIA
    first.browse_error = None
    if isinstance(failure, NotImplementedError):
        assert entity.supported_features & Feature.PLAY_MEDIA
        first.async_write_ha_state()
        await hass.async_block_till_done()
        assert not entity.supported_features & Feature.BROWSE_MEDIA
        await hass.data[DATA_COMPONENT].async_remove_entity(SONOS)
        await hass.data[DATA_COMPONENT].async_add_entities([BrowseSource(SONOS)])
    else:
        first.async_write_ha_state()
    await hass.async_block_till_done()
    assert (await entity.async_browse_media()).title == "Provider catalog"


async def test_bad_catalog_is_rejected_without_disabling_other_controls(
    hass, zone, browsers
):
    _, _, entity_id, _, _ = zone
    first, _ = browsers
    first.invalid = True
    entity = hass.data[DATA_COMPONENT].get_entity(entity_id)
    with pytest.raises(BrowseError, match="invalid media catalog"):
        await entity.async_browse_media()
    assert entity.supported_features & Feature.PLAY_MEDIA
    assert not entity.supported_features & Feature.BROWSE_MEDIA


@pytest.mark.parametrize(
    "payload",
    [
        "crestron-source://v9/bad",
        "crestron-source://v1/!",
        "crestron-source://v1/" + base64.urlsafe_b64encode(b"not json").decode(),
        "crestron-source://v1/"
        + base64.urlsafe_b64encode(json.dumps([SONOS, "album", 12]).encode()).decode(),
    ],
)
async def test_invalid_scoped_ids_never_reach_provider(hass, zone, browsers, payload):
    _, _, entity_id, _, _ = zone
    first, _ = browsers
    entity = hass.data[DATA_COMPONENT].get_entity(entity_id)
    with pytest.raises(BrowseError, match="Invalid source media"):
        await entity.async_browse_media("album", payload)
    with pytest.raises(ServiceValidationError, match="Invalid source media"):
        await entity.async_play_media("album", payload)
    assert not first.calls and not first.browse_calls
    assert entity.supported_features & Feature.BROWSE_MEDIA


async def test_scoped_content_type_cannot_be_reinterpreted(hass, zone, browsers):
    _, _, entity_id, _, _ = zone
    first, _ = browsers
    entity = hass.data[DATA_COMPONENT].get_entity(entity_id)
    album = (await entity.async_browse_media()).children[0]
    with pytest.raises(ServiceValidationError, match="Invalid source media"):
        await entity.async_play_media("music", album.media_content_id)
    assert not first.calls


async def test_indirect_browse_loop_is_rejected(hass, zone, browsers):
    _, _, entity_id, _, _ = zone
    first, _ = browsers
    first.browse_loop = entity_id
    with pytest.raises(BrowseError, match="control loop"):
        async with asyncio.timeout(2):
            await hass.data[DATA_COMPONENT].get_entity(entity_id).async_browse_media()
    assert len(first.browse_calls) == 1
