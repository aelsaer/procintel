# AWS production runtime

This stack provisions the complete production runtime: a two-AZ VPC, private
subnets, encrypted PostgreSQL 16 RDS, private encrypted OpenSearch, versioned
S3 storage, ECR, ECS services for the API, web application, durable worker and
ClamAV, EventBridge ingestion/monitoring schedules, ALB/TLS, WAF, autoscaling,
CloudWatch alarms and Secrets Manager entries.

Application releases use the images built by `infra/docker/Dockerfile.*`.
The same environment contract is documented in
`infra/docker/.env.production.example`.

```bash
terraform init
terraform plan -var='project=procintel' -var='alert_email=ops@example.gr'
terraform apply
```

The RDS master password is generated and managed by RDS in Secrets Manager.
Application secrets are intentionally created as empty containers. Before a
service deployment, populate every referenced JSON key:

- `app-database`: `DATABASE_URL`, `OWNER_DATABASE_URL`, `APP_DB_PASSWORD`.
- `oidc`: `ISSUER_URL`, `INTERNAL_ISSUER_URL`, `AUDIENCE`, `CLIENT_ID`,
  `CLIENT_SECRET`, `SESSION_SECRET`.
- `gemi`: `API_KEY`.
- `llm`: `API_KEY`, `ENDPOINT`, `MODEL`.
- `smtp`: `HOST`, `PORT`, `USERNAME`, `PASSWORD`, `FROM_ADDRESS`, `USE_TLS`.
- `webhooks`: bid-reminder URLs and any enabled CRM URLs.
- `commercial`: Stripe secret, webhook secret and public-plan price IDs.

Use JSON empty strings for optional integration values so ECS can resolve every
declared key. Values are populated out-of-band and never enter Terraform state.

For each immutable image tag: push all three images, run the migration task,
verify its successful exit, update the API/worker services, then update web.
The ECS deployment circuit breakers roll back unhealthy releases. Confirm the
ALB health endpoint and CloudWatch source-freshness metrics before promotion.

Configure AWS Backup or export RDS snapshots to a separate account for disaster
recovery. RDS retains 35 days of automated backups and protects the final
snapshot. Keep Terraform state in a versioned, locked remote backend for team
deployments; backend configuration is intentionally environment-owned.
