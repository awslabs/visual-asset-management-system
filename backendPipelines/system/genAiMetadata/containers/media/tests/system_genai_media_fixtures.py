#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Synthetic fixtures for the media container tests, generated in-process so the repository carries no
binary files: PNG/JPEG/GIF via Pillow, WAV via the wave module, a hand-built PDF with a computed xref, and
CSV/FCS/SVG/tileset bytes."""

import io
import json
import os
import wave

from PIL import Image

from media_extractors.common import ExtractContext


def make_ctx(tmp_path, file_name, max_text_chars=12_000, content_type="application/octet-stream",
             extract_geo_location=False):
    work_dir = tmp_path / "work"
    work_dir.mkdir(exist_ok=True)
    return ExtractContext(
        file_name=file_name,
        extension=os.path.splitext(file_name)[1].lower(),
        content_type=content_type,
        max_text_chars=max_text_chars,
        work_dir=str(work_dir),
        extract_geo_location=extract_geo_location,
    )


def write(tmp_path, name, data):
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


def png_bytes(width=3000, height=1000, mode="RGB"):
    colour = (200, 30, 30) if mode == "RGB" else 128
    image = Image.new(mode, (width, height), colour)
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def noise_png_bytes(width=400, height=400):
    """Incompressible pixels, so the PNG stays large until the image is downscaled."""
    image = Image.frombytes("RGB", (width, height), os.urandom(width * height * 3))
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


# GPS IFD contents as the fixture writes them: (degrees, minutes, seconds) rationals with a hemisphere reference,
# altitude in metres with a 0 (above) / 1 (below sea level) reference. Floats, so Pillow encodes RATIONALs and
# reads back IFDRational triples the way a camera's EXIF does.
SYDNEY_GPS = {"lat": (33.0, 51.0, 54.3), "lat_ref": "S", "lon": (151.0, 12.0, 35.8), "lon_ref": "E",
              "alt": 12.5, "alt_ref": 0}      # -33.865083, 151.209944, 12.5
SEATTLE_GPS = {"lat": (47.0, 36.0, 35.0), "lat_ref": "N", "lon": (122.0, 19.0, 59.0), "lon_ref": "W",
               "alt": 56.0, "alt_ref": 0}     # 47.609722, -122.333056, 56.0


def jpeg_with_exif_bytes(gps=None):
    """A JPEG with make/model/orientation and DateTimeOriginal. `gps=None` writes a GPS IFD that holds only a
    hemisphere reference (so `hasGps` is true and nothing decodes); a dict like SYDNEY_GPS writes latitude,
    longitude and, when it carries `alt`, altitude."""
    exif = Image.Exif()
    exif[271] = "ProtoMake"
    exif[272] = "ProtoModel"
    exif[274] = 6
    exif.get_ifd(0x8769)[36867] = "2026:01:02 03:04:05"
    gps_ifd = exif.get_ifd(0x8825)
    if gps is None:
        gps_ifd[1] = "N"
    else:
        gps_ifd[1] = gps["lat_ref"]
        gps_ifd[2] = gps["lat"]
        gps_ifd[3] = gps["lon_ref"]
        gps_ifd[4] = gps["lon"]
        if "alt" in gps:
            gps_ifd[5] = bytes([gps.get("alt_ref", 0)])
            gps_ifd[6] = gps["alt"]
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), (1, 2, 3)).save(buffer, "JPEG", exif=exif.tobytes())
    return buffer.getvalue()


def gif_bytes(frames=3):
    images = [Image.new("RGB", (20, 20), (index * 80, 10, 10)) for index in range(frames)]
    buffer = io.BytesIO()
    images[0].save(buffer, "GIF", save_all=True, append_images=images[1:], duration=100)
    return buffer.getvalue()


def wav_bytes(seconds=2.0, rate=8000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(seconds * rate))
    return buffer.getvalue()


def minimal_pdf_bytes(text="Hello VAMS vector search", pages=3, title="Proto Title", author="Proto Author",
                      creation_date="D:20260102030405Z"):
    """A valid PDF: catalog, page tree, one Helvetica text object per page, an indirect Info dictionary (Title,
    Author and, unless `creation_date` is None, a CreationDate in the PDF `D:` form), and an xref table with
    computed offsets."""
    objects = []
    font_number = 3 + 2 * pages
    info_number = font_number + 1
    kids = " ".join(f"{3 + 2 * index} 0 R" for index in range(pages))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode())
    for index in range(pages):
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] "
            f"/Resources << /Font << /F1 {font_number} 0 R >> >> /Contents {4 + 2 * index} 0 R >>".encode())
        stream = f"BT /F1 18 Tf 20 100 Td ({text} page {index + 1}) Tj ET".encode()
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    info = f"<< /Title ({title}) /Author ({author})"
    if creation_date:
        info += f" /CreationDate ({creation_date})"
    objects.append((info + " >>").encode())
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info {info_number} 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def csv_bytes(delimiter=","):
    rows = [["name", "qty", "price"], ["bolt", "10", "0.25"], ["nut", "20", "0.10"]]
    return "".join(delimiter.join(row) + "\n" for row in rows).encode("utf-8")


def fcs_bytes(params=("FSC-A", "SSC-A", "FL1-A"), events=1234):
    """FCS3.0: a 58-byte header naming the TEXT segment offsets, a '/'-delimited keyword TEXT segment, and a
    placeholder DATA segment that no parser here reads."""
    pairs = [("$PAR", str(len(params))), ("$TOT", str(events)), ("$DATATYPE", "F"), ("$MODE", "L"),
             ("$BYTEORD", "1,2,3,4"), ("$CYT", "ProtoCytometer")]
    pairs += [(f"$P{index + 1}N", name) for index, name in enumerate(params)]
    pairs += [(f"$P{index + 1}B", "32") for index in range(len(params))]
    text = ("".join(f"/{key}/{value}" for key, value in pairs) + "/").encode("ascii")
    text_start = 58
    text_end = text_start + len(text) - 1
    data = b"\x00" * (4 * len(params) * 2)
    data_start = text_end + 1
    data_end = data_start + len(data) - 1
    header = b"FCS3.0" + b" " * 4 + f"{text_start:>8}{text_end:>8}{data_start:>8}{data_end:>8}{0:>8}{0:>8}".encode()
    assert len(header) == 58
    return header + text + data


def svg_bytes():
    return (b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" viewBox="0 0 200 100">'
            b'<rect width="10" height="10"/><text x="5" y="50">Label</text></svg>')


def tileset_dict():
    return {
        "asset": {"version": "1.1", "tilesetVersion": "2026-09"},
        "geometricError": 500,
        "extensionsUsed": ["3DTILES_metadata"],
        "root": {
            "boundingVolume": {"region": [-1.32, 0.69, -1.31, 0.70, 0, 88]},
            "geometricError": 100,
            "refine": "ADD",
            "transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
            "children": [
                {"boundingVolume": {"region": [-1.32, 0.69, -1.315, 0.695, 0, 88]}, "geometricError": 10,
                 "content": {"uri": "tiles/a.b3dm"}},
                {"boundingVolume": {"region": [-1.315, 0.69, -1.31, 0.695, 0, 88]}, "geometricError": 10,
                 "content": {"uri": "external/tileset.json"}},
            ],
        },
    }


def geojson_point_dict():
    """A single Point Feature (Sydney) with properties."""
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [151.2, -33.9]},
            "properties": {"name": "Sydney", "kind": "city"}}


def geojson_collection_dict():
    """A FeatureCollection of a Point, a Polygon and a GeometryCollection (LineString + Point): nine positions,
    bbox longitude 0..151.2, latitude -33.9..20."""
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [151.2, -33.9]},
         "properties": {"name": "Sydney", "kind": "city"}},
        {"type": "Feature",
         "geometry": {"type": "Polygon", "coordinates": [[[10, 10], [20, 10], [20, 20], [10, 20], [10, 10]]]},
         "properties": {"name": "Box"}},
        {"type": "Feature",
         "geometry": {"type": "GeometryCollection", "geometries": [
             {"type": "LineString", "coordinates": [[0, 0], [5, 5]]},
             {"type": "Point", "coordinates": [2, 3]}]},
         "properties": {"name": "Mixed", "note": "x"}},
    ]}


ENGLISH_TEXT = (
    "The quick brown fox jumps over the lazy dog. This is a paragraph of English prose that carries enough "
    "of the common words for the language guess to settle on English, and it is long enough to be counted "
    "as more than twenty words in the sample that the guesser reads.\n"
)


# ffmpeg 7.x stderr for a 1080p H.264/AAC MP4 recorded in portrait (display-matrix rotation), as printed by
# `ffmpeg -hide_banner -nostdin -i clip.mp4 -frames:v 1 -frames:a 1 -f null -`.
FFMPEG_HEADER_SAMPLE = """Input #0, mov,mp4,m4a,3gp,3g2,mj2, from 'clip.mp4':
  Metadata:
    major_brand     : isom
    minor_version   : 512
    compatible_brands: isomiso2avc1mp41
    encoder         : Lavf60.3.100
  Duration: 00:01:32.48, start: 0.000000, bitrate: 4628 kb/s
  Stream #0:0[0x1](und): Video: h264 (High) (avc1 / 0x31637661), yuv420p(tv, bt709, progressive), 1920x1080 [SAR 1:1 DAR 16:9], 4500 kb/s, 29.97 fps, 29.97 tbr, 30k tbn (default)
      Metadata:
        handler_name    : VideoHandler
        vendor_id       : [0][0][0][0]
      Side data:
        displaymatrix: rotation of -90.00 degrees
  Stream #0:1[0x2](eng): Audio: aac (LC) (mp4a / 0x6134706D), 48000 Hz, stereo, fltp, 128 kb/s (default)
      Metadata:
        handler_name    : SoundHandler
        vendor_id       : [0][0][0][0]
