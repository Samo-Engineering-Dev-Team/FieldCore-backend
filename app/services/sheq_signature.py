"""
Signature record construction for SHEQ submissions (SHEQ-CHECKLISTS-PLAN.md
§7.3). Pure and dependency-free so `data_hash` stability is unit-testable
without a DB — the hash is the audit teeth: it proves what `data` looked like
at the moment a signature was captured.
"""

import hashlib
import json
from datetime import datetime
from typing import Any


def compute_data_hash(data: dict[str, Any]) -> str:
    """Canonical (sorted-key, separator-normalised) sha256 of `data`.

    Stable under key reordering — {"a": 1, "b": 2} and {"b": 2, "a": 1} hash
    identically — and changes whenever any value in `data` changes.
    """
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def build_signature_record(
    *,
    role: str,
    method: str,
    captured_at: datetime,
    signed_at: datetime,
    data_hash: str,
    roster_index: int | None = None,
    file_ref: dict[str, Any] | None = None,
    typed_name: str | None = None,
    signer_user_id: str | None = None,
    signer_name: str,
    offline_captured: bool = False,
    device: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Build one §7.3 signature record. `signed_at`/`ip_address`/`data_hash`
    are always server-set by the caller — never trust these from the client."""
    return {
        "role": role,
        "roster_index": roster_index,
        "method": method,
        "file_ref": file_ref,
        "typed_name": typed_name,
        "signer_user_id": signer_user_id,
        "signer_name": signer_name,
        "signed_at": signed_at.isoformat(),
        "captured_at": captured_at.isoformat(),
        "offline_captured": offline_captured,
        "device": device,
        "ip_address": ip_address,
        "user_agent": user_agent,
        "data_hash": data_hash,
    }
