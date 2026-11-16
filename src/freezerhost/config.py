from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Read from the environment (and .env). See .env.example."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    site_password: str = "frysdisk"          # shared with attendees
    admin_password: str = "change-me"        # organisers only
    session_secret: str = ""                 # empty: generated and kept in data_dir
    data_dir: Path = Path("data")
    public_url: str = ""                     # e.g. https://freezers.example.com; empty: taken from the request

    freezers_per_store: int = 10
    current_version: str = "1.4.2"
    target_version: str = "2.0.0"

    tick_seconds: float = 1.0
    reading_interval_seconds: float = 5.0
    download_seconds: float = 12.0
    install_seconds: float = 8.0
    offline_return_seconds: float = 180.0    # frys-07 comes back this long after the first install command

    attack_auto: bool = True                 # false: waves are released by the organiser only
    attack_delay_seconds: float = 60.0       # stage 1 done -> wave 1
    wave_interval_seconds: float = 240.0     # between waves
    attack_retry_seconds: float = 40.0       # between attempts of one released wave
    legit_interval_seconds: float = 45.0     # between genuine technician requests
    request_ttl_seconds: float = 90.0
    pass_ttl_seconds: int = 900