Stream mapping:
  Stream #0:0 -> #0:0 (h264 (native) -> wrapped_avframe (native))
  Stream #0:1 -> #0:1 (aac (native) -> pcm_s16le (native))
Output #0, null, to 'pipe:':
  Metadata:
    encoder         : Lavf61.7.100
  Stream #0:0(und): Video: wrapped_avframe, yuv420p(tv, bt709, progressive), 1080x1920 [SAR 1:1 DAR 9:16], q=2-31, 200 kb/s, 29.97 fps, 29.97 tbn (default)
  Stream #0:1(eng): Audio: pcm_s16le, 48000 Hz, stereo, s16, 1536 kb/s (default)
frame=    1 fps=0.0 q=-0.0 Lsize=N/A time=00:00:00.03 bitrate=N/A speed=1.2x
"""

# The same clip with format-level tags, as a tagged MP4 prints them; the stream-level Metadata blocks (eight
# spaces deep) are unchanged, so a parser that read them would pick up `handler_name` by mistake.
FFMPEG_HEADER_WITH_TAGS = FFMPEG_HEADER_SAMPLE.replace(
    "    encoder         : Lavf60.3.100\n",
    "    encoder         : Lavf60.3.100\n"
    "    title           : Proto Clip\n"
    "    artist          : Proto Artist\n"
    "    album           : Proto Album\n"
    "    date            : 2026-03-01\n",
)


class FakeFfmpeg:
    """Stands in for subprocess.run. The `-f null -` probe is answered with a canned header on stderr; a `-ss`
    keyframe command writes a PNG to its output path (or fails when its time is in `fail_at`). Every argv is
    recorded in `calls`."""

    def __init__(self, header=FFMPEG_HEADER_SAMPLE, fail_at=(), probe_returncode=0):
        self.header = header
        self.fail_at = tuple(fail_at)
        self.probe_returncode = probe_returncode
        self.calls = []

    def __call__(self, argv, **kwargs):
        import subprocess

        self.calls.append(list(argv))
        if "null" in argv:
            return subprocess.CompletedProcess(argv, self.probe_returncode, stdout=b"", stderr=self.header.encode())
        if "-ss" in argv:
            seconds = float(argv[argv.index("-ss") + 1])
            output = argv[-1]
            if any(abs(seconds - failing) < 1e-6 for failing in self.fail_at):
                return subprocess.CompletedProcess(argv, 1, stdout=b"", stderr=b"Output file is empty, nothing was encoded")
            Image.new("RGB", (64, 36), (10, 20, 30)).save(output, "PNG")
            return subprocess.CompletedProcess(argv, 0, stdout=b"", stderr=b"")
        raise AssertionError(f"unexpected ffmpeg call: {argv}")


def fake_read_frames(path):
    """imageio_ffmpeg.read_frames stand-in: yields the metadata dict and then nothing."""
    yield {
        "ffmpeg_version": "7.1-static",
        "codec": "h264",
        "pix_fmt": "yuv420p",
        "fps": 29.97,
        "source_size": (1920, 1080),
        "size": (1920, 1080),
        "rotate": 0,
        "duration": 92.48,
        "audio_codec": "aac",
    }


def failing_read_frames(path):
    raise IOError("Could not load meta information")
    yield  # pragma: no cover - makes this a generator function like the real one


class FakeS3:
    """The three S3 calls the handler makes, over an in-memory (bucket, key) -> bytes store."""

    def __init__(self):
        self.objects = {}
        self.puts = []
        self.downloads = []

    def get_object(self, Bucket, Key):
        from botocore.exceptions import ClientError

        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey", "Message": Key}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def put_object(self, Bucket, Key, Body, **kwargs):
        payload = Body.read() if hasattr(Body, "read") else Body
        self.objects[(Bucket, Key)] = payload
        self.puts.append((Bucket, Key, kwargs.get("ContentType")))
        return {}

    def download_file(self, Bucket, Key, Filename, ExtraArgs=None):
        self.downloads.append((Bucket, Key, (ExtraArgs or {}).get("VersionId")))
        with open(Filename, "wb") as handle:
            handle.write(self.objects[(Bucket, Key)])

    def manifest(self, bucket, key):
        return json.loads(self.objects[(bucket, key)])
