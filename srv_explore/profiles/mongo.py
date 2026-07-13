"""Профиль mongo — конфиг подготовки, НЕ парсер команд."""

ID = "mongo"
DESC = "MongoDB — read-роль"
COMMANDS = ["mongosh"]
PACKAGES = ["mongodb-mongosh"]
CREDS_ENV = "MONGO_INSPECTOR_DSN"

SETUP = "db.createUser({user: ':role', pwd: ':pw', roles: [{role: 'read', db: ':db'}]})"
VERIFY = "db._srvx_probe.insertOne({x: 1})"
