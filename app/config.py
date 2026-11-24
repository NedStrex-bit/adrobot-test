from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    demo_mode: bool = True
    keitaro_url: str = 'https://tracker.example.com'
    keitaro_api_key: str = ''
    database_url: str = 'sqlite:///./adrobot.db'

settings = Settings()
