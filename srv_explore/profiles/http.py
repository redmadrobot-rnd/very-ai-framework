"""Профиль curl — только GET/HEAD во внутреннюю сеть. Движок curl (g.curl).

Egress наружу режется на уровне назначения: приватные IP/loopback/имена без точек —
свободно; внешние хосты — только из EXTERNAL_ALLOW. Это закрывает и эксфильтрацию
через GET на публичный хост. Выключить сеть агенту целиком = выключить этот профиль.
"""

ID = "http"
COMMANDS = ["curl"]
DESC = "curl (только GET/HEAD, внутренняя сеть; внешнее по allowlist)"

# Внешние хосты-исключения (напр. "api.example.com"); пусто = только внутренняя сеть.
EXTERNAL_ALLOW: list[str] = []


def check(argv, g):
    return g.curl(argv, internal_only=True, external_allow=EXTERNAL_ALLOW)
