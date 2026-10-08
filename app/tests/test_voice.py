import json
from types import SimpleNamespace as NS

from skill import voice_commands, voice_match, voice_model, voice_names


def _values(types, name):
    return next(t["values"] for t in types if t["name"] == name)


def test_slot_value_synonyms_without_accents_and_brackets():
    entry = voice_names.slot_value("Szép (Remastered 2011)")
    assert entry["name"]["value"] == "Szép Remastered 2011"      # Amazon is picky about characters
    assert entry["name"]["synonyms"] == ["Szep Remastered 2011", "Szép", "Szep", "sayp"]
    assert "synonyms" not in voice_names.slot_value("Plain")["name"]
    assert len(voice_names.slot_value("x" * 300)["name"]["value"]) == voice_names.MAX_VALUE_CHARS
    assert voice_names.slot_value("!!!") is None


def test_hungarian_names_spelled_as_english_ears_hear_them():
    assert voice_names.sounds_like("Deák Bill Gyula") == "deahk Bill dyula"
    assert voice_names.sounds_like("Hobo Blues Band") is None


def test_misheard_hungarian_names_still_match_by_sound():
    assert voice_match.score("deck bill julia", "Deák Bill Gyula") > voice_match.MIN_SCORE
    assert voice_match.score("deck bill julia", "Deák Bill Gyula") > voice_match.score("deck bill julia", "Bill Evans")
    assert voice_match.score("soreny levente", "Szörényi Levente") > 0.9


def test_types_dedupe_and_any_type_without_songs():
    types, counts = voice_names.build_types({
        "playlist": ["Chill"], "artist": ["Deák Bill Gyula", "deák bill gyula"],
        "album": ["Chill"], "song": ["Only A Song"]})
    assert counts == {"playlist": 1, "artist": 1, "album": 1, "song": 1}
    assert [v["name"]["value"] for v in _values(types, "MA_NAME")] == ["Chill", "Deák Bill Gyula"]
    assert [t["name"] for t in types] == ["MA_PLAYLIST", "MA_ARTIST", "MA_ALBUM", "MA_SONG", "MA_NAME"]


def test_budget_keeps_kinds_in_order(monkeypatch):
    monkeypatch.setattr(voice_names, "MAX_VALUES", 5)
    types, counts = voice_names.build_types({
        "playlist": ["P1"], "artist": ["A1", "A2"], "album": ["B1"], "song": ["S1"]})
    assert counts == {"playlist": 1, "artist": 1, "album": 0, "song": 0}
    assert _values(types, "MA_ALBUM") == [{"name": {"value": "music assistant"}}]   # never empty


def test_digest_changes_with_names():
    a, _ = voice_names.build_types({"artist": ["A"]})
    b, _ = voice_names.build_types({"artist": ["B"]})
    assert voice_names.digest(a) == voice_names.digest(voice_names.build_types({"artist": ["A"]})[0])
    assert voice_names.digest(a) != voice_names.digest(b)
    assert (voice_names.digest(voice_names.build_types({"artist": ["A", "B"]})[0])
            == voice_names.digest(voice_names.build_types({"artist": ["B", "A"]})[0]))


def _c(kind, name, artist=""):
    return {"kind": kind, "name": name, "artist": artist}


def test_pick_exact_then_fuzzy_then_nothing():
    candidates = [_c("artist", "Deák Bill Gyula"), _c("artist", "Bill Evans")]
    assert voice_match.pick("deak bill gyula", candidates)[0]["name"] == "Deák Bill Gyula"
    assert voice_match.pick("deck bill gyula", candidates)[0]["name"] == "Deák Bill Gyula"
    assert voice_match.pick("beatles", [_c("artist", "The Beatles")])[0]["name"] == "The Beatles"
    match, score = voice_match.pick("metallica", candidates)
    assert match is None and score < voice_match.MIN_SCORE


def test_pick_by_artist_and_kind_order_on_ties():
    songs = [_c("song", "Yesterday", "Someone Else"), _c("song", "Yesterday", "The Beatles")]
    assert voice_match.pick("yesterday", songs, artist="beatles")[0]["artist"] == "The Beatles"
    same = [_c("song", "Abbey Road"), _c("album", "Abbey Road")]
    assert voice_match.pick("abbey road", same, kinds=("album", "song"))[0]["kind"] == "album"


