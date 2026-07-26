from tools import spotify_link


def test_bulk_playlist_action_searches_and_adds_distinct_tracks(monkeypatch):
    monkeypatch.setattr(
        spotify_link,
        "_find_playlist_by_name",
        lambda name: {"ok": True, "result": {"id": "playlist-1", "name": name}, "error": None},
    )
    monkeypatch.setattr(
        spotify_link,
        "_search_track",
        lambda query, limit: {
            "ok": True,
            "result": {
                "tracks": [
                    {"uri": "spotify:track:one"},
                    {"uri": "spotify:track:two"},
                    {"uri": "spotify:track:one"},
                ]
            },
            "error": None,
        },
    )
    captured = {}

    def fake_add(playlist_id, uris):
        captured.update(playlist_id=playlist_id, uris=uris)
        return {"ok": True, "result": {"added_count": len(uris)}, "error": None}

    monkeypatch.setattr(spotify_link, "_add_tracks_to_playlist", fake_add)

    result = spotify_link.tool_entry({
        "action": "add_tracks_to_playlist",
        "playlist_name": "Ostranauts",
        "track_query": "upbeat techno",
        "limit": 10,
    })

    assert result["ok"] is True
    assert captured == {
        "playlist_id": "playlist-1",
        "uris": ["spotify:track:one", "spotify:track:two"],
    }