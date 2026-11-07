"""JWKS (JSON Web Key Set) endpoint."""

from typing import Any, Dict
from fastapi import APIRouter
from app.core.security import get_jwks

router = APIRouter(tags=["JWKS"])


@router.get("/.well-known/jwks.json", response_model=Dict[str, Any])
async def jwks() -> Dict[str, Any]:
    """
    Public JWKS endpoint for asymmetric signature verification.
    External and peer microservices fetch public keys from here to verify RS256 JWTs locally.
    """
    return get_jwks()
