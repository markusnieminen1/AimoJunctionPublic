"""Pure-stdlib ISO/IEC 18004 QR Code Model 2 SVG generator (Versions 1-10, EC Level M).

Zero external dependencies and zero outbound network calls — prevents leaking private
one-time Telegram pairing tokens to third-party QR rendering APIs.
"""

from __future__ import annotations

import html

# (version, align_centers, ec_codewords_per_block, ((num_blocks, data_codewords), ...))
_QR_V1_TO_V10_EC_M: tuple[
    tuple[int, tuple[int, ...], int, tuple[tuple[int, int], ...]],
    ...,
] = (
    (1, (), 10, ((1, 16),)),
    (2, (6, 18), 16, ((1, 28),)),
    (3, (6, 22), 26, ((1, 44),)),
    (4, (6, 26), 18, ((2, 32),)),
    (5, (6, 30), 24, ((2, 43),)),
    (6, (6, 34), 16, ((4, 27),)),
    (7, (6, 22, 38), 18, ((4, 31),)),
    (8, (6, 24, 42), 22, ((2, 38), (2, 39))),
    (9, (6, 26, 46), 22, ((3, 36), (2, 37))),
    (10, (6, 28, 50), 26, ((4, 43), (1, 44))),
)

# GF(256) log/exp tables with primitive polynomial x^8 + x^4 + x^3 + x^2 + 1 (0x11D)
_GF_EXP = [0] * 512
_GF_LOG = [0] * 256


def _init_gf256() -> None:
    val = 1
    for i in range(255):
        _GF_EXP[i] = val
        _GF_LOG[val] = i
        val <<= 1
        if val & 0x100:
            val ^= 0x11D
    for i in range(255, 512):
        _GF_EXP[i] = _GF_EXP[i - 255]


_init_gf256()


def _gf_mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _GF_EXP[_GF_LOG[a] + _GF_LOG[b]]


def _rs_generator_poly(degree: int) -> list[int]:
    """Return Reed-Solomon generator polynomial coefficients (highest degree first)."""
    poly = [1]
    for i in range(degree):
        root = _GF_EXP[i]
        nxt = [0] * (len(poly) + 1)
        for j, coeff in enumerate(poly):
            nxt[j] ^= coeff
            nxt[j + 1] ^= _gf_mul(coeff, root)
        poly = nxt
    return poly


def _rs_remainder(data: list[int], ec_len: int) -> list[int]:
    """Compute `ec_len` Reed-Solomon error-correction codewords for `data`."""
    gen = _rs_generator_poly(ec_len)
    rem = [0] * ec_len
    for byte in data:
        factor = byte ^ rem[0]
        rem = [*rem[1:], 0]
        if factor != 0:
            for j in range(ec_len):
                rem[j] ^= _gf_mul(gen[j + 1], factor)
    return rem


def compute_format_bits(ec_format_bits: int, mask_id: int) -> int:
    """Compute 15-bit BCH(15,5) format info with ISO 18004 XOR mask 0x5412."""
    data5 = ((ec_format_bits & 0b11) << 3) | (mask_id & 0b111)
    rem = data5 << 10
    for i in range(14, 9, -1):
        if (rem >> i) & 1:
            rem ^= 0x537 << (i - 10)
    return ((data5 << 10) | rem) ^ 0x5412


def compute_version_bits(version: int) -> int:
    """Compute 18-bit BCH(18,6) version info for QR versions >= 7."""
    rem = version << 12
    for i in range(17, 11, -1):
        if (rem >> i) & 1:
            rem ^= 0x1F25 << (i - 12)
    return (version << 12) | rem


def _select_version(
    payload_len: int,
) -> tuple[int, tuple[int, ...], int, tuple[tuple[int, int], ...]]:
    for spec in _QR_V1_TO_V10_EC_M:
        version, _, _, groups = spec
        total_data_cw = sum(nb * dcw for nb, dcw in groups)
        count_bits = 8 if version <= 9 else 16
        # 4 mode bits + count_bits + 8 * payload_len <= 8 * total_data_cw
        max_bytes = (total_data_cw * 8 - 4 - count_bits) // 8
        if payload_len <= max_bytes:
            return spec
    raise ValueError(f"QR payload too long ({payload_len} bytes; max supported is 213 bytes)")


