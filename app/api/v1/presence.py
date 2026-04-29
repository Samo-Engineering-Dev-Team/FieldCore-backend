from fastapi import APIRouter, HTTPException
from app.services.auth import CurrentUser
from app.services.presence import PresenceService

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("/heartbeat")
def heartbeat(current_user: CurrentUser):
    """Record a heartbeat for the authenticated user (creates session if none)."""
    try:
        result = PresenceService.heartbeat(current_user.user_id, str(current_user.role))
        return {"ok": True, "session": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/logout")
def logout(current_user: CurrentUser):
    """Deactivate active sessions for the current user."""
    try:
        PresenceService.deactivate_session(user_id=current_user.user_id)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))