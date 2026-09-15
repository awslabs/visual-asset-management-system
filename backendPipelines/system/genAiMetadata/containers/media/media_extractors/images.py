# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Raster images (.png .jpg .jpeg .gif .webp): dimensions, mode and an EXIF subset -> sys_image; one PNG
normalised for the vision model. EXIF GPS becomes `exif.gps` in decimal degrees only when the context's
extract_geo_location flag (the state's extractGeoLocation) is true."""

import math
from typing import Dict, Optional

from PIL import Image, UnidentifiedImageError

from .common import (
    CLASS_IMAGE,
    MAX_RASTER_PIXELS,
    RENDER_SKIPPED_ERROR,
    RENDER_SKIPPED_SIZE,
    BranchResult,
    ExtractContext,
    human_count,
)
from .imaging import normalise_for_vision, write_png

# Tag id -> attribute key.
_IFD0_TAGS = {
    270: "description", 271: "make", 272: "model", 274: "orientation", 305: "software", 306: "dateTime",
    315: "artist", 33432: "copyright",
}
_EXIF_IFD_TAGS = {
    33434: "exposureTime", 33437: "fNumber", 34855: "iso", 36867: "dateTimeOriginal", 37386: "focalLength",
    42036: "lensModel",
}
_EXIF_IFD_POINTER = 0x8769
_GPS_IFD_POINTER = 0x8825
# GPS IFD tag ids (PIL.ExifTags.GPS): latitude/longitude are (degrees, minutes, seconds) rationals with a
# hemisphere reference; altitude is one rational with a 0 (above) / 1 (below sea level) reference.
_GPS_LATITUDE_REF, _GPS_LATITUDE, _GPS_LONGITUDE_REF, _GPS_LONGITUDE = 1, 2, 3, 4
_GPS_ALTITUDE_REF, _GPS_ALTITUDE = 5, 6
_GPS_DECIMALS = 6
_EXIF_VALUE_MAX_CHARS = 200


def _plain(value):
    """EXIF values as JSON-friendly scalars: rationals become floats, bytes become text, the rest strings."""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace").strip("\x00")[:_EXIF_VALUE_MAX_CHARS]
    if isinstance(value, (int, float)):
        return value
    if hasattr(value, "numerator") and hasattr(value, "denominator"):
        try:
            return float(value)
        except (TypeError, ValueError, ZeroDivisionError):
            return str(value)
    return str(value)[:_EXIF_VALUE_MAX_CHARS]


def _finite(value) -> Optional[float]:
    """A rational or number as a finite float; None for a zero-denominator rational (Pillow's nan) or junk."""
    try:
        number = float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return number if math.isfinite(number) else None


def _reference(value) -> str:
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("ascii", "replace")
    return str(value or "").strip().upper()[:1]


def _degrees(dms, reference, negative_reference: str) -> Optional[float]:
    """(degrees, minutes, seconds) with a hemisphere reference -> signed decimal degrees."""
    if not isinstance(dms, (tuple, list)) or len(dms) != 3:
        return None
    parts = [_finite(part) for part in dms]
    if any(part is None for part in parts):
        return None
    degrees = parts[0] + parts[1] / 60.0 + parts[2] / 3600.0
    return -degrees if _reference(reference) == negative_reference else degrees


def gps_decimal(gps_ifd) -> Optional[Dict[str, Optional[float]]]:
    """`{latitude, longitude, altitude}` from a GPS IFD, or None when it carries no decodable position. South
    and west are negative; a missing hemisphere reference reads as north/east; altitude is metres, negative
    below sea level, None when absent. A non-finite rational or a value outside +-90/+-180 rejects the block."""
    try:
        items = dict(gps_ifd or {})
    except (TypeError, ValueError):
        return None
    latitude = _degrees(items.get(_GPS_LATITUDE), items.get(_GPS_LATITUDE_REF), "S")
    longitude = _degrees(items.get(_GPS_LONGITUDE), items.get(_GPS_LONGITUDE_REF), "W")
    if latitude is None or longitude is None or abs(latitude) > 90 or abs(longitude) > 180:
        return None
    altitude = _finite(items[_GPS_ALTITUDE]) if _GPS_ALTITUDE in items else None
    if altitude is not None:
        reference = items.get(_GPS_ALTITUDE_REF)
        if isinstance(reference, (bytes, bytearray)):
            reference = reference[0] if reference else 0
        if reference in (1, "1"):
            altitude = -abs(altitude)
        altitude = round(altitude, 2)
    return {"latitude": round(latitude, _GPS_DECIMALS), "longitude": round(longitude, _GPS_DECIMALS), "altitude": altitude}


def exif_subset(img: Image.Image, extract_geo_location: bool = False) -> Dict[str, object]:
    """The EXIF keys the pipeline records. `hasGps` says whether a GPS IFD exists; `gps` carries the decoded
    position only when `extract_geo_location` is true and the block decodes."""
    try:
        exif = img.getexif()
    except Exception:  # noqa: BLE001 - a corrupt EXIF block does not fail the image
        return {}
    subset: Dict[str, object] = {}
    for tag, key in _IFD0_TAGS.items():
        if tag in exif:
            subset[key] = _plain(exif.get(tag))
    try:
        exif_ifd = exif.get_ifd(_EXIF_IFD_POINTER)
    except Exception:  # noqa: BLE001
        exif_ifd = {}
    for tag, key in _EXIF_IFD_TAGS.items():
        if tag in exif_ifd:
            subset[key] = _plain(exif_ifd.get(tag))
    try:
        gps_ifd = dict(exif.get_ifd(_GPS_IFD_POINTER))
    except Exception:  # noqa: BLE001
        gps_ifd = {}
    has_gps = bool(gps_ifd)
    if subset or has_gps:
        subset["hasGps"] = has_gps
    if has_gps and extract_geo_location:
        gps = gps_decimal(gps_ifd)
        if gps is not None:
            subset["gps"] = gps
    return subset


def _header_only(ctx: ExtractContext) -> dict:
    return {"format": ctx.extension.lstrip(".").upper() or "UNKNOWN", "decodable": False}


def extract_image(path: str, ctx: ExtractContext) -> BranchResult:
    result = BranchResult(file_class=CLASS_IMAGE)
    try:
        img = Image.open(path)
    except Image.DecompressionBombError as exc:
        result.attributes["sys_image"] = _header_only(ctx)
        result.render_skipped = RENDER_SKIPPED_SIZE
        result.warnings.append(f"Image exceeds the decode pixel budget and was not opened: {exc}")
        return result
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        result.attributes["sys_image"] = _header_only(ctx)
        result.render_skipped = RENDER_SKIPPED_ERROR
        result.warnings.append(f"Image could not be decoded: {exc}")
        return result
    with img:
        width, height = img.size
        frames = int(getattr(img, "n_frames", 1) or 1)
        sys_image: Dict[str, object] = {
            "format": img.format or ctx.extension.lstrip(".").upper(),
            "width": width,
            "height": height,
            "mode": img.mode,
            "megapixels": round(width * height / 1_000_000, 2),
            "frames": frames,
            "animated": frames > 1,
            "hasAlpha": img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info,
        }
        dpi = img.info.get("dpi")
        if dpi:
            sys_image["dpi"] = [int(round(float(dpi[0]))), int(round(float(dpi[1])))]
        exif = exif_subset(img, ctx.extract_geo_location)
        if exif:
            sys_image["exif"] = exif
        result.attributes["sys_image"] = sys_image
        result.facts["dimensions"] = f"{width} x {height} px"
        result.facts["imageFormat"] = str(sys_image["format"])
        if frames > 1:
            result.facts["frames"] = human_count(frames, "frame")
        camera = " ".join(str(exif[key]) for key in ("make", "model") if exif.get(key))
        if camera:
            result.facts["camera"] = camera
        if exif.get("dateTimeOriginal"):
            result.facts["captured"] = str(exif["dateTimeOriginal"])
        gps = exif.get("gps")
        if isinstance(gps, dict):
            result.facts["location"] = f"{gps['latitude']}, {gps['longitude']}"
        elif ctx.extract_geo_location and exif.get("hasGps"):
            result.warnings.append("EXIF GPS block present but its coordinates did not decode; no gps recorded")
        if width * height > MAX_RASTER_PIXELS:
            result.render_skipped = RENDER_SKIPPED_SIZE
            result.warnings.append(
                f"Image has {width * height:,} pixels, above the {MAX_RASTER_PIXELS:,} raster budget; "
                f"attributes only")
            return result
        try:
            img.seek(0)
            png, size = normalise_for_vision(img.copy())
        except Exception as exc:  # noqa: BLE001 - a truncated or exotic encoding keeps the attributes
            result.render_skipped = RENDER_SKIPPED_ERROR
            result.warnings.append(f"Image could not be normalised for analysis: {exc}")
            return result
    result.render_images.append(write_png(png, ctx.work_dir, "media-01.png"))
    result.facts["analysisImage"] = f"{size[0]} x {size[1]} px PNG"
    return result