def _encode_data_codewords(payload: bytes, version: int, total_data_cw: int) -> list[int]:
    bits: list[int] = [0, 1, 0, 0]  # Byte mode indicator 0100
    count_bits = 8 if version <= 9 else 16
    length = len(payload)
    for i in range(count_bits - 1, -1, -1):
        bits.append((length >> i) & 1)
    for byte in payload:
        for i in range(7, -1, -1):
            bits.append((byte >> i) & 1)

    capacity_bits = total_data_cw * 8
    terminator = min(4, capacity_bits - len(bits))
    bits.extend([0] * terminator)
    while len(bits) % 8 != 0:
        bits.append(0)

    codewords: list[int] = []
    for idx in range(0, len(bits), 8):
        val = 0
        for b in bits[idx : idx + 8]:
            val = (val << 1) | b
        codewords.append(val)

    pad_bytes = (0xEC, 0x11)
    pad_idx = 0
    while len(codewords) < total_data_cw:
        codewords.append(pad_bytes[pad_idx % 2])
        pad_idx += 1
    return codewords


def _interleave_blocks(
    data_cw: list[int],
    ec_per_block: int,
    groups: tuple[tuple[int, int], ...],
) -> list[int]:
    data_blocks: list[list[int]] = []
    ec_blocks: list[list[int]] = []
    offset = 0
    for num_blocks, block_len in groups:
        for _ in range(num_blocks):
            blk = data_cw[offset : offset + block_len]
            offset += block_len
            data_blocks.append(blk)
            ec_blocks.append(_rs_remainder(blk, ec_per_block))

    interleaved: list[int] = []
    max_data_len = max(len(b) for b in data_blocks)
    for col in range(max_data_len):
        for blk in data_blocks:
            if col < len(blk):
                interleaved.append(blk[col])
    for col in range(ec_per_block):
        for ec_blk in ec_blocks:
            interleaved.append(ec_blk[col])
    return interleaved


def _place_finder(matrix: list[list[int]], reserved: list[list[bool]], top: int, left: int) -> None:
    size = len(matrix)
    for dr in range(-1, 8):
        for dc in range(-1, 8):
            r, c = top + dr, left + dc
            if 0 <= r < size and 0 <= c < size:
                in_7x7 = 0 <= dr <= 6 and 0 <= dc <= 6
                if in_7x7:
                    is_border = dr in (0, 6) or dc in (0, 6)
                    is_core = 2 <= dr <= 4 and 2 <= dc <= 4
                    matrix[r][c] = 1 if (is_border or is_core) else 0
                else:
                    matrix[r][c] = 0
                reserved[r][c] = True


def _place_alignment(
    matrix: list[list[int]],
    reserved: list[list[bool]],
    center_r: int,
    center_c: int,
) -> None:
    for dr in range(-2, 3):
        for dc in range(-2, 3):
            r, c = center_r + dr, center_c + dc
            dist = max(abs(dr), abs(dc))
            matrix[r][c] = 1 if dist in (0, 2) else 0
            reserved[r][c] = True


