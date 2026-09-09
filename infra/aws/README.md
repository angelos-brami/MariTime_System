# AWS production deployment

This Terraform configuration is the selected production substrate for East Med. It creates:

- a two-AZ VPC with public load-balancer subnets and private application/data subnets;
- PostgreSQL 16 RDS Multi-AZ, encrypted storage, forced TLS, 35-day point-in-time backups,
  Performance Insights, deletion protection and PostgreSQL logs;
- a two-node encrypted Redis 7.1 replication group with TLS, authentication and snapshots;
- encrypted EFS source snapshots with daily 35-day and weekly 365-day AWS Backup recovery points,
  plus a private versioned S3 evidence/backup bucket;
- ECS Fargate API, web, ingestion, analysis, publication, delivery and scheduler services;
- dedicated two-AZ ingestion subnets routed symmetrically through AWS Network Firewall, with an
  HTTP-host/TLS-SNI allowlist generated from the approved source catalogue and AWS regional APIs;
- a distinct PostgreSQL login and least-privilege group for every worker class;
- raw source captures mounted and network-accessible only to the ingestion task; other application
  tasks receive no S3/KMS data-plane permissions;
- immutable, scan-on-push ECR repositories, Secrets Manager injection, TLS 1.2/1.3 ALB,
  Route53 records, VPC flow logs, Container Insights, alarms and an operations dashboard.

`activate_services` defaults to false. Terraform can provision the substrate, but it does not
start application services until the migration and role-bootstrap tasks succeed.

## Owner-controlled prerequisites

1. An AWS account with billing enabled and an IAM deployment role.
2. A Route53 public hosted zone and the final application/API hostnames.
3. A Clerk production instance and production publishable/secret keys.
4. An Anthropic API key approved for source-document processing.
5. Two real operator email addresses. Each person must accept their own Clerk invitation and
   enrol their own passkey/MFA; an administrator cannot perform that ceremony for them.
6. Docker, AWS CLI and Terraform on the deployment workstation.

## State and deployment

1. Run `scripts/bootstrap_aws_state.ps1`. Save its four output lines as `infra/aws/backend.hcl`.
2. Copy `terraform.tfvars.example` to `terraform.tfvars` and replace every value. Generate
   `allowed_fetch_hosts` with `python scripts/render_source_allowlist.py`.
3. Copy `runtime-providers.example.json` outside the repository, populate Clerk and Anthropic plus
   every provider enabled in the variables file, and restrict the file to the deployment operator.
   The deployment rejects required placeholders without printing credential values.
4. Use an immutable source-control commit SHA as the image tag.
5. Run `scripts/deploy_aws.ps1`. It provisions with services stopped, uploads provider secrets,
   builds and pushes both images, applies migrations, provisions isolated database logins, starts
   the services, and checks the public health endpoints.
6. Confirm the SNS email subscription, both Clerk MFA sessions, the restore drill and the 60-source
   rights decisions before any release owner considers outbound delivery.

The application-level source allowlist rejects unregistered hosts, redirects, credentials,
non-standard ports and private/reserved DNS results. A second network layer sends only the ingestion
worker through AWS Network Firewall and allows catalogue hostnames plus regional AWS control-plane
endpoints by HTTP Host/TLS SNI. Security groups expose only the ALB and keep PostgreSQL, Redis and
EFS private. Network Firewall incurs material hourly and traffic-processing charges; review the plan
and AWS pricing before apply. A catalogue hostname must pass both layers.

Outbound delivery remains hard-disabled in this configuration. Provider acceptance is a separate
release ceremony and must never be combined with initial infrastructure deployment.
