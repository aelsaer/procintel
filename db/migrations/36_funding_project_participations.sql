-- Provider-confirmed company participation in funded projects.
--
-- ΑΝΑΠΤΥΞΗ can confirm that an exact ΑΦΜ participates in a project without
-- identifying which one of several free-text contractor body names belongs
-- to that ΑΦΜ. Keep that exact project-level fact separately instead of
-- creating a false name-to-entity match.
CREATE TABLE IF NOT EXISTS funding_project_participations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    funding_project_id  UUID NOT NULL REFERENCES funding_projects(id) ON DELETE CASCADE,
    entity_id           UUID NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    role                TEXT NOT NULL,
    link_method         TEXT NOT NULL,
    confidence          NUMERIC(5,4) NOT NULL,
    evidence            JSONB NOT NULL DEFAULT '{}'::jsonb,
    review_status       TEXT NOT NULL DEFAULT 'AUTO_ACCEPTED',
    source_record_id    UUID REFERENCES source_records(id),
    observed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (funding_project_id, entity_id, role, link_method)
);

CREATE INDEX IF NOT EXISTS ix_funding_project_participations_entity
    ON funding_project_participations (entity_id, role, observed_at DESC);

CREATE INDEX IF NOT EXISTS ix_funding_project_participations_project
    ON funding_project_participations (funding_project_id, role);
