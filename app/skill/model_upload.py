# -*- coding: utf-8 -*-
"""Uploading the voice model to Amazon with the Skill Management API.

Needs a Login with Amazon security profile and a refresh token with the
skill management scopes, set as LWA_CLIENT_ID, LWA_CLIENT_SECRET and
LWA_REFRESH_TOKEN. Without them nothing is uploaded; the status page
offers the model as a download instead.

For each locale of the skill that has a voice template, the current model
is fetched from Amazon, merged with the template and the library names,
and uploaded. A hash of the names and templates is kept per locale in
voice_upload.json, so unchanged ones aren't uploaded again. A locale that
fails doesn't hold up the others.
"""

import hashlib
import json
import logging
import os
import time

from env_secrets import get_env_secret
from . import voice_model

logger = logging.getLogger(__name__)

STAGE = "development"
BUILD_TIMEOUT_S = 600
BUILD_POLL_S = 10
_ENV = ("LWA_CLIENT_ID", "LWA_CLIENT_SECRET", "LWA_REFRESH_TOKEN")


class UploadFailed(Exception):
    """Amazon refused the model or its build failed."""


def configured():
    return all(get_env_secret(name) for name in _ENV)


def _state_path():
    mapping = os.environ.get("DEVICE_MAPPING_PATH", "/app/instance_data/device_players.json")
    return os.path.join(os.path.dirname(mapping), "voice_upload.json")


def _uploaded():
    """locale -> hash of what was last uploaded for it."""
    try:
        with open(_state_path(), encoding="utf-8") as f:
            locales = json.load(f).get("locales")
    except (OSError, ValueError):
        return {}
    return locales if isinstance(locales, dict) else {}


def _save(uploaded):
    tmp = _state_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"locales": uploaded, "at": int(time.time())}, f)
    os.replace(tmp, _state_path())


def _key(voice_types):
    """What was uploaded: the names and the voice templates."""
    digest = hashlib.sha256(voice_types["hash"].encode())
    for path in sorted(voice_model.TEMPLATE_DIR.glob("*.json")):
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def _client():
    """The SDK client, answering with Amazon's JSON as is.

    The SDK's own classes reject values newer than the SDK (a new build
    step name broke the status), and rebuilding the model from them could
    drop fields.
    """
    from ask_smapi_sdk import StandardSmapiClientBuilder
    client = StandardSmapiClientBuilder(*(get_env_secret(name) for name in _ENV)).client()
    invoke = client.invoke

    def plain_invoke(**kwargs):
        if kwargs.get("response_type"):
            kwargs["response_type"] = "object"
        return invoke(**kwargs)

    client.invoke = plain_invoke
    return client


def _locales(client, skill_id):
    manifest = client.get_skill_manifest_v1(skill_id, STAGE)
    return sorted(manifest["manifest"]["publishingInformation"].get("locales") or {})


def _wait_for_build(client, skill_id, locale):
    deadline = time.monotonic() + BUILD_TIMEOUT_S
    while time.monotonic() < deadline:
        time.sleep(BUILD_POLL_S)
        status = client.get_skill_status_v1(skill_id, resource="interactionModel")
        request = (status.get("interactionModel") or {}).get(locale, {}).get("lastUpdateRequest") or {}
        if request.get("status") == "SUCCEEDED":
            return
        if request.get("status") == "FAILED":
            steps = (request.get("buildDetails") or {}).get("steps") or []
            failed = [step.get("name") for step in steps if step.get("status") == "FAILED"]
            errors = [e.get("message") for e in request.get("errors") or []]
            raise UploadFailed(f"{locale} build failed in {failed}: {errors}")
    raise UploadFailed(f"{locale} build didn't finish in {BUILD_TIMEOUT_S} s")


def upload(voice_types):
    """Upload voice_types ({"hash", "types"} from voice_lists) if they changed.

    Returns the locales uploaded, [] if nothing needed it, None if not configured.
    """
    if not configured():
        return None
    if not voice_types:
        return []
    from ask_sdk_model_runtime.exceptions import ServiceException
    key = _key(voice_types)
    uploaded = _uploaded()
    skill_id = os.environ["SKILL_ID"]
    client = _client()
    done, failed = [], []
    try:
        locales = _locales(client, skill_id)
    except ServiceException as e:
        raise UploadFailed(f"Amazon said {e.status_code}: {e}") from e
    for locale in locales:
        template = voice_model.template_for(locale)
        if not template or uploaded.get(locale) == key:
            continue
        try:
            base = client.get_interaction_model_v1(skill_id, STAGE, locale)
            model = voice_model.merge(base, template, voice_types["types"])
            client.set_interaction_model_v1(skill_id, STAGE, locale, model)
            _wait_for_build(client, skill_id, locale)
        except ServiceException as e:
            failed.append(f"{locale}: Amazon said {e.status_code}: {e}")
            continue
        except UploadFailed as e:
            failed.append(str(e))
            continue
        except Exception as e:      # a network error or an odd model must not stop the other locales
            failed.append(f"{locale}: {e!r}")
            continue
        logger.info("Voice model uploaded for %s", locale)
        uploaded[locale] = key
        _save(uploaded)
        done.append(locale)
    if failed:
        raise UploadFailed("; ".join(failed))
    return done