def _write_format_bits(
    matrix: list[list[int]],
    reserved: list[list[bool]],
    mask_id: int,
) -> None:
    size = len(matrix)
    # Error correction level M has format indicator 0b00
    fmt = compute_format_bits(0b00, mask_id)

    # Copy 1: around top-left finder (bits 14..0)
    tl_coords = [
        (8, 0),
        (8, 1),
        (8, 2),
        (8, 3),
        (8, 4),
        (8, 5),
        (8, 7),
        (8, 8),
        (7, 8),
        (5, 8),
        (4, 8),
        (3, 8),
        (2, 8),
        (1, 8),
        (0, 8),
    ]
    for idx, (r, c) in enumerate(tl_coords):
        bit = (fmt >> (14 - idx)) & 1
        matrix[r][c] = bit
        reserved[r][c] = True

    # Copy 2: bottom-left vertical (bits 14..8) + top-right horizontal (bits 7..0)
    for idx in range(7):
        r = size - 1 - idx
        matrix[r][8] = (fmt >> (14 - idx)) & 1
        reserved[r][8] = True
    # Always-dark module at (size - 8, 8)
    matrix[size - 8][8] = 1
    reserved[size - 8][8] = True
    for idx in range(8):
        c = size - 8 + idx
        matrix[8][c] = (fmt >> (7 - idx)) & 1
        reserved[8][c] = True


def _write_version_bits(
    matrix: list[list[int]],
    reserved: list[list[bool]],
    version: int,
) -> None:
    if version < 7:
        return
    size = len(matrix)
    ver = compute_version_bits(version)
    for i in range(18):
        bit = (ver >> i) & 1
        r1, c1 = size - 11 + (i % 3), i // 3
        r2, c2 = i // 3, size - 11 + (i % 3)
        matrix[r1][c1] = bit
        reserved[r1][c1] = True
        matrix[r2][c2] = bit
        reserved[r2][c2] = True


