"""Профиль redis — конфиг подготовки, НЕ парсер команд."""

ID = "redis"
DESC = "Redis — read-only ACL-юзер"
COMMANDS = ["redis-cli"]
PACKAGES = ["redis-tools"]
CREDS_ENV = "REDIS_INSPECTOR_DSN"

SETUP = "ACL SETUSER :role on >:pw ~* &* +@read -@dangerous resetchannels"
VERIFY = "SET _srvx_probe 1"
