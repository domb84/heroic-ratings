#!/usr/bin/env python3
"""Draw the app icon (rounded square + gold star) as a PNG using only the stdlib."""
import math
import struct
import sys
import zlib

SIZE = 256
SS = 3  # supersampling for smooth edges


def star(cx, cy, r_out, r_in, n=5):
    pts = []
    for i in range(n * 2):
        a = -math.pi / 2 + i * math.pi / n
        r = r_out if i % 2 == 0 else r_in
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def inside(poly, x, y):
    c = False
    j = len(poly) - 1
    for i, (xi, yi) in enumerate(poly):
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            c = not c
        j = i
    return c


def in_rounded(x, y, size, rad):
    cx = min(max(x, rad), size - rad)
    cy = min(max(y, rad), size - rad)
    return (x - cx) ** 2 + (y - cy) ** 2 <= rad ** 2


def render(path):
    S = SIZE
    st = star(S / 2, S * 0.53, S * 0.36, S * 0.15)
    rows = []
    for py in range(S):
        row = bytearray([0])
        for px in range(S):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sy in range(SS):
                for sx in range(SS):
                    x = px + (sx + 0.5) / SS
                    y = py + (sy + 0.5) / SS
                    if not in_rounded(x, y, S, S * 0.2):
                        continue
                    if inside(st, x, y):
                        t = y / S
                        col = (255, int(214 - 40 * t), int(80 - 40 * t))  # gold
                    else:
                        t = y / S
                        col = (int(22 + 10 * t), int(70 + 60 * t), int(140 + 80 * t))  # deep blue
                    acc[0] += col[0]; acc[1] += col[1]; acc[2] += col[2]; acc[3] += 255
            n = SS * SS
            a = acc[3] / n
            if a:
                row += bytes([int(acc[0] / (acc[3] / 255)), int(acc[1] / (acc[3] / 255)),
                              int(acc[2] / (acc[3] / 255)), int(a)])
            else:
                row += b"\0\0\0\0"
        rows.append(bytes(row))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", S, S, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


if __name__ == "__main__":
    render(sys.argv[1] if len(sys.argv) > 1 else "icon.png")
