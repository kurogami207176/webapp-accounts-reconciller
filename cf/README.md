# CloudFormation Templates

Infrastructure-as-Code for the webapp-accounts-reconciller ECS Express deployment.

## Architecture

```
Internet
   │
  ALB  ◄── auto-managed by ECS Express Mode
   │
  ECS Fargate Tasks (auto-managed VPC + security groups)
   │
  ECR (Docker image registry)
```

ECS Express Mode (`AWS::ECS::ExpressGatewayService`) automatically provisions
the ECS cluster, task definition, service, ALB, target groups, security groups,
auto-scaling policies, and CloudWatch log groups. No separate network stack needed.

## Stacks

| File | Stack name | Purpose |
|------|-----------|---------|
| `ecr.yml` | `<app>-ecr` | ECR repository |
| `ecs.yml` | `<app>-ecs-<env>` | ECS Express service (auto-provisions ALB + networking) |
| `dns.yml` | `<app>-dns-<env>` | Route53 alias → ALB (optional, for custom domain) |
| `iam-github-oidc.yml` | `<app>-github-oidc` | GitHub Actions deploy role (one-time) |

`network.yml` is retained for reference but is no longer deployed — ECS Express
Mode manages its own VPC resources.

## First-time setup

```bash
# Deploys ECR → ECS Express (→ optional DNS) in one shot
./bin/deploy-infra.sh --env production --region ap-southeast-2

# Skip the DNS/Route53 stack if you don't have a custom domain yet
./bin/deploy-infra.sh --env production --skip-dns
```

## Subsequent deploys

```bash
# From local machine — update the image tag in the CF stack
./bin/deploy-app.sh --env production --tag v1.2.3

# Or push to main → GitHub Actions handles it automatically
```

## Stack outputs

Each stack exports its key resources so dependent stacks can `Fn::ImportValue`
them without hardcoding ARNs or account IDs.
