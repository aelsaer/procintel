# AWS production foundation

This stack provisions the stateful and security-sensitive production
foundation: a two-AZ VPC, private subnets, encrypted PostgreSQL 16 RDS,
private encrypted OpenSearch, versioned S3 document/raw storage, ECR
repositories, an ECS cluster, CloudWatch logs and Secrets Manager entries.

Application releases use the images built by `infra/docker/Dockerfile.*`.
The same environment contract is documented in
`infra/docker/.env.production.example`.

```bash
terraform init
terraform plan -var='project=procintel' -var='alert_email=ops@example.gr'
terraform apply
```

The RDS master password is generated and managed by RDS in Secrets Manager.
Provider/OIDC values are intentionally created as empty secret containers;
populate them out-of-band so credentials never enter Terraform state.

Run `db/run_migrations_tracked.sh` as a one-off ECS task before promoting new
API/scheduler images. Configure AWS Backup or export RDS snapshots to a
separate account for disaster recovery; the database defaults below retain
35 days of automated backups and protect the final snapshot.
