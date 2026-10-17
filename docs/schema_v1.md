# Schema đồ thị tri thức — v1 (bản nháp)

> Trạng thái: **khung — chủ dự án điền phần 2**. Phần 1 đã cố định theo `docs/PLAN.md` và parser.
> Dùng ở: T3 (nạp lớp cấu trúc vào Neo4j; LLM gợi ý sửa schema, chủ dự án duyệt), T4 (trích thực thể–quan hệ theo schema).
> Quy ước tên: nhãn nút `PascalCase` không dấu (`VanBan`), tên cạnh `UPPER_SNAKE` không dấu (`DAN_CHIEU`),
> thuộc tính `snake_case`.

## 1. Lớp cấu trúc (quy tắc, không dùng LLM) — đã có

Nguồn: `data/processed/qc3626.json` (sinh bởi `src/ingest/parse_regulation.py`).

### Nút

| Nhãn | Mã (`id`) | Thuộc tính | Số lượng (QĐ 3626) |
| --- | --- | --- | --- |
| `VanBan` | `qc3626` | `title`, `issued_by`, `source_file` | 1 |
| `Chuong` | `qc3626.c{N}` | `number`, `roman`, `title`, `page_start`, `page_end`, `text` | 9 |
| `Dieu` | `qc3626.d{N}` | `number`, `title`, `n_khoan`, `page_start`, `page_end`, `own_text`, `text` | 51 |
| `Khoan` | `qc3626.d{N}.k{M}` | `number`, `page_start`, `page_end`, `own_text`, `text`, `overrides` (nếu sửa tay) | 179 |
| `Diem` | `qc3626.d{N}.k{M}.{x}` | như `Khoan` | 262 |

### Cạnh

| Cạnh | Từ → Đến | Ý nghĩa | Cách tạo |
| --- | --- | --- | --- |
| `THUOC` | `Chuong`→`VanBan`, `Dieu`→`Chuong`, `Khoan`→`Dieu`, `Diem`→`Khoan` | quan hệ cha–con | trường `parent_id` |
| `DAN_CHIEU` | `Dieu`/`Khoan`/`Diem` → `Dieu`/`Khoan`/`Diem` | "theo quy định tại khoản 1, Điều 21…" | regex (T3) |
| `SUA_DOI` | `VanBan`/`Dieu`/`Khoan` → cùng loại | văn bản sau sửa đổi văn bản trước | khi có văn bản sửa đổi (chưa có) |

Dẫn chiếu đã thấy trong QĐ 3626 (đếm sơ bộ bằng regex, khoảng 20 chỗ):

| Dạng | Ví dụ thật | Đích |
| --- | --- | --- |
| `khoản M, Điều N` | `qc3626.d7.k2`: "khoản 2, Điều 6" | `qc3626.d6.k2` |
| `điểm x, khoản M, Điều N` (có thể nhiều điểm) | `qc3626.d7.k1`: "điểm a, b, c, d, đ, e, g, khoản 2, Điều 6" | 7 nút `qc3626.d6.k2.{a…g}` |
| `Điều N` đứng riêng | `qc3626.d7.k2`: "Điều 10 của Quy chế này" | `qc3626.d10` |
| `khoản M, Điều này` / `Điều này` | `qc3626.d21.k6`: "khoản 1, Điều này" | khoản trong **chính điều đó** (`qc3626.d21.k1`) |
| Văn bản ngoài | `qc3626.d10.k1`: "Luật Giáo dục đại học…" | ngoài phạm vi — xem câu hỏi mở Q4 |

## 2. Lớp ngữ nghĩa (LLM trích theo schema) — **chủ dự án điền**

Nguyên tắc đã chốt trong PLAN mục 3:
- Mỗi thực thể / quan hệ ngữ nghĩa giữ liên kết về `Khoan` (hoặc `Diem`/`Dieu`) nguồn → cạnh `NGUON` bên dưới.
- Có bước gộp thực thể trùng (ví dụ "SV", "sinh viên", "người học" → một nút).

### 2.1. Loại thực thể

Năm nhóm dưới đây lấy từ PLAN mục 3. Bạn có thể đổi tên, gộp, tách hoặc thêm dòng.

