"""Профиль mongosh --eval — read-методы. Движок mongo (g.mongo).

Граница read-only — роль read в самой Mongo:
    db.createUser({ user:'inspector', pwd:passwordPrompt(),
                    roles:[{role:'read', db:'app'}] })
    // НЕ давать readWrite/dbAdmin/clusterAdmin/root
Пароль — Environment Secret MONGO_INSPECTOR_PASSWORD. Гард режет мутирующие
методы, стадии записи ($out/$merge), runCommand; --file и множественный --eval — deny.
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
    return g.mongo(
        argv,
        eval_flags=["--eval", "-e"],
        file_flags=["-f", "--file"],
        forbid=_FORBID,
    )
