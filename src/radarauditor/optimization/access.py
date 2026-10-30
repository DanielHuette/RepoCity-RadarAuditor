"""Decide which customer systems the optimisation may use: WordPress and the Business Profile.

Context: Shared by the optimize step (which tells Claude Code what is reachable) and the apply step
(which refuses targets without access). The WordPress password is read from the database only here
and goes no further than the WordPress client.
Input: CustomerAccess, Repositories.
Output: WordPress client or None; Business Profile location name or the reason it is unusable.
"""

import httpx

from radarauditor.collectors.google_auth import load_grant
from radarauditor.db.models import CustomerAccess
from radarauditor.db.repositories import Repositories
from radarauditor.errors import AuthorizationError
from radarauditor.integrations.wordpress import WordPressClient

NO_PROFILE_ACCESS = "Kein Zugriff auf das Unternehmensprofil"
NO_WORDPRESS_ACCESS = "Kein Zugang zu WordPress hinterlegt"


def wordpress_client(
    access: CustomerAccess | None,
    repositories: Repositories,
    transport: httpx.AsyncBaseTransport | None = None,
) -> WordPressClient | None:
    """A client for the customer's WordPress when URL, user and application password are stored.

    Args:
        access: What the customer granted, or None.
        repositories: Reads the stored application password.
        transport: Replaces the network in tests.

    Returns:
        The client, or None when any of the three is missing.

    Raises:
        StorageError: If the password cannot be read.
    """
    if access is None or not access.wordpress_url or not access.wordpress_user:
        return None
    password = repositories.wordpress_password(access.customer_id)
    if password is None:
        return None
    return WordPressClient(access.wordpress_url, access.wordpress_user, password, transport)


def profile_location(access: CustomerAccess | None, repositories: Repositories) -> str | None:
    """The Business Profile location the operator may change, or None.

    It needs the customer's grant, the location name and the operator's stored Google grant.

    Raises:
        StorageError: If the stored grant cannot be read.
    """
    if access is None or not access.google_business_profile_granted:
        return None
    if not access.google_location_name:
        return None
    try:
        grant = load_grant(repositories)
    except AuthorizationError:
        return None
    return access.google_location_name if grant is not None else None
