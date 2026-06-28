r"""Offline licence and entitlement seam for SA-Live-Transcribe.

The whole app is local and privacy-first, so licensing must be too: no account,
no phone-home, no telemetry. A licence is a short signed token the user pastes in
once; the app verifies it locally against an embedded public key and unlocks what
it grants. Only the holder of the matching private key (you) can issue a valid
token, so it cannot be forged.

Forward-compatible on purpose. A licence carries a tier, an explicit feature list
(for add-ons), the highest major version it covers, and an optional expiry. That
single format expresses every model we are still choosing between:
  perpetual per major version  : valid_until = null, max_major = N
  subscription (monthly/annual): valid_until = <date>, max_major = null
so the commercial model can change later without touching this code or the UI.

Token format: base64url(payload_json) + "." + base64url(ed25519_signature).
Verification needs only the public key, which is safe to ship in the binary.

Issuing licences (you, offline; the private key never leaves your machine):
    python -m live_transcribe.licensing keygen
    python -m live_transcribe.licensing sign <priv_hex> --tier pro --max-major 1
Inspecting a token:
    python -m live_transcribe.licensing verify <token>
"""
import argparse
import base64
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# Replace with YOUR public key: run `keygen`, keep the private key secret, paste
# the public hex here. While this is empty (dev/test) the SA_LIVE_LICENSE_PUBKEY
# env var supplies the key; once a real key is baked in here it takes precedence
# and the env var is ignored, so a shipped build can't be pointed at another key.
_PUBLIC_KEY_HEX = ""

# The package major version a licence is checked against. Bump on a paid major
# release so older perpetual licences resolve to "upgrade available", not "valid".
APP_VERSION = "1.6.4"
APP_MAJOR = 1

_LICENSE_PATH = (
    Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    / "sa-live-transcribe" / "license.key"
)

# Pro covers only what genuinely needs an online connection. Calendar attendee
# seeding talks to Microsoft Graph, so it is Pro. cloud_api and multi_seat are
# add-ons, granted only when explicitly listed in the licence, never by the tier
# alone. Everything that runs on this machine (live transcription, local
# summaries, the clean pass, history, exports, saved vocab) stays free, by
# design: local features are never gated.
PRO_FEATURES = frozenset({"calendar"})
ADDON_FEATURES = frozenset({"cloud_api", "multi_seat"})
ALL_FEATURES = PRO_FEATURES | ADDON_FEATURES


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _pubkey_hex() -> str:
    # A baked-in key always wins; the env override only applies when no key is
    # shipped (dev/test), so a released build can't be pointed at another key to
    # self-sign Pro.
    return _PUBLIC_KEY_HEX or os.environ.get("SA_LIVE_LICENSE_PUBKEY", "").strip()


@dataclass(frozen=True)
class Entitlements:
    tier: str
    features: frozenset
    max_major: Optional[int]
    valid_until: Optional[str]
    seats: int

    def has(self, feature: str) -> bool:
        return feature in self.features


FREE = Entitlements(tier="free", features=frozenset(), max_major=None, valid_until=None, seats=1)


def _resolve(payload: dict) -> Entitlements:
    """Map a verified payload to the entitlements it grants (tier set + add-ons)."""
    tier = (payload.get("tier") or "free").lower()
    granted = set(PRO_FEATURES) if tier == "pro" else set()
    for f in payload.get("features", []):
        if f in ALL_FEATURES:
            granted.add(f)
    return Entitlements(
        tier=tier,
        features=frozenset(granted),
        max_major=payload.get("max_major"),
        valid_until=payload.get("valid_until"),
        seats=int(payload.get("seats", 1)),
    )


def _verify_payload(token: str) -> Optional[dict]:
    """Return the payload dict iff the token's signature checks out. Fails closed."""
    pub_hex = _pubkey_hex()
    if not pub_hex or not token or "." not in token:
        return None
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        payload_b64, sig_b64 = token.strip().split(".", 1)
        payload_bytes = _b64d(payload_b64)
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
        pub.verify(_b64d(sig_b64), payload_bytes)  # raises on a bad signature
        return json.loads(payload_bytes.decode("utf-8"))
    except Exception:
        return None


