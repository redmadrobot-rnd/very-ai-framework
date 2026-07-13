"""Профиль mongosh --eval — read-методы (один --eval, без write/$out/$merge/runCommand).
Граница read-only — роль read в самой Mongo; пароль в Secret MONGO_INSPECTOR_PASSWORD.
"""

ID = "mongo"
COMMANDS = ["mongosh"]
DESC = "mongosh --eval (read-методы, без $out/$merge/runCommand)"

_FORBID = [
    "insertOne",
    "insertMany",
    "insert",
    "updateOne",
    "updateMany",
    "update",
    "replaceOne",
    "deleteOne",
    "deleteMany",
    "remove",
    "findOneAndUpdate",
    "findOneAndDelete",
    "findOneAndReplace",
    "findAndModify",
    "bulkWrite",
    "save",
    "drop",
    "dropDatabase",
    "createCollection",
    "createIndex",
    "createIndexes",
    "dropIndex",
    "dropIndexes",
    "renameCollection",
    "mapReduce",
    "$out",
    "$merge",
    "createUser",
    "dropUser",
    "updateUser",
    "grantRolesToUser",
    "revokeRolesFromUser",
    "createRole",
    "dropRole",
    "runCommand",
    "adminCommand",
    "eval",
    "shutdownServer",
    "fsyncLock",
    "compact",
    "reIndex",
    "cloneCollection",
    "copyDatabase",
    "setProfilingLevel",
    "enableSharding",
    "shardCollection",
    "load",
    "Function",
]


def check(argv, g):
    vals, err = g.values(argv, ["--eval", "-e"], file_flags=["-f", "--file"])
    if err:
        return False, err
    if len(vals) != 1:
        return False, "mongosh: ровно один --eval с read-выражением"
    kw = g.forbid_substr(vals[0], _FORBID)
    if kw:
        return False, f"mongosh: запрещённый метод {kw!r} (write/DDL/runCommand)"
    return True, "mongosh (read)"
