"""
So khớp tên cho bảng / cột bị gõ sai.

Xếp hạng dựa trên hai tín hiệu:
  1. khoảng cách sửa có tính cả đảo hai chữ cái liền nhau (Damerau / OSA): "ABL" -> "ALB" tốn 1
     (một lần đảo), còn "ABL" -> "ALP" tốn 2. Tỉ lệ giống thông thường coi hai trường hợp này như nhau.
  2. độ giống SequenceMatcher để phân xử khi bằng nhau và làm phương án dự phòng cho tên dài.

Ứng viên tốt nhất chỉ được chấp nhận khi *rõ ràng* tốt hơn ứng viên thứ hai;
nếu không thì báo là mơ hồ để lớp sửa lỗi từ chối thay vì đoán
(VD "A1" cách cả "A10" lẫn "A11" đúng một lần sửa).
"""

from difflib import SequenceMatcher
from typing import Iterable, List, Optional, Tuple, TypeVar

T = TypeVar("T")


def normalize(name: str) -> str:
    return name.lower().replace("_", "").replace(" ", "")


def osa_distance(a: str, b: str) -> int:
    """Khoảng cách OSA (thêm / xoá / thay / đảo hai ký tự liền nhau)."""
    la, lb = len(a), len(b)
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[la][lb]


class Match:
    __slots__ = ("item", "name", "distance", "similarity")

    def __init__(self, item, name: str, distance: int, similarity: float):
        self.item, self.name, self.distance, self.similarity = item, name, distance, similarity

    def __repr__(self):
        return f"Match({self.name!r}, d={self.distance}, s={self.similarity:.2f})"


def rank(query: str, items: Iterable[Tuple[T, str]]) -> List[Match]:
    """Xếp hạng các cặp (item, tên) theo độ gần với chuỗi cần tìm: khoảng cách nhỏ trước, rồi độ giống cao."""
    q = normalize(query)
    out = []
    for item, name in items:
        n = normalize(name)
        out.append(Match(item, name, osa_distance(q, n), SequenceMatcher(None, q, n).ratio()))
    out.sort(key=lambda m: (m.distance, -m.similarity))
    return out


def max_edits(query: str) -> int:
    return max(2, len(normalize(query)) // 4)


def acceptable(m: Match, query: str) -> bool:
    """Một lỗi gõ hợp lý (tối đa ~2 lần sửa, tăng theo độ dài tên) hoặc một tên giống rõ rệt."""
    return m.distance <= max_edits(query) or m.similarity >= 0.6


def pick(query: str, items: Iterable[Tuple[T, str]], sim_margin: float = 0.05) -> Tuple[Optional[Match], bool, List[Match]]:
    """
    Trả về (khớp_tốt_nhất_hoặc_None, có_mơ_hồ_không, danh_sách_xếp_hạng).
    is_ambiguous=True khi ứng viên thứ hai gần y như vậy (cùng khoảng cách và độ giống chênh dưới ngưỡng).
    """
    all_ranked = rank(query, items)
    ranked = [m for m in all_ranked if acceptable(m, query)]
    if not ranked:
        return None, False, []
    best = ranked[0]
    second = all_ranked[1] if len(all_ranked) > 1 else None
    if second is not None:
        if best.distance <= max_edits(query):
            # khớp kiểu gõ sai: chỉ mơ hồ khi ứng viên thứ hai cũng cách đúng bấy nhiêu lần sửa
            if second.distance == best.distance and best.similarity - second.similarity < sim_margin:
                return None, True, ranked
        else:
            # khớp lỏng hơn theo độ giống: đòi hỏi cách biệt rõ với ứng viên thứ hai
            by_sim = sorted(all_ranked, key=lambda m: -m.similarity)
            if by_sim[0] is not best or best.similarity - by_sim[1].similarity < 0.1:
                return None, True, ranked
    return best, False, ranked
