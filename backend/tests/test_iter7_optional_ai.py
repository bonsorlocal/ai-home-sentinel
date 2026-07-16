"""Iteration 7: Verify backend still works after making emergentintegrations optional
and slimming requirements. Focus on assistant chat endpoint which uses the lazy import."""
import os
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://home-sentinel-ai-1.preview.emergentagent.com").rstrip("/")


def test_root_ok():
    r = requests.get(f"{BASE_URL}/api/", timeout=15)
    assert r.status_code == 200
    d = r.json()
    assert d.get("service") == "AI Home Sentinel"
    assert d.get("status") == "ok"


def test_assistant_chat_grounded():
    """emergentintegrations is installed in preview; lazy import should resolve and Gemini responds."""
    payload = {"session_id": "iter7-test", "message": "What events did the camera detect recently?"}
    r = requests.post(f"{BASE_URL}/api/assistant/chat", json=payload, timeout=90)
    assert r.status_code == 200, (r.status_code, r.text[:400])
    d = r.json()
    # Extract answer from any reasonable shape
    ans = d.get("answer") or d.get("reply") or d.get("message") or d.get("response") or ""
    if not ans and isinstance(d, dict):
        # nested
        for v in d.values():
            if isinstance(v, str) and len(v) > 10:
                ans = v
                break
    assert isinstance(ans, str) and len(ans) > 5, d
    low = ans.lower()
    # Should NOT report the library missing (that would mean lazy import failed)
    assert "not installed" not in low, ans
    assert "is not configured" not in low, ans
