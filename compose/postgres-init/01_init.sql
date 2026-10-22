-- separate database for n8n's own data
CREATE DATABASE n8n;

-- project database (ar_demo): vectors + AR schema
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS ar;

CREATE TABLE ar.inbound_email (
  id                bigserial PRIMARY KEY,
  message_id        text UNIQUE NOT NULL,
  received_at       timestamptz,
  sender            text,
  subject           text,
  body_hash         text,
  attachment_hashes text[],
  status            text NOT NULL DEFAULT 'NEW',
  created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ar.ar_case (
  id             bigserial PRIMARY KEY,
  email_id       bigint REFERENCES ar.inbound_email(id),
  intents        text[],
  company_code   text,
  customer       text,
  extraction     jsonb,
  recommendation jsonb,
  confidence     numeric(4,3),
  status         text NOT NULL DEFAULT 'OPEN',
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ar.case_event (
  id          bigserial PRIMARY KEY,
  case_id     bigint REFERENCES ar.ar_case(id),
  event_type  text NOT NULL,
  actor       text NOT NULL,
  payload     jsonb,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- audit log is append-only
CREATE FUNCTION ar.block_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'case_event is append-only'; END $$;
CREATE TRIGGER case_event_no_change BEFORE UPDATE OR DELETE ON ar.case_event
  FOR EACH ROW EXECUTE FUNCTION ar.block_change();

CREATE TABLE ar.customer_domain_map (
  email_domain  text PRIMARY KEY,
  company_code  text NOT NULL,
  customer      text NOT NULL,
  note          text
);
