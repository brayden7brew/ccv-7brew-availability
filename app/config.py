from functools import lru_cache
from zoneinfo import ZoneInfo
from pydantic import model_validator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    database_url: str = 'sqlite:///./portal.db'
    ops_backend_url: str = ''
    ops_integration_key: str = ''
    secret_key: str
    environment: str = 'development'
    allowed_hosts: str = 'localhost,127.0.0.1,testserver'
    secure_cookies: bool = False
    dry_run: bool = True
    wiw_mode: str = 'demo'
    wiw_token: str = ''
    wiw_webhook_secret: str = ''
    wiw_developer_key: str = ''
    wiw_auto_enroll: bool = False
    wiw_auto_enroll_location: str = ''
    wiw_context_user_id: int = 0
    wiw_account_id: int = 0
    business_timezone: str = 'America/New_York'
    minimum_notice_days: int = 1
    session_hours: int = 8
    email_enabled: bool = False
    email_provider: str = 'smtp'
    microsoft_tenant_id: str = ''
    microsoft_client_id: str = ''
    microsoft_client_secret: str = ''
    email_from: str = 'alerts@rva7brew.com'
    public_base_url: str = ''
    smtp_host: str = ''
    smtp_port: int = 587
    smtp_username: str = ''
    smtp_password: str = ''
    smtp_ssl: bool = False
    availability_request_limit_30_days: int = Field(default=0, ge=0)

    @model_validator(mode='after')
    def validate_settings(self):
        ZoneInfo(self.business_timezone)
        if self.ops_backend_url:
            from urllib.parse import urlsplit
            url = urlsplit(self.ops_backend_url)
            if url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
                raise ValueError('OPS_BACKEND_URL must be an HTTPS origin')
            if len(self.ops_integration_key) < 32:
                raise ValueError('OPS_INTEGRATION_KEY must contain at least 32 random characters')
        if self.email_enabled:
            from urllib.parse import urlsplit
            url = urlsplit(self.public_base_url)
            if url.scheme != 'https' or not url.hostname or url.hostname in ('localhost','127.0.0.1') or url.username or url.password or url.query or url.fragment:
                raise ValueError('Email requires a public HTTPS portal URL')
            if self.email_provider not in ('smtp','microsoft_graph'):
                raise ValueError('Choose smtp or microsoft_graph for EMAIL_PROVIDER')
            if not self.email_from or '\n' in self.email_from or '\r' in self.email_from:
                raise ValueError('Email requires a valid sender')
            if self.email_provider=='smtp' and not self.smtp_host:
                raise ValueError('SMTP host is required')
            if self.email_provider=='microsoft_graph':
                from uuid import UUID
                UUID(self.microsoft_tenant_id)
                UUID(self.microsoft_client_id)
                if not self.microsoft_client_secret:
                    raise ValueError('Microsoft application credential is required')
        if self.wiw_auto_enroll and not self.wiw_auto_enroll_location.strip():
            raise ValueError('Automatic WIW enrollment requires a portal location')
        if len(self.secret_key) < 32:
            raise ValueError('SECRET_KEY must contain at least 32 random characters')
        if self.wiw_mode not in ('demo', 'live'):
            raise ValueError('WIW_MODE must be demo or live')
        if not self.dry_run and self.wiw_mode != 'live':
            raise ValueError('Real writes require WIW_MODE=live')
        if self.wiw_mode == 'live' and not (self.wiw_token and self.wiw_context_user_id and self.wiw_account_id):
            raise ValueError('Live mode requires token, context user ID and account ID')
        if self.environment == 'production':
            if not self.secure_cookies or not self.database_url.startswith(('postgresql', 'postgres://')):
                raise ValueError('Production requires secure cookies and PostgreSQL')
            if '*' in self.allowed_hosts or self.wiw_mode == 'demo':
                raise ValueError('Production requires explicit hosts and live WIW reads')
        if self.database_url.startswith('postgres://'):
            self.database_url = self.database_url.replace('postgres://', 'postgresql+psycopg://', 1)
        elif self.database_url.startswith('postgresql://'):
            self.database_url = self.database_url.replace('postgresql://', 'postgresql+psycopg://', 1)
        return self

@lru_cache
def settings():
    return Settings()
