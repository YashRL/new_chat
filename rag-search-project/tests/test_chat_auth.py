from types import SimpleNamespace

from chat.chat import _require_user


def test_require_user_accepts_valid_user_id():
    request = SimpleNamespace(state=SimpleNamespace(user_id="user-guest", username="guest@system.local"))

    assert _require_user(request) == "user-guest"
    assert request.state.user_id == "user-guest"


def test_require_user_falls_back_to_username_for_stale_token_id():
    request = SimpleNamespace(state=SimpleNamespace(user_id="id", username="yash@talenteam.com"))

    resolved = _require_user(request)

    assert resolved == "de451af1f4dcbef8839b2288f1b1fafd"
    assert request.state.user_id == "de451af1f4dcbef8839b2288f1b1fafd"