| Nhãn | Mô tả (thế nào thì thuộc loại này) | Thuộc tính | Ví dụ thật (ghi mã khoản) |
| --- | --- | --- | --- |
| `KhaiNiem` | | | |
| `DoiTuong` | | | |
| `DieuKien` | | | |
| `Nguong` | | | |
| `HeQua` | | | |
| | | | |
| | | | |

### 2.2. Loại quan hệ

| Cạnh | Từ → Đến | Ý nghĩa | Ví dụ thật (ghi mã khoản) |
| --- | --- | --- | --- |
| `NGUON` | mọi thực thể ngữ nghĩa → `Khoan`/`Diem`/`Dieu` | thực thể được nêu ở đâu (**bắt buộc**, theo PLAN) | — |
| | | | |
| | | | |
| | | | |
| | | | |

### 2.3. Ví dụ minh họa cách đọc một khoản thành đồ thị

Chỉ để tham khảo cách nghĩ — tên nhãn/cạnh dưới đây **chưa phải lựa chọn**, bạn thay bằng tên trong 2.1–2.2.

`qc3626.d17.k2` (tr. 15): chương trình đào tạo chuẩn, số tín chỉ tích lũy dưới 35 → năm thứ nhất.

```
(KhaiNiem "Năm thứ nhất") -[CO_DIEU_KIEN]-> (Nguong "số tín chỉ tích lũy < 35")
(Nguong "số tín chỉ tích lũy < 35") -[AP_DUNG_CHO]-> (DoiTuong "CTĐT chuẩn")
mọi nút trên -[NGUON]-> (Khoan qc3626.d17.k2)
```

### 2.4. Câu hỏi để thử schema

Với mỗi câu, ghi đồ thị cần những nút/cạnh nào (theo 2.1–2.2) để trả lời và trích dẫn đúng.
Nếu không diễn tả được → schema còn thiếu. Thêm câu của bạn vào cuối bảng.

| # | Loại | Câu hỏi | Căn cứ | Đồ thị cần gì? |
| --- | --- | --- | --- | --- |
| 1 | single | Điểm chữ C+ được quy đổi thành bao nhiêu điểm số? | `qc3626.d43.k1` | |
| 2 | conditional | Sinh viên chương trình chất lượng cao đã tích lũy 50 tín chỉ thì là sinh viên năm thứ mấy? | `qc3626.d17.k2` | |
| 3 | multi | Điểm trung bình chung tích lũy được dùng vào những việc gì, và được tính thế nào? | `qc3626.d43.k2` | |
| 4 | multi | Để được xét tốt nghiệp, sinh viên cần những điều kiện gì? | `qc3626.d46` | |
| 5 | conditional | Khi nào sinh viên bị xử lý học vụ, và hệ quả là gì? | `qc3626.d44` | |
| 6 | out_of_scope | Học phí ngành Khoa học máy tính năm 2026 là bao nhiêu tiền? | không có trong quy chế | |
| | | | | |

## 3. Câu hỏi mở — chủ dự án quyết

| # | Câu hỏi | Quyết định |
| --- | --- | --- |
| Q1 | `Diem` là nút riêng, hay gộp vào nội dung `Khoan`? (ảnh hưởng mã trích dẫn và độ mịn của vector index) | |
| Q2 | Ngưỡng (35 tín chỉ, 2,00 điểm…) là **nút riêng** `Nguong`, hay **thuộc tính** trên cạnh/nút điều kiện? | |
| Q3 | Các loại chương trình (chuẩn, chất lượng cao, tài năng, song ngành…) là thực thể, hay chỉ là thuộc tính? | |
| Q4 | Văn bản ngoài được dẫn chiếu (Luật Giáo dục đại học, Thông tư của Bộ…) có thành nút `VanBan` "rỗng" không? | |
| Q5 | Cạnh `DAN_CHIEU` trỏ tới mức nào khi văn bản chỉ nói "Điều N" — nút `Dieu`, hay tất cả `Khoan` của điều đó? | |
| Q6 | Gộp thực thể trùng: dựa vào danh sách từ đồng nghĩa viết tay, embedding, hay LLM? | |

## Lịch sử

| Ngày | Phiên bản | Thay đổi |
| --- | --- | --- |
| 17/10/2026 | v1 khung | Tạo khung; điền lớp cấu trúc theo PLAN + parser. Lớp ngữ nghĩa chờ chủ dự án. |
