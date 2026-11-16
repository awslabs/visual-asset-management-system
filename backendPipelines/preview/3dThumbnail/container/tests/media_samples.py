#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Sample images and videos for the media preview tests, generated in the test: nothing binary is
committed. Pillow writes the still formats, ``tifffile`` the TIFF variants Pillow cannot (16-bit
colour, float, BigTIFF, tiled, planar) and PyAV the videos."""

import os

import numpy as np
from PIL import Image


def _rng(seed=7):
    return np.random.default_rng(seed)


def gradient(width, height):
    """A smooth, compressible RGB image (red across, green down, constant blue)."""
    x = np.linspace(0, 255, width, dtype=np.uint8)[None, :]
    y = np.linspace(0, 255, height, dtype=np.uint8)[:, None]
    return np.stack([np.broadcast_to(x, (height, width)), np.broadcast_to(y, (height, width)),
                     np.full((height, width), 90, np.uint8)], axis=-1)


def noise(width, height, seed=7):
    """Incompressible RGB noise."""
    return (_rng(seed).random((height, width, 3)) * 255).astype(np.uint8)


def write_png(path, width=64, height=48, pixels=None):
    Image.fromarray(gradient(width, height) if pixels is None else pixels).save(path, "PNG")
    return path


def write_jpeg(path, width=64, height=48, pixels=None, quality=90, exif_orientation=None):
    image = Image.fromarray(gradient(width, height) if pixels is None else pixels)
    kwargs = {"quality": quality}
    if exif_orientation:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        kwargs["exif"] = exif
    image.save(path, "JPEG", **kwargs)
    return path


def write_png_of_size(path, target_bytes, height=900, seed=3):
    """A PNG of exactly ``target_bytes`` bytes: a noise image just under that size, padded to it with a
    tEXt chunk (12 bytes of framing, the key, a NUL and the text)."""
    from PIL.PngImagePlugin import PngInfo
    key = "pad"
    width = max(8, int(target_bytes / (height * 3)))
    while True:
        Image.fromarray(noise(width, height, seed)).save(path, "PNG", compress_level=1)
        if os.path.getsize(path) <= target_bytes - 16:
            break
        width = int(width * 0.97)
    missing = target_bytes - os.path.getsize(path)
    info = PngInfo()
    info.add_text(key, "x" * (missing - 12 - len(key) - 1))
    Image.fromarray(noise(width, height, seed)).save(path, "PNG", compress_level=1, pnginfo=info)
    assert os.path.getsize(path) == target_bytes, (os.path.getsize(path), target_bytes)
    return path


def write_bmp(path, width=64, height=48):
    Image.fromarray(gradient(width, height)).save(path, "BMP")
    return path


def write_tga(path, width=64, height=48, rle=False, rgba=False):
    pixels = gradient(width, height)
    image = Image.fromarray(pixels)
    if rgba:
        image = image.convert("RGBA")
        alpha = np.zeros((height, width), np.uint8)
        alpha[:, width // 2:] = 255
        image.putalpha(Image.fromarray(alpha))
    image.save(path, "TGA", compression="tga_rle" if rle else None)
    return path


def write_tiff_pillow(path, width=64, height=48, mode="RGB", compression="tiff_lzw", **kwargs):
    image = Image.fromarray(gradient(width, height))
    if mode != "RGB":
        image = image.convert(mode)
    image.save(path, "TIFF", compression=compression, **kwargs)
    return path


def write_tiff_16bit_rgb(path, width=64, height=48, **kwargs):
    import tifffile
    rgb = gradient(width, height).astype(np.uint16) * 257
    tifffile.imwrite(path, rgb, photometric="rgb", **kwargs)
    return path


def write_tiff_float(path, width=64, height=48, with_nan=True):
    import tifffile
    values = np.linspace(-3.5, 120.25, width * height, dtype=np.float32).reshape(height, width)
    if with_nan:
        values[0, :4] = np.nan
        values[1, :4] = np.inf
    tifffile.imwrite(path, values, photometric="minisblack")
    return path


def write_tiff_float_rgb(path, width=64, height=48):
    """Three float32 samples per pixel: a layout Pillow cannot open, read through tifffile."""
    import tifffile
    rgb = gradient(width, height).astype(np.float32) / 255.0 * 4000.0
    tifffile.imwrite(path, rgb, photometric="rgb")
    return path


def write_tiff_multipage(path, width=64, height=48):
    """A TIFF whose first page is a reduced-resolution copy and whose second is the full image."""
    import tifffile
    full = gradient(width, height)
    small = np.full((height // 4, width // 4, 3), 200, np.uint8)
    with tifffile.TiffWriter(path) as writer:
        writer.write(small, photometric="rgb", subfiletype=1)
        writer.write(full, photometric="rgb")
    return path


def write_tiff_bigtiff_tiled(path, width=256, height=192):
    import tifffile
    tifffile.imwrite(path, gradient(width, height), photometric="rgb", bigtiff=True,
                     tile=(64, 64), compression="zlib")
    return path


def write_tiff_planar(path, width=64, height=48):
    import tifffile
    planes = np.moveaxis(gradient(width, height), -1, 0)
    tifffile.imwrite(path, planes, photometric="rgb", planarconfig="separate")
    return path


def write_tiff_cmyk(path, width=64, height=48, cmyk=(0, 255, 255, 0)):
    """A CMYK TIFF filled with one colour (default 0,255,255,0 = red)."""
    image = Image.new("CMYK", (width, height), cmyk)
    image.save(path, "TIFF")
    return path


def write_geotiff(path, width=64, height=48):
    import tifffile
    # ModelPixelScale, ModelTiepoint and GeoKeyDirectory tags make a minimal GeoTIFF
    extratags = [
        (33550, "d", 3, (1.0, 1.0, 0.0), True),
        (33922, "d", 6, (0.0, 0.0, 0.0, 500000.0, 4000000.0, 0.0), True),
        (34735, "H", 8, (1, 1, 0, 1, 1024, 0, 1, 1), True),
    ]
    tifffile.imwrite(path, gradient(width, height), photometric="rgb", extratags=extratags)
    return path


def animated_frames(count, width, height, seed=11, noisy=True):
    """Frames of a moving pattern: noisy ones are incompressible, so the GIF's size scales with the
    frame count and the frame area."""
    rng = _rng(seed)
    frames = []
    for index in range(count):
        if noisy:
            frames.append(Image.fromarray((rng.random((height, width, 3)) * 255).astype(np.uint8)))
        else:
            array = gradient(width, height).copy()
            array[:, (index * 3) % width] = 255
            frames.append(Image.fromarray(array))
    return frames


def write_animated_gif(path, count=10, width=64, height=48, duration=100, noisy=False, seed=11):
    frames = animated_frames(count, width, height, seed, noisy)
    frames[0].save(path, "GIF", save_all=True, append_images=frames[1:], duration=duration, loop=0)
    return path


def write_animated_webp(path, count=10, width=64, height=48, duration=100, noisy=False, seed=11):
    frames = animated_frames(count, width, height, seed, noisy)
    frames[0].save(path, "WEBP", save_all=True, append_images=frames[1:], duration=duration, loop=0,
                   lossless=True)
    return path


def write_video(path, container_format, codec, width=160, height=120, frames=30, fps=10,
                rotation=None, pix_fmt="yuv420p", audio=False):
    """A video of ``frames`` frames whose red channel ramps with the frame index."""
    import av
    with av.open(path, mode="w", format=container_format) as container:
        stream = container.add_stream(codec, rate=fps)
        stream.width, stream.height, stream.pix_fmt = width, height, pix_fmt
        if rotation is not None:
            stream.set_display_rotation(rotation)
        for index in range(frames):
            array = np.zeros((height, width, 3), np.uint8)
            array[..., 0] = int(255 * index / max(1, frames - 1))
            array[: height // 2, :, 1] = 200  # the top half is green, to tell a rotation
            frame = av.VideoFrame.from_ndarray(array, format="rgb24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


def write_audio_only(path):
    import av
    with av.open(path, mode="w", format="matroska") as container:
        stream = container.add_stream("libopus" if "libopus" in av.codecs_available else "aac", rate=48000)
        stream.layout = "mono"
        samples = np.zeros((1, 960), dtype=np.float32)
        for _ in range(10):
            frame = av.AudioFrame.from_ndarray(samples, format="fltp", layout="mono")
            frame.sample_rate = 48000
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


def write_tiff_gray_alpha(path, associated=True, compression="tiff_lzw", width=64, height=48, alpha=200):
    """A gray+alpha TIFF. With ``associated`` the ExtraSamples tag says the alpha is associated
    (premultiplied), which Pillow reads as mode ``La``; Pillow writes only unassociated alpha, so the tag
    of a file it wrote is patched in place (a SHORT stored in the IFD entry)."""
    import struct
    gray = np.linspace(0, 255, width * height, dtype=np.uint8).reshape(height, width)
    alpha_plane = np.full((height, width), alpha, np.uint8)
    Image.fromarray(np.dstack([gray, alpha_plane]), "LA").save(path, "TIFF", compression=compression)
    if associated:
        data = bytearray(open(path, "rb").read())
        offset = struct.unpack("<I", data[4:8])[0]
        count = struct.unpack("<H", data[offset:offset + 2])[0]
        for index in range(count):
            entry = offset + 2 + 12 * index
            if struct.unpack("<H", data[entry:entry + 2])[0] == 338:  # ExtraSamples
                data[entry + 8:entry + 10] = struct.pack("<H", 1)
        open(path, "wb").write(bytes(data))
    return path


def write_tiff_mode(path, mode, compression=None, width=64, height=48):
    """A TIFF of Pillow mode ``mode`` (1, L, P, RGBA, CMYK, YCbCr, I, F, I;16) holding a gradient."""
    gray = np.linspace(0, 255, width * height, dtype=np.uint8).reshape(height, width)
    if mode == "1":
        image = Image.fromarray(gray > 100)
    elif mode == "L":
        image = Image.fromarray(gray)
    elif mode == "P":
        image = Image.fromarray(gray).convert("P")
    elif mode == "RGBA":
        image = Image.fromarray(np.dstack([gray, gray[::-1], gray, np.full_like(gray, 180)]), "RGBA")
    elif mode == "CMYK":
        image = Image.fromarray(np.dstack([gray, gray, gray, gray]), "CMYK")
    elif mode == "YCbCr":
        image = Image.fromarray(np.dstack([gray, gray, gray])).convert("YCbCr")
    elif mode == "I":
        image = Image.fromarray(gray.astype(np.int32), "I")
    elif mode == "F":
        image = Image.fromarray(gray.astype(np.float32), "F")
    elif mode == "I;16":
        image = Image.fromarray(gray.astype(np.uint16) * 257)
    else:
        raise ValueError(mode)
    image.save(path, "TIFF", compression=compression)
    return path


def write_tiff_gray16_alpha(path, width=64, height=48):
    """A 16-bit gray+alpha TIFF, which Pillow cannot open (tifffile reads it)."""
    import tifffile
    gray = np.linspace(0, 255, width * height, dtype=np.uint8).reshape(height, width).astype(np.uint16) * 257
    alpha_plane = np.full((height, width), 200 * 257, np.uint16)
    tifffile.imwrite(path, np.dstack([gray, alpha_plane]), photometric="minisblack",
                     extrasamples=["unassalpha"])
    return path
