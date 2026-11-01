"""Image helpers: read bytes, then cut them into a disc.

The preview in the window and whatever composites the finished frame need
the same disc, so the crop lives here rather than in either of them: an
anti-aliased circle at :data:`~action_studio.render.AVATAR_BASE_SIZE`, which is
what makes an avatar placed in the window line up with the result.

Nothing here needs the network, a token, or any player's source, and nothing here
awaits, so all of it is safe on a worker thread.
"""

from __future__ import annotations

import io
import logging

from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

# The ellipse mask is drawn at this multiple of the target size and scaled down,
# which is what keeps a 180 px circle smooth instead of stair-stepped.
MASK_SCALE = 3


def decode_image(data: bytes | None) -> Image.Image | None:
    """Decode image bytes into RGBA, or ``None`` for unusable input.

    Bytes come from a download or from a file on disk, so a truncated response or
    a format Pillow cannot read arrives as an ``OSError``/``ValueError`` rather
    than a useful error at the call site.

    Args:
        data: The bytes to decode; ``None`` or empty yields ``None``.

    Returns:
        An RGBA image, or ``None`` when the bytes are not an image.
    """
    if not data:
        return None
    try:
        with Image.open(io.BytesIO(data)) as image:
            return image.convert("RGBA")
    except (OSError, ValueError):
        logger.warning("Image bytes could not be decoded; treating it as missing")
        return None


def circle(image: Image.Image, size: int) -> Image.Image:
    """Crop ``image`` into a ``size`` x ``size`` circle with a transparent ring.

    The mask is drawn at ``MASK_SCALE`` times the target and rescaled with
    LANCZOS, so the edge is anti-aliased. The source is square-cropped by
    resizing without keeping aspect ratio: avatar images are already square,
    and stretching a non-square image is what a caller asked for by asking for
    a circle.

    Args:
        image: The source image; any mode, though RGBA gives the cleanest edge.
        size: The diameter of the resulting circle in pixels.

    Returns:
        An RGBA image of exactly ``size`` x ``size`` pixels.
    """
    square = image.resize((size, size))
    mask = Image.new("L", (size * MASK_SCALE, size * MASK_SCALE), 0)
    ImageDraw.Draw(mask).ellipse(
        (0, 0, size * MASK_SCALE - 1, size * MASK_SCALE - 1), fill=255
    )
    mask = mask.resize((size, size), Image.LANCZOS)
    square.putalpha(mask)
    return square
