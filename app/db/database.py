import aiosqlite
import asyncio
from app.core.config import get_settings
import os

settings = get_settings()


async def get_db():
    os.makedirs(os.path.dirname(settings.sqlite_db_path), exist_ok=True)
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        db.row_factory = aiosqlite.Row
        yield db


async def init_db():
    os.makedirs(os.path.dirname(settings.sqlite_db_path), exist_ok=True)
    async with aiosqlite.connect(settings.sqlite_db_path) as db:
        await db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            hashed_password TEXT NOT NULL,
            role TEXT DEFAULT 'agent',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS customers (
            customer_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            age INTEGER,
            city TEXT,
            preferred_channel TEXT,
            preferred_language TEXT,
            segment TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS policies (
            policy_id TEXT PRIMARY KEY,
            customer_id TEXT,
            policy_type TEXT,
            sum_assured INTEGER,
            annual_premium INTEGER,
            premium_due_date DATE,
            payment_mode TEXT,
            fund_value INTEGER,
            status TEXT DEFAULT 'ACTIVE',
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
        );

        CREATE TABLE IF NOT EXISTS policy_state (
            policy_id TEXT PRIMARY KEY,
            current_node TEXT DEFAULT 'ORCHESTRATOR',
            last_channel TEXT,
            waiting_for TEXT,
            sentiment_score REAL DEFAULT 0.0,
            distress_flag INTEGER DEFAULT 0,
            objection_count INTEGER DEFAULT 0,
            mode TEXT DEFAULT 'AI',
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS interactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            policy_id TEXT,
            channel TEXT,
            message_direction TEXT,
            content TEXT,
            sentiment_score REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS escalation_cases (
            case_id INTEGER PRIMARY KEY AUTOINCREMENT,
            policy_id TEXT,
            escalation_reason TEXT,
            priority_score REAL,
            assigned_to TEXT,
            status TEXT DEFAULT 'OPEN',
            sla_deadline TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            policy_id TEXT,
            action_type TEXT,
            action_reason TEXT,
            triggered_by TEXT,
            prompt_version INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS workflow_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            policy_id TEXT,
            node_name TEXT,
            content TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS agent_telemetry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            policy_id TEXT,
            agent_name TEXT,
            execution_time_ms REAL,
            tokens_input INTEGER DEFAULT 0,
            tokens_output INTEGER DEFAULT 0,
            status TEXT DEFAULT 'SUCCESS',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS ab_tests (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            segment TEXT,
            channel TEXT,
            variant_a TEXT,
            variant_b TEXT,
            sends_a INTEGER DEFAULT 0,
            sends_b INTEGER DEFAULT 0,
            conv_a INTEGER DEFAULT 0,
            conv_b INTEGER DEFAULT 0,
            status TEXT DEFAULT 'RUNNING',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_interactions_policy ON interactions(policy_id);
        CREATE INDEX IF NOT EXISTS idx_policy_state_node ON policy_state(current_node);
        CREATE INDEX IF NOT EXISTS idx_escalation_status ON escalation_cases(status);
        CREATE INDEX IF NOT EXISTS idx_audit_policy ON audit_logs(policy_id);
        CREATE INDEX IF NOT EXISTS idx_ab_tests_status ON ab_tests(status);
        """)
        await db.commit()
    print(f"[DB] SQLite initialized at {settings.sqlite_db_path}")
