# Checks a base64 image before anything uses it: the request (contracts.py, HTTP 422) and the
# visual motor (app/motors/visual/model.py) both go through decode_image().

import base64
import binascii
from typing import Tuple

from app import config


class InvalidImage(ValueError):
    """The image cannot be sent to a model. The message is safe to show to the app."""


# First bytes of each image format Bedrock accepts.
IMAGE_SIGNATURES = {b"\xff\xd8\xff": "jpeg", b"\x89PNG": "png", b"GIF8": "gif", b"RIFF": "webp"}


def decode_image(image: str) -> Tuple[bytes, str]:
    """The image bytes and their format ("jpeg", "png", "gif" or "webp"). Raises InvalidImage."""
    try:
        raw = base64.b64decode(image, validate=True)
    except (binascii.Error, ValueError):
        raise InvalidImage("image is not valid base64") from None
    if len(raw) > config.MAX_IMAGE_BYTES:
        raise InvalidImage("image is larger than 3.75 MB")
    for signature, image_format in IMAGE_SIGNATURES.items():
        if raw.startswith(signature):
            return raw, image_format
    raise InvalidImage("image must be JPEG, PNG, GIF or WEBP")