def _mask_condition(mask_id: int, r: int, c: int) -> bool:
    if mask_id == 0:
        return (r + c) % 2 == 0
    if mask_id == 1:
        return r % 2 == 0
    if mask_id == 2:
        return c % 3 == 0
    if mask_id == 3:
        return (r + c) % 3 == 0
    if mask_id == 4:
        return (r // 2 + c // 3) % 2 == 0
    if mask_id == 5:
        return (r * c) % 2 + (r * c) % 3 == 0
    if mask_id == 6:
        return ((r * c) % 2 + (r * c) % 3) % 2 == 0
    return ((r + c) % 2 + (r * c) % 3) % 2 == 0


def _penalty_score(matrix: list[list[int]]) -> int:
    size = len(matrix)
    score = 0
    # N1: 5+ consecutive same-color modules in row/col
    for r in range(size):
        run_val = matrix[r][0]
        run_len = 1
        for c in range(1, size):
            if matrix[r][c] == run_val:
                run_len += 1
            else:
                if run_len >= 5:
                    score += 3 + (run_len - 5)
                run_val = matrix[r][c]
                run_len = 1
        if run_len >= 5:
            score += 3 + (run_len - 5)

    for c in range(size):
        run_val = matrix[0][c]
        run_len = 1
        for r in range(1, size):
            if matrix[r][c] == run_val:
                run_len += 1
            else:
                if run_len >= 5:
                    score += 3 + (run_len - 5)
                run_val = matrix[r][c]
                run_len = 1
        if run_len >= 5:
            score += 3 + (run_len - 5)

    # N2: 2x2 blocks of identical color
    for r in range(size - 1):
        row0 = matrix[r]
        row1 = matrix[r + 1]
        for c in range(size - 1):
            v = row0[c]
            if row0[c + 1] == v and row1[c] == v and row1[c + 1] == v:
                score += 3

    # N3: finder-like 1:1:3:1:1 patterns with 4 light modules on either side
    pat1 = [1, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0]
    pat2 = [0, 0, 0, 0, 1, 0, 1, 1, 1, 0, 1]
    for r in range(size):
        row = matrix[r]
        for c in range(size - 10):
            seg = row[c : c + 11]
            if seg == pat1 or seg == pat2:
                score += 40
    for c in range(size):
        col = [matrix[r][c] for r in range(size)]
        for r in range(size - 10):
            seg = col[r : r + 11]
            if seg == pat1 or seg == pat2:
                score += 40

    # N4: dark module balance
    dark = sum(sum(row) for row in matrix)
    total = size * size
    k = abs(dark * 20 - total * 10) // total
    score += k * 10
    return score


def build_qr_matrix(text: str) -> list[list[int]]:
    """Encode `text` as an ISO/IEC 18004 QR module matrix (`1` = dark, `0` = light)."""
    payload = text.encode("utf-8")
    version, align_centers, ec_per_block, groups = _select_version(len(payload))
    size = 17 + 4 * version
    total_data_cw = sum(nb * dcw for nb, dcw in groups)
    data_cw = _encode_data_codewords(payload, version, total_data_cw)
    all_cw = _interleave_blocks(data_cw, ec_per_block, groups)

    bit_stream: list[int] = []
    for cw in all_cw:
        for i in range(7, -1, -1):
            bit_stream.append((cw >> i) & 1)

    base_matrix = [[0] * size for _ in range(size)]
    reserved = [[False] * size for _ in range(size)]

    _place_finder(base_matrix, reserved, 0, 0)
    _place_finder(base_matrix, reserved, 0, size - 7)
    _place_finder(base_matrix, reserved, size - 7, 0)

    if align_centers:
        last_idx = len(align_centers) - 1
        for ir, cr in enumerate(align_centers):
            for ic, cc in enumerate(align_centers):
                if (ir == 0 and ic == 0) or (ir == 0 and ic == last_idx) or (
                    ir == last_idx and ic == 0
                ):
                    continue
                _place_alignment(base_matrix, reserved, cr, cc)

    # Timing patterns on row 6 and col 6
    for i in range(8, size - 8):
        bit = 1 if (i % 2 == 0) else 0
        if not reserved[6][i]:
            base_matrix[6][i] = bit
            reserved[6][i] = True
        if not reserved[i][6]:
            base_matrix[i][6] = bit
            reserved[i][6] = True

    # Reserve format & version areas before zigzag data placement
    _write_format_bits(base_matrix, reserved, 0)
    _write_version_bits(base_matrix, reserved, version)

    # Place data + EC bits in upward/downward 2-column zigzag from right to left
    bit_idx = 0
    upward = True
    col = size - 1
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for r in rows:
            for dc in (0, 1):
                c = col - dc
                if not reserved[r][c]:
                    base_matrix[r][c] = bit_stream[bit_idx] if bit_idx < len(bit_stream) else 0
                    bit_idx += 1
        upward = not upward
        col -= 2

    best_matrix: list[list[int]] | None = None
    best_penalty: int | None = None
    for mask_id in range(8):
        cand = [row[:] for row in base_matrix]
        for r in range(size):
            for c in range(size):
                if not reserved[r][c] and _mask_condition(mask_id, r, c):
                    cand[r][c] ^= 1
        _write_format_bits(cand, [row[:] for row in reserved], mask_id)
        pen = _penalty_score(cand)
        if best_penalty is None or pen < best_penalty:
            best_penalty = pen
            best_matrix = cand

    assert best_matrix is not None
    return best_matrix


def generate_qr_svg(
    text: str,
    *,
    border: int = 4,
    dark_color: str = "#003747",
    light_color: str = "#FFFFFF",
    label: str = "Telegram QR Pairing Code",
) -> str:
    """Render an ISO/IEC 18004 scannable `<svg>` QR code with crisp 4-module quiet zone."""
    matrix = build_qr_matrix(text)
    size = len(matrix)
    total = size + 2 * border
    safe_label = html.escape(label, quote=True)
    safe_dark = html.escape(dark_color, quote=True)
    safe_light = html.escape(light_color, quote=True)

    subpaths: list[str] = []
    for r, row in enumerate(matrix):
        y = r + border
        for c, val in enumerate(row):
            if val:
                x = c + border
                subpaths.append(f"M{x},{y}h1v1h-1z")

    path_d = "".join(subpaths)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {total} {total}" '
        f'width="240" height="240" shape-rendering="crispEdges" role="img" '
        f'aria-label="{safe_label}">'
        f'<rect width="{total}" height="{total}" fill="{safe_light}"/>'
        f'<path d="{path_d}" fill="{safe_dark}"/>'
        f"</svg>"
    )
