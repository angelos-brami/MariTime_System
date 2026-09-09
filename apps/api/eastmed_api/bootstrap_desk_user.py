from __future__ import annotations

import argparse

from eastmed_schema.enums import DeskRole

from eastmed_api.ai_governance import AIGovernanceError, bootstrap_first_desk_user
from eastmed_api.database import SessionLocal


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create the first East Med desk identity without storing a password"
    )
    parser.add_argument("--issuer", required=True)
    parser.add_argument("--subject", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument(
        "--role",
        choices=[role.value for role in DeskRole if role != DeskRole.SERVICE],
        default=DeskRole.ADMINISTRATOR.value,
    )
    args = parser.parse_args()
    try:
        with SessionLocal() as session:
            user = bootstrap_first_desk_user(
                session,
                auth_issuer=args.issuer.strip(),
                auth_subject=args.subject.strip(),
                email=args.email.strip().casefold(),
                display_name=args.display_name.strip(),
                role=DeskRole(args.role),
            )
    except AIGovernanceError as exc:
        parser.error(str(exc))
    print(f"Bootstrapped desk user {user.id} ({user.role.value})")