def test_merge_keeps_invocation_and_replaces_ours():
    base = {"interactionModel": {"languageModel": {
        "invocationName": "music assistant",
        "intents": [{"name": "PlayAudio", "samples": ["play"]},
                    {"name": "PlayArtist", "samples": ["old"]},
                    {"name": "PlayAnything", "samples": ["{mode} {name}"]},
                    {"name": "AMAZON.NextIntent", "samples": []}],
        "types": [{"name": "MA_ARTIST", "values": []}, {"name": "OTHER", "values": []}]}}}
    template = voice_model.template_for("en-AU")
    model = voice_model.merge(base, template, [{"name": "MA_ARTIST", "values": [{"name": {"value": "A"}}]}])
    language = model["interactionModel"]["languageModel"]
    names = [i["name"] for i in language["intents"]]
    assert language["invocationName"] == "music assistant"
    assert names.count("PlayArtist") == 1 and names.count("AMAZON.NextIntent") == 1
    assert "AMAZON.ShuffleOnIntent" in names and "PlayAudio" in names
    # MA's spoken fallback says "ask ... to play audio": that must stay PlayAudio.
    assert "play audio" in next(i for i in language["intents"] if i["name"] == "PlayAudio")["samples"]
    assert [t["name"] for t in language["types"]] == ["OTHER", "MA_MODE", "MA_KIND", "MA_FAVORITE_KIND", "MA_ARTIST"]
    assert base["interactionModel"]["languageModel"]["intents"][1]["samples"] == ["old"]
    # The old "play X" goes; requests for Alexa teach the fallback.
    assert "PlayAnything" not in names
    fallback = next(i for i in language["intents"] if i["name"] == "AMAZON.FallbackIntent")
    assert "set volume to two" in fallback["samples"]
    assert language["modelConfiguration"]["fallbackIntentSensitivity"] == {"level": "HIGH"}


def test_template_uses_only_known_slot_types():
    template = voice_model.template_for("en-US")
    known = set(voice_names.SLOT_TYPES.values()) | {voice_names.ANY_TYPE} | {t["name"] for t in template["types"]}
    used = {s["type"] for i in template["intents"] for s in i["slots"]}
    assert used <= known
    for intent in template["intents"]:
        slots = {s["name"] for s in intent["slots"]}
        for sample in intent["samples"]:
            assert {part.split("}")[0] for part in sample.split("{")[1:]} <= slots, sample
    assert voice_model.template_for("de-DE") is None
    assert set(voice_commands.INTENTS) <= {i["name"] for i in template["intents"]} | set(template["retiredIntents"])


def _slot(value, resolved=None):
    resolutions = None
    if resolved:
        resolutions = NS(resolutions_per_authority=[NS(
            status=NS(code=NS(value="ER_SUCCESS_MATCH")), values=[NS(value=NS(name=resolved))])])
    return NS(value=value, resolutions=resolutions)


def test_request_prefers_the_resolved_name_and_reads_shuffle():
    wanted = voice_commands.request_of("PlayAlbum", {
        "album": _slot("abby road", "Abbey Road"), "mode": _slot("mix", "shuffle"), "artist": _slot(None)})
    assert wanted == {"meant": True, "heard": "Abbey Road", "artist": None, "kinds": ("album",),
                      "option": "replace", "shuffle": True, "radio": False}
    played = voice_commands.request_of("PlayArtist", {"artist": _slot("x"), "mode": _slot("start", "play")})
    assert played["meant"] and played["shuffle"] is False
    radio = voice_commands.request_of("PlayRadio", {"name": _slot("x")})
    assert radio["meant"] and radio["radio"] and radio["shuffle"] is None
    queued = voice_commands.request_of("Queue", {"kind": _slot(None), "name": _slot(None), "song": _slot("hey jude")})
    assert queued["meant"] and queued["heard"] == "hey jude" and queued["kinds"] == ("song",)
    assert queued["option"] == "add"
    album = voice_commands.request_of("Queue", {"kind": _slot("album", "album"), "name": _slot("abbey road")})
    assert album["meant"] and album["heard"] == "abbey road" and album["kinds"] == ("album",)
    # "queue track X" fills the kind, not the song.
    track = voice_commands.request_of("Queue", {"kind": _slot("track", "song"), "name": _slot("hey jude"),
                                                "song": _slot(None)})
    assert track["meant"] and track["heard"] == "hey jude" and track["kinds"] == ("song",)
    assert voice_commands.request_of("AMAZON.NextIntent", {}) is None


