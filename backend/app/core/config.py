from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    project_name: str = "Voice Agent API"
    api_v1_str: str = "/api/v1"

    class Config:
        env_file = ".env"

settings = Settings()
