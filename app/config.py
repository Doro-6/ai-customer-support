from dotenv import load_dotenv
from pydantic_settings import BaseSettings

# Load .env into the environment, so Pydantic AI also finds DEEPSEEK_API_KEY.
load_dotenv()


class Settings(BaseSettings):
    database_url: str = ""
    mail_address: str = ""
    mail_auth_code: str = ""
    imap_host: str = "imap.qq.com"
    imap_port: int = 993
    smtp_host: str = "smtp.qq.com"
    smtp_port: int = 465
    classifier_model: str = "deepseek:deepseek-flash"
    agent_model: str = "deepseek:deepseek-v4-pro"
    poll_interval_seconds: int = 60
    pipeline_dry_run: bool = False
    dashboard_username: str = "admin"
    dashboard_password: str = ""


settings = Settings()
