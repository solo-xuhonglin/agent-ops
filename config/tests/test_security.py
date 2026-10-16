from config.security import hash_password, verify_password


def test_hash_is_argon2id_and_verifies():
    hashed = hash_password("s3cret-password")

    assert hashed.startswith("$argon2id$")
    assert "s3cret-password" not in hashed
    assert verify_password("s3cret-password", hashed) is True


def test_wrong_password_rejected():
    hashed = hash_password("s3cret-password")

    assert verify_password("wrong-password", hashed) is False
