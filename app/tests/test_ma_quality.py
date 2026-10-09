from types import SimpleNamespace

from music_assistant_models.enums import ContentType
from music_assistant_models.media_items import AudioFormat

from skill.ma_control import quality


def _item(*formats, stream=None):
    mappings = [SimpleNamespace(available=True, audio_format=f) for f in formats]
    return SimpleNamespace(media_item=SimpleNamespace(provider_mappings=mappings),
                           streamdetails=SimpleNamespace(audio_format=stream) if stream else None)


def test_lossless_shows_bit_depth_and_khz():
    assert quality(_item(AudioFormat(content_type=ContentType.FLAC, sample_rate=44100, bit_depth=16))) \
        == "FLAC 16/44.1"
    assert quality(_item(AudioFormat(content_type=ContentType.FLAC, sample_rate=96000, bit_depth=24))) \
        == "FLAC 24/96"


def test_lossy_shows_the_bitrate_if_known():
    assert quality(_item(AudioFormat(content_type=ContentType.MPEG, bit_rate=192))) == "MP3 192k"
    assert quality(_item(AudioFormat(content_type=ContentType.MPEG))) == "MP3"


def test_the_codec_beats_the_container():
    assert quality(_item(AudioFormat(content_type=ContentType.M4A, codec_type=ContentType.ALAC,
                                     sample_rate=48000, bit_depth=24))) == "ALAC 24/48"
    assert quality(_item(AudioFormat(content_type=ContentType.M4A, bit_rate=256))) == "AAC 256k"


def test_unknown_shows_nothing():
    assert quality(None) == ""
    assert quality(_item()) == ""
    assert quality(_item(AudioFormat())) == ""


def test_the_stream_when_the_file_is_unknown():
    stream = AudioFormat(content_type=ContentType.OGG, bit_rate=160)
    assert quality(_item(stream=stream)) == "OGG 160k"
