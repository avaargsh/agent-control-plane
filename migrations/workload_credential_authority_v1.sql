-- Operator-installed schema for workload credential authority v1.
-- Apply separately via the reviewed deployment/migration procedure. The HTTP
-- service never creates or upgrades these tables at request time.
BEGIN;

CREATE TABLE IF NOT EXISTS public.acp_workload_credential_bindings (
    binding_id TEXT PRIMARY KEY CHECK (length(btrim(binding_id)) > 0),
    capability TEXT NOT NULL CHECK (length(btrim(capability)) > 0),
    audience TEXT NOT NULL CHECK (length(btrim(audience)) > 0),
    allowed_scopes JSONB NOT NULL
        CHECK (jsonb_typeof(allowed_scopes) = 'array' AND jsonb_array_length(allowed_scopes) > 0),
    max_ttl_seconds INTEGER NOT NULL CHECK (max_ttl_seconds BETWEEN 1 AND 3600),
    active BOOLEAN NOT NULL DEFAULT TRUE,
    version BIGINT NOT NULL CHECK (version > 0),
    updated_at DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM clock_timestamp())
);

CREATE TABLE IF NOT EXISTS public.acp_workload_approval_grants (
    approval_id TEXT PRIMARY KEY CHECK (length(btrim(approval_id)) > 0),
    tenant_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    binding_id TEXT NOT NULL REFERENCES public.acp_workload_credential_bindings(binding_id),
    audience TEXT NOT NULL,
    scopes JSONB NOT NULL
        CHECK (jsonb_typeof(scopes) = 'array' AND jsonb_array_length(scopes) > 0),
    approved_by TEXT NOT NULL CHECK (length(btrim(approved_by)) > 0),
    issued_at DOUBLE PRECISION NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL CHECK (expires_at > issued_at),
    state TEXT NOT NULL CHECK (state IN ('PENDING', 'ACTIVE', 'DENIED', 'REVOKED')),
    version BIGINT NOT NULL CHECK (version > 0),
    revoke_event_id TEXT,
    updated_at DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM clock_timestamp())
);

CREATE INDEX IF NOT EXISTS acp_workload_approval_scope_idx
    ON public.acp_workload_approval_grants
    (tenant_id, agent_id, session_id, run_id, state, expires_at);

CREATE TABLE IF NOT EXISTS public.acp_workload_credentials (
    credential_id TEXT PRIMARY KEY CHECK (length(credential_id) = 32),
    token_digest TEXT NOT NULL UNIQUE CHECK (token_digest ~ '^[0-9a-f]{64}$'),
    workload_issuer TEXT NOT NULL,
    subject TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    release_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    sandbox_id TEXT NOT NULL,
    sandbox_revision BIGINT NOT NULL CHECK (sandbox_revision > 0),
    workspace_ref TEXT NOT NULL,
    binding_id TEXT NOT NULL REFERENCES public.acp_workload_credential_bindings(binding_id),
    capability TEXT NOT NULL,
    audience TEXT NOT NULL,
    allowed_scopes JSONB NOT NULL
        CHECK (jsonb_typeof(allowed_scopes) = 'array' AND jsonb_array_length(allowed_scopes) > 0),
    scopes JSONB NOT NULL
        CHECK (jsonb_typeof(scopes) = 'array' AND jsonb_array_length(scopes) > 0),
    issued_at DOUBLE PRECISION NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL CHECK (expires_at > issued_at),
    approval_id TEXT REFERENCES public.acp_workload_approval_grants(approval_id),
    approval_version BIGINT,
    binding_version BIGINT NOT NULL CHECK (binding_version > 0),
    revoked_at DOUBLE PRECISION,
    revoke_event_id TEXT,
    created_at DOUBLE PRECISION NOT NULL DEFAULT EXTRACT(EPOCH FROM clock_timestamp())
);

CREATE INDEX IF NOT EXISTS acp_workload_credentials_run_idx
    ON public.acp_workload_credentials(run_id, sandbox_revision);
CREATE INDEX IF NOT EXISTS acp_workload_credentials_approval_idx
    ON public.acp_workload_credentials(approval_id)
    WHERE approval_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.acp_workload_credential_requests (
    issuer TEXT NOT NULL,
    run_id TEXT NOT NULL,
    binding_id TEXT NOT NULL REFERENCES public.acp_workload_credential_bindings(binding_id),
    request_id TEXT NOT NULL CHECK (length(btrim(request_id)) BETWEEN 1 AND 200),
    request_digest TEXT NOT NULL CHECK (request_digest ~ '^[0-9a-f]{64}$'),
    credential_id TEXT NOT NULL UNIQUE REFERENCES public.acp_workload_credentials(credential_id),
    created_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (issuer, run_id, binding_id, request_id)
);

CREATE TABLE IF NOT EXISTS public.acp_workload_approval_revocation_events (
    event_id TEXT PRIMARY KEY CHECK (length(btrim(event_id)) > 0),
    approval_id TEXT NOT NULL REFERENCES public.acp_workload_approval_grants(approval_id),
    approval_version BIGINT NOT NULL CHECK (approval_version > 0),
    revoked_count INTEGER NOT NULL CHECK (revoked_count >= 0),
    created_at DOUBLE PRECISION NOT NULL,
    UNIQUE (event_id, approval_id)
);

CREATE TABLE IF NOT EXISTS public.acp_workload_credential_revocation_outbox (
    event_id TEXT PRIMARY KEY REFERENCES public.acp_workload_approval_revocation_events(event_id),
    approval_id TEXT NOT NULL REFERENCES public.acp_workload_approval_grants(approval_id),
    approval_version BIGINT NOT NULL CHECK (approval_version > 0),
    payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    created_at DOUBLE PRECISION NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at DOUBLE PRECISION NOT NULL,
    claim_id TEXT,
    lease_until DOUBLE PRECISION,
    delivered_at DOUBLE PRECISION,
    last_error TEXT CHECK (last_error IS NULL OR length(last_error) <= 256),
    CHECK ((claim_id IS NULL) = (lease_until IS NULL))
);

CREATE INDEX IF NOT EXISTS acp_workload_credential_revocation_pending_idx
    ON public.acp_workload_credential_revocation_outbox(available_at, created_at)
    WHERE delivered_at IS NULL;

COMMIT;
