"""The app's log lines name the part of the app they come from."""

import logging

import pytest

from skill import log_setup


@pytest.fixture
def component():
    real, handlers = logging.getLogRecordFactory(), list(logging.getLogger().handlers)
    log_setup.setup()

    def of(name, path):
        return logging.getLogger(name).makeRecord(name, logging.INFO, path, 1, "Spielt", None, None).component
    yield of
    logging.setLogRecordFactory(real)
    logging.getLogger().handlers[:] = handlers


@pytest.mark.parametrize("name, component_name", [("skill.ma.client", "Skill"), ("ask_sdk_runtime.dispatch", "Skill"),
                                                  ("web.ma_routes", "API"), ("web.status", "UI/Web")])
def test_each_log_line_names_its_part_of_the_app(component, name, component_name):
    path = f"/app/src/{name.replace('.', '/')}.py"
    assert component(name, path) == component_name
