# -*- coding: utf-8 -*-
"""The interaction model with voice commands, for one locale.

The template (models/voice/<language>.json) holds the voice intents and
their phrases. It is merged into the locale's current model at Amazon, so
the invocation name and any other intents stay as they are.
"""

import copy
import json
from pathlib import Path

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "models" / "voice"


def template_for(locale):
    """The voice template for locale ("en-AU" -> en.json), or None."""
    path = TEMPLATE_DIR / f"{locale.split('-')[0]}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def merge(base, template, types):
    """base (an interactionModel document) with template's intents and types.

    types: the library slot types (voice_names.build_types). Intents and
    types of the same name are replaced, retired ones removed; built-in
    intents are added if missing.
    """
    model = copy.deepcopy(base) if base else {}
    language = model.setdefault("interactionModel", {}).setdefault("languageModel", {})
    intents = language.setdefault("intents", [])
    ours = {intent["name"] for intent in template["intents"]} | set(template.get("retiredIntents", []))
    intents[:] = [intent for intent in intents if intent.get("name") not in ours]
    intents.extend(copy.deepcopy(template["intents"]))
    have = {intent.get("name") for intent in intents}
    for name in template.get("builtInIntents", []):
        if name not in have:
            intents.append({"name": name, "samples": []})
    # Phrases other intents must keep: MA's spoken "ask ... to play audio"
    # would otherwise land on "play something random", and requests meant
    # for Alexa ("turn off the light") on a music command.
    for intent in intents:
        extra = template.get("extraSamples", {}).get(intent.get("name"), [])
        samples = intent.setdefault("samples", [])
        samples.extend(sample for sample in extra if sample not in samples)
    if template.get("fallbackSensitivity"):
        language.setdefault("modelConfiguration", {})["fallbackIntentSensitivity"] = {
            "level": template["fallbackSensitivity"]}
    new_types = copy.deepcopy(template.get("types", [])) + list(types)
    replaced = {slot_type["name"] for slot_type in new_types}
    language["types"] = [t for t in language.get("types", []) if t.get("name") not in replaced] + new_types
    return model
