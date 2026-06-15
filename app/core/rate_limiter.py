"""Rate limiting configuration for API endpoints."""

from slowapi import Limiter
from slowapi.util import get_remote_address

# Baseline per-client limit applied to EVERY route via SlowAPIMiddleware (H4).
# Routes that need to be stricter (e.g. login) add their own @limiter.limit,
# which is enforced on top of this default.
#
# Client identity comes from get_remote_address (request.client.host). The app
# runs uvicorn with --proxy-headers/--forwarded-allow-ips, so client.host is the
# real caller IP behind the proxy rather than the proxy's address.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["300/minute"],
)
