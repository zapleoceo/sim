"""Business logic of the agent: inbox, outbox, media, understanding, context, reply, memory.

Services know only the ports (sim/ports.py) and the database session; they are wired through
`Deps` (sim/services/deps.py). Logic is described in docs/reply.md."""
