# So sánh trước / sau khi sửa (2026-10-02)

Chạy trên đủ 11 database BIRD dev (1.534 câu). Bài 2 dùng **cùng một bộ câu lỗi** cho cả hai lần (seed 42, 300 câu mỗi loại), nên so sánh theo từng cặp.

## Đã sửa gì

1. **Hiểu tên tắt của bảng** (`T1`, `T2`...): bản vá đổi tên cột và bản vá thêm JOIN giờ dùng đúng tên tắt đang có trong câu.
2. **Kiểm tra nối bảng theo từng SELECT**: bảng trong subquery không bị gộp với bảng bên ngoài; chỉ những điều kiện thật sự nối 2 bảng mới được tính.
3. Kèm theo (để không sinh lỗi mới): nhận ra tên tự đặt trong câu (`COUNT(*) AS post_count`, cột của CTE / bảng con); sửa lỗi đảo cột khi đi ngược khóa ngoại trong đồ thị schema.

## Bài 1 — gold SQL (lý tưởng: để nguyên tất cả)

| | Trước | Sau |
|---|---:|---:|
| Để nguyên (UNCHANGED) | 1.488 (97,0%) | **1.532 (99,9%)** |
| Báo nhầm, bỏ cuộc (ABSTAIN) | 44 | 1 (câu chạy quá 5 giây) |
| Có sửa (REPAIRED) | 2 | 1 |
| Sửa làm sai kết quả so với gold | 1 | 1* |

\* Câu 646: gold SQL của BIRD **bị lỗi sẵn** (`ON T2.PostId = T2.Id`). Hệ thống sửa thành `T1.Id = T2.PostId`, tức là sửa đúng, nhưng vì khác kết quả của gold (bị lỗi) nên bị tính là sai.

## Bài 2 — câu bị làm hỏng có kiểm soát

| Loại lỗi | Sửa đúng: trước → sau | Sửa sai | Bỏ cuộc | Bỏ sót (để nguyên câu hỏng) |
|---|---|---:|---:|---:|
| Gõ sai tên cột | 64 → **292** /300 | 0 → 2 | 236 → 6 | 0 → 0 |
| Gõ sai tên bảng | 3 → 3 /300 | 0 → 0 | 297 → 297 | 0 → 0 |
| Bỏ điều kiện JOIN | 0 → **265** /300 | 0 → 9 | 247 → 22 | 53 → 4 |
| **Tổng** | **67 (7,4%) → 560 (62,2%)** | 0 → 11 | 780 → 325 | 53 → 4 |

## Còn lại cần làm

- **Tên bảng gõ sai**: chưa có gợi ý tên bảng gần giống → gần như không sửa được (chưa nằm trong đợt sửa này).
- **9 ca sửa JOIN sai**: hầu hết vì giữa 2 bảng có **nhiều khóa ngoại** (superhero → colour qua eye/hair/skin; Player → Match qua away_player_1..11; posts → users qua OwnerUserId / LastEditorUserId). Hệ thống đang lấy khóa ngoại đầu tiên. Kế hoạch đã cảnh báo: khi có nhiều đường nối gần như nhau thì nên giữ nhiều ứng viên hoặc từ chối sửa.
- **2 ca sửa tên cột sai**: tên rất ngắn, có nhiều ứng viên giống nhau (`ABL` → `ALP` thay vì `ALB`; `A1` → `A10` thay vì `A11`). Nên từ chối sửa khi 2 ứng viên đầu có độ giống gần bằng nhau.
- Chỉ số "AST edit size" chưa đo được việc đổi tên (chỉ đếm thay đổi loại node), nên ca sửa tên cột hiện ra ~0.

Thư mục `_cu_9db_cloud/` là lần chạy đầu tiên (9 database, trong cloud), giữ lại để tham khảo, có thể xoá.

---

# Đợt 2 — Nhóm 1 (2026-10-05) → `results/after_nhom1/`

## Đã sửa gì

1. **Từ chối đoán khi có nhiều cách nối bảng**: nếu giữa 2 bảng có nhiều khóa ngoại (VD superhero → colour qua mắt / tóc / da), hệ thống **chạy thử tất cả các cách**. Cùng kết quả → sửa (VD `player_api_id` / `player_fifa_api_id` cùng chỉ 1 cầu thủ). Khác kết quả → từ chối sửa và ghi lại các phương án.
2. **So tên cột tốt hơn**: tính cả lỗi đảo 2 chữ cái (`ABL` → `ALB`, không phải `ALP`). Nếu 2 ứng viên gần như nhau (`A1` → `A10` hay `A11`?) thì từ chối sửa.
3. **Sửa được tên bảng gõ sai**: gợi ý tên bảng gần nhất, giữ nguyên tên tắt (`lpTimes AS T1` → `lapTimes AS T1`) và sửa cả chỗ dùng tên bảng làm tiền tố.

## Kết quả (cùng bộ 900 câu lỗi như 2 lần trước)

| Loại lỗi | Ban đầu | Đợt 1 | **Đợt 2** |
|---|---:|---:|---:|
| Gõ sai tên cột — sửa đúng | 64 | 292 | **292** |
| Gõ sai tên bảng — sửa đúng | 3 | 3 | **300** |
| Bỏ JOIN — sửa đúng | 0 | 265 | **244** |
| **Tổng sửa đúng** | 67 (7,4%) | 560 (62,2%) | **836 (92,9%)** |
| **Sửa sai** | 0 | 11 | **0** |
| Bỏ sót | 53 | 4 | 4 |

Bài 1 (gold SQL): 1.533 / 1.534 để nguyên; câu còn lại là câu 646 (gold của BIRD bị lỗi sẵn, hệ thống sửa đúng).

Bỏ JOIN giảm 265 → 244 là **đánh đổi có chủ ý**: 21 câu trước đây "đoán trúng" khóa ngoại đầu tiên, giờ hệ thống thấy các cách nối cho kết quả khác nhau nên từ chối. Đổi lại không còn ca nào sửa sai.

## 52 câu bỏ JOIN còn từ chối sửa

- 27: các cách nối cho kết quả khác nhau (cần thêm thông tin từ câu hỏi → việc của Gate F / LLM).
- 21: hệ thống không phát hiện được chỗ thiếu nối (VD nối qua bảng con, điều kiện nối phức tạp trong WHERE).
- 4: quá nhiều phương án để thử trong ngân sách (tối đa 5 ứng viên).
