-- =====================================================
-- LEDGERCORE: INITIAL POSTGRESQL SCHEMA
-- =====================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- 1. ENUMS

CREATE TYPE account_type AS ENUM (
    'asset',
    'liability',
    'equity',
    'revenue',
    'expense'
);

CREATE TYPE entry_side AS ENUM ('debit', 'credit');

CREATE TYPE payment_status AS ENUM (
    'pending',
    'processing',
    'succeeded',
    'failed',
    'needs_review'
);

CREATE TYPE ledger_transaction_type AS ENUM (
    'payment',
    'refund',
    'reversal',
    'adjustment'
);

CREATE TYPE outbox_status AS ENUM (
    'pending',
    'published',
    'failed'
);

-- 2. ACCOUNTS

CREATE TABLE accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    account_number VARCHAR(50) UNIQUE NOT NULL,
    name VARCHAR(150) NOT NULL,

    type account_type NOT NULL,
    normal_side entry_side NOT NULL,

    currency CHAR(3) NOT NULL DEFAULT 'INR',

    current_balance_minor BIGINT NOT NULL DEFAULT 0,
    allow_negative BOOLEAN NOT NULL DEFAULT FALSE,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT valid_currency
        CHECK (currency ~ '^[A-Z]{3}$'),

    CONSTRAINT account_normal_side_check
        CHECK (
            (type IN ('asset', 'expense') AND normal_side = 'debit')
            OR
            (type IN ('liability', 'equity', 'revenue') AND normal_side = 'credit')
        )
);

-- 3. PAYMENTS

CREATE TABLE payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    customer_account_id UUID NOT NULL
        REFERENCES accounts(id),

    merchant_account_id UUID NOT NULL
        REFERENCES accounts(id),

    amount_minor BIGINT NOT NULL
        CHECK (amount_minor > 0),

    currency CHAR(3) NOT NULL DEFAULT 'INR',

    status payment_status NOT NULL DEFAULT 'pending',

    provider VARCHAR(100),
    payment_method VARCHAR(30),
    bank_code VARCHAR(30),
    provider_payment_id VARCHAR(255),

    request_hash VARCHAR(64) NOT NULL,
    response JSONB,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT different_payment_accounts
        CHECK (customer_account_id <> merchant_account_id)
);

CREATE INDEX idx_payments_status
ON payments(status);

CREATE INDEX idx_payments_created_at
ON payments(created_at);

CREATE UNIQUE INDEX uq_provider_payment
ON payments(provider, provider_payment_id)
WHERE provider_payment_id IS NOT NULL;

-- 4. IDEMPOTENCY KEYS

CREATE TABLE idempotency_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    scope_id UUID NOT NULL
        CONSTRAINT fk_idempotency_scope REFERENCES accounts(id),
    idempotency_key VARCHAR(255) NOT NULL,

    request_hash VARCHAR(64) NOT NULL,

    payment_id UUID REFERENCES payments(id),

    response_status INTEGER,
    response_body JSONB,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,

    CONSTRAINT unique_scoped_idempotency_key
        UNIQUE(scope_id, idempotency_key)
);

-- 5. LEDGER TRANSACTIONS
-- A payment can have several ledger transactions (the payment itself,
-- refunds, reversals), so payment_id is NOT unique.
-- reverses_transaction_id is UNIQUE: a transaction can be reversed only once.

CREATE TABLE ledger_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    payment_id UUID REFERENCES payments(id),

    reverses_transaction_id UUID UNIQUE
        REFERENCES ledger_transactions(id),

    type ledger_transaction_type NOT NULL,

    reference_id UUID NOT NULL,

    currency CHAR(3) NOT NULL DEFAULT 'INR',

    description TEXT,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT unique_ledger_reference
        UNIQUE(type, reference_id)
);

CREATE INDEX idx_ledger_tx_payment
ON ledger_transactions(payment_id);

-- 6. LEDGER ENTRIES

CREATE TABLE ledger_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    transaction_id UUID NOT NULL
        REFERENCES ledger_transactions(id),

    account_id UUID NOT NULL
        REFERENCES accounts(id),

    side entry_side NOT NULL,

    amount_minor BIGINT NOT NULL
        CHECK (amount_minor > 0),

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_entries_transaction
ON ledger_entries(transaction_id);

CREATE INDEX idx_entries_account
ON ledger_entries(account_id);

CREATE INDEX idx_entries_account_created
ON ledger_entries(account_id, created_at);

-- 7. TRANSACTIONAL OUTBOX