def test_request_without_its_kind_or_play_word_is_not_meant():
    # What Alexa made of "turn off lamp" and "set volume to 2" with the player page open.
    assert not voice_commands.request_of("Queue", {"kind": _slot(None), "name": _slot("off lamp")})["meant"]
    assert not voice_commands.request_of("Queue", {"kind": _slot("volume"), "name": _slot("to two")})["meant"]
    assert not voice_commands.request_of("PlayNext", {"kind": _slot(None), "name": _slot("off lamp")})["meant"]
    assert not voice_commands.request_of("PlaySong", {"mode": _slot("set"), "song": _slot("volume to 2")})["meant"]
    assert not voice_commands.request_of("PlayArtist", {"artist": _slot("lamp")})["meant"]


def test_request_from_the_model_before_kind_words():
    # Amazon keeps the old model until the new one is uploaded.
    queued = voice_commands.request_of("Queue", {"name": _slot("abbey road"), "song": _slot(None)})
    assert queued["meant"] and queued["heard"] == "abbey road" and len(queued["kinds"]) == 4
    song = voice_commands.request_of("PlayNext", {"name": _slot(None), "song": _slot("hey jude")})
    assert song["meant"] and song["heard"] == "hey jude" and song["kinds"] == ("song",)
    played = voice_commands.request_of("PlayAnything", {"name": _slot("abbey road"), "mode": _slot("play", "play")})
    assert played["meant"] and played["option"] == "replace"


def test_answers():
    _ = lambda s: s
    match = {"kind": "album", "name": "Abbey Road", "artist": "The Beatles"}
    wanted = voice_commands.request_of("PlayAlbum", {"album": _slot("x"), "mode": _slot("shuffle", "shuffle")})
    assert voice_commands.answer(_, wanted, match) == "Shuffling Abbey Road by The Beatles"
    wanted = voice_commands.request_of("PlayNext", {"kind": _slot("artist", "artist"), "name": _slot("x")})
    assert voice_commands.answer(_, wanted, {"kind": "artist", "name": "X", "artist": ""}) == "Playing X next"


def test_free_names_come_after_their_kind():
    """Every sample with a free name also has a kind word, so requests meant for Alexa don't match it."""
    template = voice_model.template_for("en-AU")
    kind_words = ("artist", "album", "song", "track", "playlist", "radio", "{kind}", " like ")
    for intent in template["intents"]:
        for sample in intent["samples"]:
            if any(f"{{{slot}}}" in sample for slot in ("name", "artist", "album", "song", "playlist")):
                assert any(word in sample for word in kind_words), sample


def test_template_is_small():
    assert len(json.dumps(voice_model.template_for("en-AU")).encode()) < 20_000


def test_upload_merges_each_locale_with_a_template_once(monkeypatch, tmp_path):
    import pytest
    pytest.importorskip("ask_sdk_model_runtime")
    from skill import model_upload
    monkeypatch.setenv("DEVICE_MAPPING_PATH", str(tmp_path / "m.json"))
    monkeypatch.setenv("SKILL_ID", "skill")
    monkeypatch.setattr(model_upload, "configured", lambda: True)
    monkeypatch.setattr(model_upload, "BUILD_POLL_S", 0)
    sent = {}
    done = {"interactionModel": {l: {"lastUpdateRequest": {"status": "SUCCEEDED"}}
                                 for l in ("en-US", "de-DE")}}
    client = NS(
        get_skill_manifest_v1=lambda *_: {"manifest": {"publishingInformation": {
            "locales": {"en-US": {}, "de-DE": {}}}}},
        get_interaction_model_v1=lambda *_: {"interactionModel": {"languageModel": {
            "invocationName": "mine", "intents": [], "types": []}}},
        set_interaction_model_v1=lambda _id, _stage, locale, model: sent.__setitem__(locale, model),
        get_skill_status_v1=lambda *_, **__: done)
    monkeypatch.setattr(model_upload, "_client", lambda: client)
    voice = {"hash": "h1", "types": [{"name": "MA_ARTIST", "values": [{"name": {"value": "A"}}]}]}
    assert model_upload.upload(voice) == ["en-US"]                 # no German template yet
    language = sent["en-US"]["interactionModel"]["languageModel"]
    assert language["invocationName"] == "mine"
    assert "PlayArtist" in {i["name"] for i in language["intents"]}
    sent.clear()
    assert model_upload.upload(voice) == [] and not sent           # same names: no upload