def status(token: Optional[str], app_major: int = APP_MAJOR,
           today: Optional[date] = None) -> dict:
    """Resolve a token to a UI-friendly status. Always returns the same keys.

    status is one of: none, invalid, expired, version_exceeded, ok. tier/features
    are the EFFECTIVE grants right now (free unless status == ok); licensed_tier/
    licensed_features are what the token says, so the UI can show, for example,
    "your licence covers version 1, version 2 is available as an upgrade".
    """
    today = today or date.today()
    base = {
        "status": "none", "tier": "free", "licensed_tier": None,
        "features": [], "licensed_features": [], "valid_until": None,
        "max_major": None, "seats": 1, "app_major": app_major,
    }
    if not token:
        return base
    payload = _verify_payload(token)
    if payload is None:
        base["status"] = "invalid"
        return base
    ent = _resolve(payload)
    state = "ok"
    if ent.valid_until:
        try:
            if date.fromisoformat(ent.valid_until) < today:
                state = "expired"
        except ValueError:
            state = "invalid"
    if state == "ok" and ent.max_major is not None and app_major > ent.max_major:
        state = "version_exceeded"
    base.update({
        "status": state,
        "tier": ent.tier if state == "ok" else "free",
        "licensed_tier": ent.tier,
        "features": sorted(ent.features) if state == "ok" else [],
        "licensed_features": sorted(ent.features),
        "valid_until": ent.valid_until,
        "max_major": ent.max_major,
        "seats": ent.seats,
    })
    return base


def current(app_major: int = APP_MAJOR) -> Entitlements:
    """The entitlements in force right now (free unless a stored licence is ok)."""
    st = status(load_token(), app_major=app_major)
    if st["status"] != "ok":
        return FREE
    return Entitlements(
        tier=st["tier"], features=frozenset(st["features"]),
        max_major=st["max_major"], valid_until=st["valid_until"], seats=st["seats"],
    )


# --- stored licence --------------------------------------------------------

def load_token() -> Optional[str]:
    try:
        return _LICENSE_PATH.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def save_token(token: str) -> dict:
    """Validate, then persist. Raises ValueError on a non-genuine token so the
    caller can show a clear activation failure."""
    if _verify_payload(token) is None:
        raise ValueError("This licence key is not valid.")
    _LICENSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _LICENSE_PATH.write_text(token.strip(), encoding="utf-8")
    return status(token)


def clear_token() -> None:
    try:
        _LICENSE_PATH.unlink()
    except OSError:
        pass


# --- issuing (CLI, offline) ------------------------------------------------

def _keygen() -> int:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    priv = Ed25519PrivateKey.generate()
    priv_hex = priv.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
        serialization.NoEncryption()).hex()
    pub_hex = priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    print("PRIVATE KEY (keep secret, this signs every licence you ever issue):")
    print("  " + priv_hex)
    print("PUBLIC KEY (paste into _PUBLIC_KEY_HEX in licensing.py, safe to ship):")
    print("  " + pub_hex)
    return 0


def _sign(args) -> int:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    payload = {
        "tier": args.tier,
        "features": [f.strip() for f in (args.features or "").split(",") if f.strip()],
        "max_major": args.max_major,
        "valid_until": args.valid_until,
        "seats": args.seats,
        "issued_to": args.issued_to,
        "issued_at": datetime.now().date().isoformat(),
    }
    payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    priv = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(args.private_key))
    print(_b64e(payload_bytes) + "." + _b64e(priv.sign(payload_bytes)))
    return 0


def _verify(args) -> int:
    st = status(args.token)
    print(json.dumps(st, indent=2))
    return 0 if st["status"] == "ok" else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="live_transcribe.licensing")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("keygen", help="generate a new Ed25519 keypair")
    s = sub.add_parser("sign", help="sign a licence with your private key")
    s.add_argument("private_key", help="private key hex from keygen")
    s.add_argument("--tier", default="pro", choices=["free", "pro"])
    s.add_argument("--features", default="", help="comma add-ons, e.g. cloud_api,multi_seat")
    s.add_argument("--max-major", type=int, default=None, dest="max_major",
                   help="highest major version this licence covers (perpetual model)")
    s.add_argument("--valid-until", default=None, dest="valid_until",
                   help="ISO expiry date, e.g. 2027-06-30 (subscription model)")
    s.add_argument("--seats", type=int, default=1)
    s.add_argument("--issued-to", default="", dest="issued_to")
    v = sub.add_parser("verify", help="inspect and validate a token")
    v.add_argument("token")
    args = p.parse_args(argv)
    return {"keygen": lambda: _keygen(), "sign": lambda: _sign(args),
            "verify": lambda: _verify(args)}[args.cmd]()


if __name__ == "__main__":
    sys.exit(main())
