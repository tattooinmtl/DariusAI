import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dariusai.brain import secrets


def test_encrypt_decrypt_round_trip():
    plaintext = "sk-ant-verySecretKey1234567890"
    ciphertext = secrets.encrypt(plaintext)
    assert isinstance(ciphertext, bytes)
    assert ciphertext != plaintext.encode("utf-8")
    assert plaintext.encode("utf-8") not in ciphertext  # not just base64 wrapping, actually opaque

    decrypted = secrets.decrypt(ciphertext)
    assert decrypted == plaintext


def test_empty_string_round_trips_to_empty():
    assert secrets.encrypt("") == b""
    assert secrets.decrypt(b"") == ""


def test_different_secrets_produce_different_ciphertext():
    a = secrets.encrypt("key-one")
    b = secrets.encrypt("key-two")
    assert a != b


def test_mask_keeps_only_last_n_chars():
    assert secrets.mask("sk-ant-1234567890", keep=4) == "•" * 13 + "7890"
    assert secrets.mask("abc", keep=4) == "•••"  # shorter than keep — fully masked, no crash