def test_upload_failing_locale_does_not_hold_up_the_others(monkeypatch, tmp_path):
    import pytest
    pytest.importorskip("ask_sdk_model_runtime")
    from skill import model_upload
    monkeypatch.setenv("DEVICE_MAPPING_PATH", str(tmp_path / "m.json"))
    monkeypatch.setenv("SKILL_ID", "skill")
    monkeypatch.setattr(model_upload, "configured", lambda: True)
    monkeypatch.setattr(model_upload, "BUILD_POLL_S", 0)
    sent = []
    status = {"interactionModel": {
        "en-AU": {"lastUpdateRequest": {"status": "SUCCEEDED"}},
        "en-US": {"lastUpdateRequest": {"status": "FAILED", "buildDetails": {"steps": [
            {"name": "LANGUAGE_MODEL_QUICK_BUILD", "status": "SUCCEEDED"},
            {"name": "LANGUAGE_MODEL_FULL_BUILD", "status": "FAILED"}]}}}}}
    client = NS(
        get_skill_manifest_v1=lambda *_: {"manifest": {"publishingInformation": {
            "locales": {"en-US": {}, "en-AU": {}}}}},
        get_interaction_model_v1=lambda *_: {"interactionModel": {"languageModel": {"intents": [], "types": []}}},
        set_interaction_model_v1=lambda _id, _stage, locale, model: sent.append(locale),
        get_skill_status_v1=lambda *_, **__: status)
    monkeypatch.setattr(model_upload, "_client", lambda: client)
    voice = {"hash": "h1", "types": []}
    with pytest.raises(model_upload.UploadFailed, match=r"en-US build failed in \['LANGUAGE_MODEL_FULL_BUILD'\]"):
        model_upload.upload(voice)
    assert sent == ["en-AU", "en-US"]
    sent.clear()
    with pytest.raises(model_upload.UploadFailed):
        model_upload.upload(voice)
    assert sent == ["en-US"]                                       # en-AU is up to date
    sent.clear()

    def down(_id, _stage, locale, model):
        sent.append(locale)
        raise ConnectionError("down")
    client.set_interaction_model_v1 = down
    with pytest.raises(model_upload.UploadFailed, match="en-AU: ConnectionError.*en-US: ConnectionError"):
        model_upload.upload({"hash": "h2", "types": []})
    assert sent == ["en-AU", "en-US"]                              # one error doesn't stop the rest


def test_upload_needs_credentials(monkeypatch):
    from skill import model_upload
    monkeypatch.setattr(model_upload, "get_env_secret", lambda name: None)
    assert model_upload.upload({"hash": "h", "types": []}) is None


def test_closest_library_name_by_sound():
    names = ["Bill Evans", "Deák Bill Gyula", "Zorán"]
    assert voice_match.closest("deck bill julia", names) == "Deák Bill Gyula"
    assert voice_match.closest("something else entirely", names) is None
    assert voice_match.closest("x", []) is None
    assert voice_match.closest("deck", names) == "Deák Bill Gyula"
    assert voice_match.closest("bill evans", names) == "Bill Evans"


def test_favorite_kind_from_what_was_said():
    slots = {"what": NS(value="record", resolutions=None)}
    assert voice_commands.favorite_kind(slots) == "album"
    assert voice_commands.favorite_kind({}) == "song"
    assert voice_commands.FAVORITE_INTENTS == {"AddFavorite": True, "RemoveFavorite": False}
