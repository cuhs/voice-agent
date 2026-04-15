from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    project_name: str = "Voice Agent API"
    api_v1_str: str = "/api/v1"
    deepgram_api_key: str | None = None
    groq_api_key: str | None = None

    class Config:
        env_file = ".env"

settings = Settings()
