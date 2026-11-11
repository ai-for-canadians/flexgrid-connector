import pytest

from flexgrid_connector import FlexGridConfig, FlexGridConfigError


def test_from_env_reads_the_four_variables():
    config = FlexGridConfig.from_env(
        {
            "FLEXGRID_BASE_URL": "http://box.example:8000/v1/",
            "FLEXGRID_MODEL": "some-model",
            "FLEXGRID_API_KEY": "k-123",
            "FLEXGRID_TIMEOUT": "12.5",
        }
    )
    assert config.base_url == "http://box.example:8000/v1"  # trailing slash dropped
    assert config.model == "some-model"
    assert config.api_key == "k-123"
    assert config.timeout == 12.5


def test_from_env_uses_the_process_environment_by_default(monkeypatch):
    monkeypatch.setenv("FLEXGRID_BASE_URL", "http://box.example/v1")
    assert FlexGridConfig.from_env().base_url == "http://box.example/v1"


def test_only_the_url_is_required():
    config = FlexGridConfig.from_env({"FLEXGRID_BASE_URL": "http://box.example/v1"})
    assert (config.model, config.api_key, config.timeout) == (None, None, 60.0)


@pytest.mark.parametrize("env", [{}, {"FLEXGRID_BASE_URL": ""}, {"FLEXGRID_BASE_URL": "   "}])
def test_missing_url_is_a_config_error_that_names_the_variable(env):
    with pytest.raises(FlexGridConfigError, match="FLEXGRID_BASE_URL is not set"):
        FlexGridConfig.from_env(env)


@pytest.mark.parametrize("url", ["box.example:8000", "ftp://box.example/v1", "http://", "http://box.example:port/v1"])
def test_malformed_url_is_rejected(url):
    with pytest.raises(FlexGridConfigError, match="FLEXGRID_BASE_URL"):
        FlexGridConfig(base_url=url)


@pytest.mark.parametrize("raw", ["soon", "0", "-3"])
def test_bad_timeout_is_rejected_rather_than_defaulted(raw):
    with pytest.raises(FlexGridConfigError, match="(?i)timeout"):
        FlexGridConfig.from_env({"FLEXGRID_BASE_URL": "http://box.example/v1", "FLEXGRID_TIMEOUT": raw})


def test_blank_model_and_key_count_as_unset():
    config = FlexGridConfig(base_url="http://box.example/v1", model="  ", api_key="")
    assert config.model is None and config.api_key is None


def test_api_key_never_shows_in_repr():
    config = FlexGridConfig(base_url="http://box.example/v1", api_key="k-secret")
    assert "k-secret" not in repr(config)


def test_safe_url_drops_embedded_credentials():
    config = FlexGridConfig(base_url="http://user:hunter2@box.example:8000/v1")
    assert config.safe_url == "http://box.example:8000/v1"
    assert "hunter2" not in config.safe_url
