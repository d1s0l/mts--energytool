"""Anonymous session capabilities and a separate reviewer credential."""

import hashlib
import secrets

from fastapi import HTTPException, Request


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def require_access(request: Request):
    admin = request.app.state.settings.api_access_token
    supplied = request.headers.get("authorization", "")
    if admin and secrets.compare_digest(supplied, "Bearer " + admin):
        return
    session_id = request.path_params.get("session_id")
    if session_id is None:
        if request.method == "POST":
            return  # Creating a fresh anonymous session requires no previous identity.
        raise HTTPException(403, "access_denied")
    token = request.headers.get("x-session-token", "")
    if not token or len(token) > 256:
        raise HTTPException(403, "access_denied")
    async with request.app.state.db_pool.connection() as conn:
        cursor = await conn.execute("SELECT access_token_hash FROM sessions WHERE id::text = %s",
                                    (str(session_id),))
        row = await cursor.fetchone()
    if not row or not row["access_token_hash"] or not secrets.compare_digest(
        row["access_token_hash"], token_hash(token)
    ):
        raise HTTPException(403, "access_denied")
