"""Lõi quy hoạch động cho dóng hàng đơn điệu (không đảo thứ tự), dùng chung cho mọi aligner.

Aligner chỉ cần cung cấp `cost(i0, i1, j0, j1)` = chi phí gom nguồn[i0:i1] với đích[j0:j1]
(i1-i0 hoặc j1-j0 có thể bằng 0 = bỏ qua một phía). Lõi này lo phần tìm đường đi tối ưu.
"""

from __future__ import annotations

from typing import Callable, Sequence

Move = tuple[int, int]          # (số câu nguồn, số câu đích) tiêu thụ trong một bước
Span = tuple[int, int, int, int]  # (i0, i1, j0, j1)

DEFAULT_MOVES: tuple[Move, ...] = ((1, 1), (1, 2), (2, 1), (1, 3), (3, 1), (2, 2), (1, 0), (0, 1))


def monotonic_align(n: int, m: int, cost: Callable[[int, int, int, int], float],
                    moves: Sequence[Move] = DEFAULT_MOVES, band: int | None = 30) -> list[Span]:
    """Tìm chuỗi bước có tổng chi phí nhỏ nhất đi từ (0,0) đến (n,m).

    band: chỉ xét ô (i,j) cách đường chéo kỳ vọng j≈i*m/n không quá `band` câu.
          None = không giới hạn (chậm với sách dài). Dải được nới ra nếu n, m lệch nhau nhiều.
    """
    if n == 0 and m == 0:
        return []
    ratio = m / n if n else 0.0
    w = None if band is None else band + abs(m - n) // 2

    def window(i: int) -> tuple[int, int]:
        if w is None:
            return 0, m
        centre = round(i * ratio)
        return max(0, centre - w), min(m, centre + w)

    INF = float("inf")
    best: list[dict[int, float]] = [dict() for _ in range(n + 1)]
    back: list[dict[int, Move]] = [dict() for _ in range(n + 1)]
    best[0][0] = 0.0
    for i in range(n + 1):
        lo, hi = window(i)
        for j in range(lo, hi + 1):
            base = best[i].get(j)
            if base is None:
                continue
            for a, b in moves:
                ni, nj = i + a, j + b
                if ni > n or nj > m:
                    continue
                nlo, nhi = window(ni)
                if not nlo <= nj <= nhi:
                    continue
                c = base + cost(i, ni, j, nj)
                if c < best[ni].get(nj, INF):
                    best[ni][nj] = c
                    back[ni][nj] = (a, b)
    if m not in best[n]:
        raise ValueError("Không tìm được đường dóng hàng; tăng --band hoặc kiểm tra dữ liệu hai lớp")
    spans: list[Span] = []
    i, j = n, m
    while (i, j) != (0, 0):
        a, b = back[i][j]
        spans.append((i - a, i, j - b, j))
        i, j = i - a, j - b
    spans.reverse()
    return spans
