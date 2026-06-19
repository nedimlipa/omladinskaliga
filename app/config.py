from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str
    SECRET_KEY: str = "promijeni-ovo-na-random-string-min-32-karaktera"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480  # 8 sati

    class Config:
        env_file = ".env"

settings = Settings()
