"""The skill: Alexa's requests go to the handlers in alexa/."""

from ask_sdk.standard import StandardSkillBuilder

from .alexa import handlers

sb = StandardSkillBuilder()
handlers.register(sb)
