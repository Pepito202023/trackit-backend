"""Save public API URL (Cloudflare tunnel) for phones on any network."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.firebase_service import FirebaseService  # noqa: E402


def main():
    if len(sys.argv) < 2:
        print("Usage: python register_public_url.py https://xxx.trycloudflare.com")
        sys.exit(1)

    raw = sys.argv[1].strip().rstrip("/")
    api_base = raw if raw.endswith("/api") else f"{raw}/api"

    backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tunnel_file = os.path.join(backend_root, "tunnel_url.txt")
    with open(tunnel_file, "w", encoding="utf-8") as f:
        f.write(api_base)

    svc = FirebaseService()
    result = svc.update_settings({"publicApiBaseUrl": api_base})
    if result.get("success"):
        print(f"[OK] Public API URL registered: {api_base}")
    else:
        print(f"[WARN] Saved to file only: {api_base}")
        print(result.get("error", ""))


if __name__ == "__main__":
    main()
