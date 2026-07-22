"""Профиль rabbitmq — конфиг подготовки, НЕ парсер команд."""

ID = "rabbitmq"
DESC = "RabbitMQ — monitoring-юзер (read)"
COMMANDS = ["rabbitmqctl"]
PACKAGES = []  # rabbitmqctl обычно уже на ноде
CREDS_ENV = "RABBITMQ_INSPECTOR_DSN"

SETUP = (
    "rabbitmqctl set_user_tags :role monitoring; "
    "rabbitmqctl set_permissions -p / :role '^$' '^$' '.*'"
)
VERIFY = None  # тег monitoring + пустые config/write permissions — запись невозможна