CREATE TABLE outbox_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    aggregate_type VARCHAR(100) NOT NULL,
    aggregate_id UUID NOT NULL,

    event_type VARCHAR(150) NOT NULL,
    payload JSONB NOT NULL,

    status outbox_status NOT NULL DEFAULT 'pending',

    attempts INTEGER NOT NULL DEFAULT 0
        CHECK (attempts >= 0),

    available_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    published_at TIMESTAMPTZ,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_outbox_pending
ON outbox_events(available_at, created_at)
WHERE status = 'pending';

-- =====================================================
-- LEDGER BALANCE ENFORCEMENT
-- =====================================================

-- 8. UPDATE ACCOUNT BALANCE ON ENTRY INSERTION
-- Positive balance means balance on the account's
-- normal side.

CREATE OR REPLACE FUNCTION update_account_balance()
RETURNS TRIGGER AS $$
DECLARE
    v_normal_side entry_side;
    v_currency CHAR(3);
    v_new_balance BIGINT;
    v_allow_negative BOOLEAN;
    v_transaction_currency CHAR(3);
BEGIN

    SELECT normal_side, currency,
           current_balance_minor, allow_negative
    INTO v_normal_side, v_currency,
         v_new_balance, v_allow_negative
    FROM accounts
    WHERE id = NEW.account_id
    FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Account does not exist';
    END IF;

    SELECT currency
    INTO v_transaction_currency
    FROM ledger_transactions
    WHERE id = NEW.transaction_id;

    IF v_currency <> v_transaction_currency THEN
        RAISE EXCEPTION 'Currency mismatch';
    END IF;

    IF NEW.side = v_normal_side THEN
        v_new_balance := v_new_balance + NEW.amount_minor;
    ELSE
        v_new_balance := v_new_balance - NEW.amount_minor;
    END IF;

    IF NOT v_allow_negative AND v_new_balance < 0 THEN
        RAISE EXCEPTION 'Insufficient account balance';
    END IF;

    UPDATE accounts
    SET current_balance_minor = v_new_balance,
        updated_at = NOW()
    WHERE id = NEW.account_id;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_update_account_balance
BEFORE INSERT ON ledger_entries
FOR EACH ROW
EXECUTE FUNCTION update_account_balance();

-- 9. PREVENT LEDGER ENTRY MODIFICATION

CREATE OR REPLACE FUNCTION prevent_ledger_entry_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'Ledger entries are immutable. Use a reversal or adjustment.';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_immutable_ledger_entries
BEFORE UPDATE OR DELETE ON ledger_entries
FOR EACH ROW
EXECUTE FUNCTION prevent_ledger_entry_mutation();

-- 10. VERIFY DOUBLE-ENTRY BALANCE

CREATE OR REPLACE FUNCTION assert_ledger_transaction_balanced(
    p_transaction_id UUID
)
RETURNS VOID AS $$
DECLARE
    v_entry_count BIGINT;
    v_debits BIGINT;
    v_credits BIGINT;
BEGIN

    SELECT
        COUNT(*),
        COALESCE(
            SUM(amount_minor) FILTER (WHERE side = 'debit'), 0
        ),
        COALESCE(
            SUM(amount_minor) FILTER (WHERE side = 'credit'), 0
        )
    INTO v_entry_count, v_debits, v_credits
    FROM ledger_entries
    WHERE transaction_id = p_transaction_id;

    IF v_entry_count < 2 THEN
        RAISE EXCEPTION
            'Ledger transaction must have at least two entries';
    END IF;

    IF v_debits <> v_credits THEN
        RAISE EXCEPTION
            'Unbalanced ledger: debits %, credits %',
            v_debits, v_credits;
    END IF;
END;
$$ LANGUAGE plpgsql;

-- 11. DEFERRED CHECK FOR LEDGER TRANSACTIONS

CREATE OR REPLACE FUNCTION check_new_ledger_transaction()
RETURNS TRIGGER AS $$
BEGIN
    PERFORM assert_ledger_transaction_balanced(NEW.id);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER trg_check_ledger_transaction
AFTER INSERT ON ledger_transactions
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION check_new_ledger_transaction();

-- Also check after entries are inserted.

CREATE OR REPLACE FUNCTION check_new_ledger_entry()
RETURNS TRIGGER AS $$
BEGIN
    PERFORM assert_ledger_transaction_balanced(
        NEW.transaction_id
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER trg_check_ledger_entry
AFTER INSERT ON ledger_entries
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW
EXECUTE FUNCTION check_new_ledger_entry();

-- 12. PREVENT LEDGER TRANSACTION MUTATION

CREATE OR REPLACE FUNCTION prevent_ledger_transaction_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION
        'Posted ledger transactions are immutable';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_immutable_ledger_transactions
BEFORE UPDATE OR DELETE ON ledger_transactions
FOR EACH ROW
EXECUTE FUNCTION prevent_ledger_transaction_mutation();