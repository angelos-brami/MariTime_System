# Production desk identity: Clerk

## Decision

Use one Clerk production instance for the subscriber portal and the internal desk. This matches
the existing Next.js integration and avoids a second identity bridge. Clerk session tokens are
signed JWTs, expose the signed `fva` factor-verification-age claim, and can be verified by the API
through the instance JWKS endpoint.

The local shared Basic password is a demo-only control. In `production` with
`EASTMED_DESK_AUTH_MODE=oidc`, `/console` and `/api/console` require an authenticated Clerk
session; the Basic credential path is not used.

## Clerk production-instance settings

1. Disable public sign-up and permit invitations only.
2. Enable passkey sign-in.
3. Require multi-factor authentication for every user. Enrol two distinct operators with a
   platform passkey or separate FIDO2 security key and retain recovery codes offline.
4. Disable SMS as an operator factor. Keep TOTP only as a documented recovery path if business
   continuity requires it.
5. Set an inactivity timeout of 30 minutes and a maximum session lifetime of 8 hours.
6. Configure the production custom domain before passkey enrolment; WebAuthn credentials are
   scoped to the relying-party domain.
7. In **Sessions > Customize session token**, add the contents of
   `config/clerk-session-claims.json`, replacing the API domain. Do not use a custom JWT template:
   EastMed uses the session-bound token because it contains `fva`.

## EastMed environment mapping

```dotenv
EASTMED_DESK_AUTH_MODE=oidc
EASTMED_DESK_OIDC_ISSUER=https://REPLACE_WITH_CLERK_FRONTEND_API
EASTMED_DESK_OIDC_AUDIENCE=https://api.REPLACE_WITH_PRODUCTION_DOMAIN
EASTMED_DESK_OIDC_JWKS_URL=https://REPLACE_WITH_CLERK_FRONTEND_API/.well-known/jwks.json
EASTMED_DESK_OIDC_AUTHORIZED_PARTIES=https://REPLACE_WITH_PRODUCTION_DOMAIN
EASTMED_DESK_OIDC_ALGORITHMS=RS256
EASTMED_DESK_OIDC_TOKEN_TYPES=JWT
EASTMED_DESK_OIDC_MFA_MAX_AGE_MINUTES=10
EASTMED_DESK_OIDC_TOKEN_TEMPLATE=
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=REPLACE_IN_SECRET_MANAGER
CLERK_SECRET_KEY=REPLACE_IN_SECRET_MANAGER
```

The API treats a signed Clerk token as MFA only while the second-factor entry in `fva` is present,
non-negative, and no older than ten minutes. An explicitly configured `acr` value can represent a
phishing-resistant session; EastMed never infers that stronger class from `fva` alone.

## Two-person desk registration

After each invited operator accepts the invitation and completes MFA, record the Clerk `user_...`
subject in EastMed. Use separate people; never create two identities controlled by one person.

```bash
eastmed-bootstrap-desk-user \
  --issuer "https://REPLACE_WITH_CLERK_FRONTEND_API" \
  --subject "user_REPLACE_OPERATOR_ONE" \
  --email "operator-one@REPLACE_DOMAIN" \
  --display-name "Operator One" \
  --role senior_analyst

eastmed-bootstrap-desk-user \
  --issuer "https://REPLACE_WITH_CLERK_FRONTEND_API" \
  --subject "user_REPLACE_OPERATOR_TWO" \
  --email "operator-two@REPLACE_DOMAIN" \
  --display-name "Operator Two" \
  --role administrator
```

Acceptance is complete only when both users can sign in independently, the dashboard reports
OIDC identity active, and a severity-4 test publication is requested by one person and approved by
the other after fresh MFA.
