"""Procrastinate tasks (queue in PostgreSQL). Task names are fixed strings shared by every
process: the api and the telegram process only defer them, the worker runs `agent.*` and the
telegram process runs `telegram.*` (docs/reply.md, «Очередь»)."""
