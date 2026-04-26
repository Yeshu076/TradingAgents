import os

from tradingagents.ops.bootstrap_env import bootstrap_environment


def test_bootstrap_environment_syncs_dhan(tmp_path):
    for key in [
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "ANTHROPIC_API_KEY",
        "XAI_API_KEY",
        "OPENROUTER_API_KEY",
        "DELTA_REST_BASE_URL",
        "DHAN_CLIENT_ID",
        "DHAN_ACCESS_TOKEN",
        "DHAN_NIFTY_SECURITY_ID",
        "DHAN_NIFTY_UNDERLYING_SEGMENT",
        "ALPHA_VANTAGE_API_KEY",
    ]:
        os.environ.pop(key, None)

    env_file = tmp_path / ".env"
    env_file.write_text("GOOGLE_API_KEY=test_google\n", encoding="utf-8")

    dhan_cfg = tmp_path / "dhan_config.json"
    dhan_cfg.write_text(
        """
{
  "credentials": {
    "client_id": "12345",
    "access_token": "token_abc"
  },
  "underlying": {
    "security_id": 13,
    "exchange_segment": "IDX_I"
  }
}
""".strip(),
        encoding="utf-8",
    )

    result = bootstrap_environment(
        env_file=env_file,
        sync_dhan_config=dhan_cfg,
        write_env=True,
    )

    by_key = {item.key: item for item in result.items}

    assert by_key["GOOGLE_API_KEY"].value == "test_google"
    assert by_key["DHAN_CLIENT_ID"].value == "12345"
    assert by_key["DHAN_ACCESS_TOKEN"].value == "token_abc"
    assert by_key["DELTA_REST_BASE_URL"].value == "https://api.india.delta.exchange"

    saved = env_file.read_text(encoding="utf-8")
    assert "DHAN_CLIENT_ID=12345" in saved
    assert "DHAN_ACCESS_TOKEN=token_abc" in saved
