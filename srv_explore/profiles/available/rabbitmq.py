"""Профиль rabbitmqctl — только read-подкоманды (list_*/status/метрики).
Граница read-only — юзер с тегом monitoring и read-only permissions.
"""

ID = "rabbitmq"
COMMANDS = ["rabbitmqctl"]
DESC = "rabbitmqctl (list_*/status/cluster_status)"

_READS = [
    "list_queues",
    "list_exchanges",
    "list_bindings",
    "list_connections",
    "list_channels",
    "list_consumers",
    "list_users",
    "list_vhosts",
    "list_permissions",
    "list_topic_permissions",
    "list_user_permissions",
    "list_user_topic_permissions",
    "list_policies",
    "list_operator_policies",
    "list_parameters",
    "list_global_parameters",
    "list_vhost_limits",
    "list_user_limits",
    "list_unresponsive_queues",
    "list_hashes",
    "list_ciphers",
    "status",
    "cluster_status",
    "node_health_check",
    "environment",
    "report",
    "ping",
    "version",
    "list_feature_flags",
    "list_deprecated_features",
]
_VALUE_FLAGS = [
    "-n",
    "--node",
    "-t",
    "--timeout",
    "--erlang-cookie",
    "--rabbitmq-home",
    "--formatter",
]


def check(argv, g):
    return g.verbs(argv, value_flags=_VALUE_FLAGS, allow=_READS)
