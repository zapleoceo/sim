"""0008 is additive, reversible and never changes PR12 permanent receipts."""
import sqlite3

from alembic import command

from tests.test_migrations import alembic_config, columns


def test_0008_roundtrip_preserves_existing_messages_and_receipts(tmp_path):
    db = tmp_path / "activity.db"
    cfg = alembic_config(db)
    # Options are already loaded by alembic_config. Keep migrations from replacing
    # pytest's logging configuration or disabling application loggers for later tests.
    cfg.config_file_name = None
    command.upgrade(cfg, "0007")
    with sqlite3.connect(db) as con:
        # Legacy data can exist before any operator attribution is available.
        con.execute("INSERT INTO message (id, dialog_id, direction, author, text, delivery, created_at) "
                    "VALUES (10, 999, 'out', 'operator', 'legacy', 'sent', '2026-11-07')")
        con.execute("INSERT INTO outbound_send_intent (message_id, fingerprint, created_at, sent_at) "
                    "VALUES (10, 'permanent', '2026-11-07', '2026-11-07')")
    command.upgrade(cfg, "0008")
    assert {"operator_id", "operator_name"} <= columns(db, "message")
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT text, operator_id, operator_name FROM message").fetchone() == ("legacy", None, None)
        assert any(row[2] == "admin_user" and row[3] == "operator_id" and row[6] == "SET NULL"
                   for row in con.execute("PRAGMA foreign_key_list(message)"))
        indexes = {row[1] for row in con.execute("PRAGMA index_list(message)")}
        assert {"ix_message_created_at_id", "ix_message_dialog_created_at_id"} <= indexes
    command.downgrade(cfg, "0007")
    assert not {"operator_id", "operator_name"} & columns(db, "message")
    command.upgrade(cfg, "0008")
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT text FROM message").fetchone() == ("legacy",)
        assert con.execute("SELECT fingerprint, sent_at FROM outbound_send_intent").fetchone() == ("permanent", "2026-11-07")
