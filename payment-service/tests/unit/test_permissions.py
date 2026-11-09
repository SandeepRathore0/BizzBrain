from app.core.permissions import has_permission


def test_has_permission():
    assert has_permission("OWNER", "payments.view") is True
    assert has_permission("ADMIN", "payments.view") is True
    assert has_permission("EMPLOYEE", "payments.view") is True
    assert has_permission("GUEST", "payments.view") is False
    assert has_permission("OWNER", "unknown.perm") is False
