import pytest

from nani_pix_bot.services.pixelate import fingerprint as fp
from tests.frames import encode, frame, frame_bytes


def test_identical_bytes_have_distance_zero() -> None:
    data = frame_bytes(1)

    assert fp.distance(fp.fingerprint(data), fp.fingerprint(data)) == 0


def test_resized_recompressed_copy_is_the_same_frame() -> None:
    copy = frame(1).resize((384, 216)).convert("RGB")

    a = fp.fingerprint(frame_bytes(1))
    b = fp.fingerprint(encode(copy, "JPEG", quality=60))

    assert fp.same_frame(a, b)


@pytest.mark.parametrize("other_seed", [2, 3, 4, 5, 6])
def test_a_different_frame_is_not_the_same_frame(other_seed: int) -> None:
    a = fp.fingerprint(frame_bytes(1))
    b = fp.fingerprint(frame_bytes(other_seed))

    assert not fp.same_frame(a, b)


def test_fingerprint_fits_a_signed_64_bit_column() -> None:
    for seed in range(20):
        value = fp.fingerprint(frame_bytes(seed))
        assert -(2**63) <= value < 2**63


def test_distance_works_across_the_sign_bit() -> None:
    assert fp.distance(-1, 0) == 64
    assert fp.distance(-1, -1) == 0


def test_undecodable_bytes_raise_value_error() -> None:
    with pytest.raises(ValueError, match="fingerprint"):
        fp.fingerprint(b"not an image")
