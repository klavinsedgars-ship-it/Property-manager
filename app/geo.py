"""Latvian grid coordinates -> WGS84 latitude/longitude.

LKS-92 TM (EPSG:3059) and its successor LKS-2020 TM share the projection:
Transverse Mercator on GRS80, central meridian 24°E, scale 0.9996,
false easting 500 000 m, false northing −6 000 000 m. (The datum shift
between them is centimetres — irrelevant for a deal map.)
"""
from math import cos, degrees, radians, sin, sqrt, tan

A = 6378137.0
F = 1 / 298.257222101
K0, LON0, FE, FN = 0.9996, radians(24.0), 500000.0, -6000000.0
E2 = F * (2 - F)
EP2 = E2 / (1 - E2)
E1 = (1 - sqrt(1 - E2)) / (1 + sqrt(1 - E2))


def lks_to_wgs84(easting: float, northing: float) -> tuple[float, float]:
    m = (northing - FN) / K0
    mu = m / (A * (1 - E2 / 4 - 3 * E2 ** 2 / 64 - 5 * E2 ** 3 / 256))
    phi1 = (mu + (3 * E1 / 2 - 27 * E1 ** 3 / 32) * sin(2 * mu)
            + (21 * E1 ** 2 / 16 - 55 * E1 ** 4 / 32) * sin(4 * mu)
            + (151 * E1 ** 3 / 96) * sin(6 * mu) + (1097 * E1 ** 4 / 512) * sin(8 * mu))
    s, c, t = sin(phi1), cos(phi1), tan(phi1)
    n1 = A / sqrt(1 - E2 * s * s)
    r1 = A * (1 - E2) / (1 - E2 * s * s) ** 1.5
    c1, t1 = EP2 * c * c, t * t
    d = (easting - FE) / (n1 * K0)
    lat = phi1 - (n1 * t / r1) * (d ** 2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * EP2) * d ** 4 / 24
                                  + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * EP2 - 3 * c1 ** 2) * d ** 6 / 720)
    lon = LON0 + (d - (1 + 2 * t1 + c1) * d ** 3 / 6
                  + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * EP2 + 24 * t1 ** 2) * d ** 5 / 120) / c
    return degrees(lat), degrees(lon)


def x_is_easting(xs, ys) -> bool:
    """Decide axis order for a whole file. Latvian eastings centre on ~500 km,
    northings on ~300 km, so the column with the larger mean is the easting."""
    xs, ys = [v for v in xs if v], [v for v in ys if v]
    if not xs or not ys:
        return True
    return sum(xs) / len(xs) >= sum(ys) / len(ys)


def to_latlon(x, y, x_easting=True):
    if x is None or y is None:
        return None
    e, n = (x, y) if x_easting else (y, x)
    lat, lon = lks_to_wgs84(e, n)
    return (round(lat, 6), round(lon, 6)) if in_latvia(lat, lon) else None


def in_latvia(lat, lon) -> bool:
    return lat is not None and lon is not None and 55.6 < lat < 58.2 and 20.8 < lon < 28.3


