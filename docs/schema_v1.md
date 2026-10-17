# Schema đồ thị tri thức — v1 (bản sửa sau khi đối chiếu toàn văn)

> **Trạng thái:** đề xuất, chờ chủ dự án duyệt. Cột "Quyết định" ở mục 4 để trống.
> **Đối chiếu với:** `data/processed/qc3626.json` (commit `7ef08ad`) và PDF gốc 52 trang. Đã đọc đủ 51 điều. Mọi mã đơn vị nêu trong file này đã được script kiểm là có trong `qc3626.json` (trừ `d1.k22` ở mục 1.3, cố ý nêu làm ví dụ đích sai). Các đoạn trích nguyên văn cũng đã được so lại với `qc3626.json`.
> **Dùng ở:** T3 (nạp lớp cấu trúc), T4 (LLM trích lớp ngữ nghĩa theo schema).
> **Quy ước tên:** nhãn nút `PascalCase` không dấu, cạnh `UPPER_SNAKE` không dấu, thuộc tính `snake_case`. Thuộc tính lớp cấu trúc giữ tên tiếng Anh của parser (`number`, `title`, `page_start`…).

## 0. Đối chiếu cho thấy gì

| # | Phát hiện | Bằng chứng | Hệ quả cho schema |
| --- | --- | --- | --- |
| F1 | Ngưỡng thường là **khoảng**, không phải một số | 6 bảng phân loại: năm đào tạo `d17.k2`, điểm chữ `d41.k2`, thang 4 `d43.k1`, học lực `d45.k2`, hạng tốt nghiệp `d47.k5`, điểm thưởng `d32.k2` | `DieuKien` lưu `min`/`max` có cờ bao gồm (Q2) |
| F2 | Ngưỡng **đổi theo loại chương trình** | `d13.k3` (ngoại ngữ bậc 3/4), `d14.k2` (tín chỉ tối thiểu), `d17.k2` (3 cột chuẩn/CLC/tài năng), `d46.k1.d` (2,00/2,50) | Loại chương trình là thực thể (Q3) |
| F3 | Điều kiện có cả **AND, OR và lồng nhau** | AND: `d46.k1` ("có đủ các điều kiện sau"), `d28.k1` ("đáp ứng đầy đủ các điều kiện sau"); OR: `d27.k3.a`, `d44.k2` ("một trong các trường hợp"), `d40.k2` ("một trong các lỗi"), và OR ngầm không có dấu hiệu ở `d24.k1`, `d44.k1`; lồng: `d26.k1.c` = (TBC ≥ 2,5 và …) **hoặc** (TBC ≥ 2,0 và …) | Thêm nút `NhomDieuKien` (Q7). Bản trước đề xuất "mặc định AND" là **sai** |
| F4 | Điều kiện **phủ định một trạng thái** rất phổ biến | "chưa thuộc diện bị buộc thôi học" `d23.k1.c`, `d45.k2`; "không bị cảnh báo học tập" `d28.k1.d`; "không thuộc các trường hợp bị xem xét buộc thôi học" `d36.k3.d` | `DieuKien` trỏ được tới `HeQua`, có `phu_dinh` |
| F5 | **Giao quyền** cho đơn vị đào tạo: khoảng 40 đơn vị nội dung; "thủ trưởng đơn vị đào tạo" xuất hiện 49 lần | `d44.k1.c`: thủ trưởng chọn 1–2 trong 3 điều kiện cảnh báo; `d44.k2.a`: số lần cảnh báo do thủ trưởng quy định; `d21.k1`: học kỳ phụ | Thêm cạnh `GIAO_QUY_DINH`. Câu trả lời đúng là "QĐ 3626 giao cho UET quy định", không từ chối, không đoán |
| F6 | **Loại trừ khi tính đại lượng** nằm rải ở nhiều điều | Không tính vào TBC: `d5.k2.g` (học phần điều kiện), `d21.k2.d` (tự chọn tự do), `d43.k2` (GDQP-AN, GDTC); `d32.k1`: điểm thưởng NCKH cộng vào TBC để xét học bổng nhưng **không** dùng xếp loại học lực, hạng tốt nghiệp | Thêm cạnh `TINH_VAO {co, boi_canh}` và `DUNG_DE {co}`. Đây là chỗ cần nhiều bước thật sự |
| F7 | Quy định **phân biệt trường thành viên và đơn vị trực thuộc** | 10 đơn vị nội dung, ví dụ `d28.k1.e` (ai duyệt chuyển ngành), `d47.k2` (ai ký quyết định tốt nghiệp), `d39.k1` | Cần tri thức nền "UET là trường đại học thành viên" (Q8) |
| F8 | Có đơn vị nội dung **không nằm trong khoản nào** | 4 điều không có khoản (`d16`, `d19`, `d49`, `d51`); phần dẫn có nội dung thật ở `d22`, `d40`, `d44` | Index vector chỉ trên `Khoan` sẽ **không bao giờ tìm thấy** các đoạn này (S2) |
| F9 | Parser gắn **đoạn không đánh số** (sau hoặc **xen giữa** danh sách điểm) vào điểm đứng ngay trước nó | Đã xác nhận trên PDF 8 chỗ, ví dụ `d44.k1.c` chứa câu giao quyền áp dụng cho cả khoản 1; `d35.k4.d` (thi hộ) chứa nghĩa vụ của sinh viên CLC; `d44.k2.d`: câu "Chậm nhất là 1 tháng…" nằm **giữa** điểm d) và đ) (ảnh tr. 47) | Trích dẫn sẽ ghi sai điểm (S1). Trường `doan_khong_so` phải giữ vị trí gốc |
| F10 | PDF chỉ có **Quy chế**, không có trang Quyết định | Trang 1 bắt đầu bằng "QUY CHẾ ĐÀO TẠO ĐẠI HỌC…(Ban hành kèm theo…)". Thông tư 08/2021 **không được nhắc tên** ở đâu trong 51 điều | Cạnh `CAN_CU` của QĐ 3626 phải nhập tay từ trang Quyết định (S5) |
| F11 | Quy chế chỉ áp dụng **từ khóa tuyển sinh 2022** | `d51` | `VanBan.ap_dung_tu_khoa = 2022`. Câu hỏi của khóa trước 2022 nằm ngoài phạm vi |

## 1. Lớp cấu trúc (quy tắc, không dùng LLM)

### 1.1. Nút

| Nhãn | Mã | Thuộc tính | Số lượng |
| --- | --- | --- | --- |
| `VanBan` | `qc3626` | `title`, `issued_by`, `source_file`, **`so_hieu`** (`3626/QĐ-ĐHQGHN`), **`ngay_ban_hanh`** (`2022-10-21`), **`cap_ban_hanh`** (`bo` \| `dhqghn` \| `truong`), **`ap_dung_tu_khoa`** (`2022`), **`trong_pham_vi`** (`true` nếu đã tách nội dung) | 1, cộng các văn bản ngoài ở S5 |
| `Chuong` | `qc3626.c{N}` | như parser | 9 |
| `Dieu` | `qc3626.d{N}` | như parser; `own_text` là **phần dẫn** (câu trước khoản 1); **`doan_khong_so`** (nếu S1 = B) | 51 |
| `Khoan` | `qc3626.d{N}.k{M}` | như parser; **`doan_khong_so`** (nếu S1 = B) | 179 |
| `Diem` | `qc3626.d{N}.k{M}.{x}` | như parser | 262 |

**`doan_khong_so: [{sau, text}]`** — đoạn văn không đánh số nằm sau hoặc xen giữa các đơn vị con (mục 1.4):
- `sau`: mã đơn vị con đứng ngay trước đoạn trong văn bản gốc; `text`: nguyên văn đoạn.
- `text` của đơn vị cha dựng lại **theo thứ tự gốc**: `own_text`, rồi lần lượt từng đơn vị con, mỗi đoạn chèn ngay sau đơn vị `sau` của nó.
- Ví dụ xen giữa: `d44.k2.doan_khong_so = [{sau: "qc3626.d44.k2.d", text: "Chậm nhất là 1 tháng…"}]`, nên `d44.k2.text` = … d) → đoạn này → đ).
- Ví dụ mức điều: `d35.doan_khong_so = [{sau: "qc3626.d35.k4", text: "Ngoài những nghĩa vụ chung…"}]` (đoạn cuối điều, sau khoản cuối).

**Nhãn phụ `:DonViNoiDung`** (nếu S2 = B) gắn cho mọi `Khoan`, và cho `Dieu` có phần dẫn (12 điều, trong đó 4 điều không có khoản). Vector index đặt trên nhãn này, tổng 191 đơn vị. Nếu S1 = B thì thêm `d35` (có `doan_khong_so` mức điều, không nằm trong khoản nào): **13 điều, 192 đơn vị**. Nội dung đem embedding của một `Dieu` là phần dẫn cộng `doan_khong_so`, **không** gồm các khoản (tránh trùng với `Khoan`).

Văn bản dài: khoản dài nhất là `d14.k2` (2.769 ký tự). Trung vị 345 ký tự, nên không cần cắt nhỏ khoản.

### 1.2. Cạnh

| Cạnh | Từ → Đến | Ý nghĩa | Cách tạo |
| --- | --- | --- | --- |
| `THUOC` | `Chuong`→`VanBan`, `Dieu`→`Chuong`, `Khoan`→`Dieu`, `Diem`→`Khoan` | cha–con | `parent_id` |
| `DAN_CHIEU` | `Dieu`/`Khoan`/`Diem` → `Dieu`/`Khoan`/`Diem`/`VanBan` | dẫn chiếu trong văn bản | regex theo 1.3, **đích phải tồn tại** |
| `SUA_DOI` | `VanBan`/`Dieu`/`Khoan` → cùng loại | văn bản sau sửa đổi văn bản trước | khi có văn bản sửa đổi (hiện chưa có) |
| `CAN_CU` | `VanBan` → `VanBan` | ban hành căn cứ vào văn bản kia | **nhập tay** từ trang Quyết định (F10) |
| `CU_THE_HOA` *(để sau)* | `VanBan` cấp trường → `Dieu`/`Khoan` của QĐ 3626 | quy định của UET chi tiết hóa nội dung được giao quyền | khi thêm văn bản UET |

### 1.3. Dẫn chiếu thật trong QĐ 3626

Quét toàn văn bằng regex được **21 cụm dẫn chiếu nội bộ và 1 cụm ra ngoài**. Danh sách đủ 22 cụm, kèm đích mong đợi, ở **Phụ lục A** (dữ liệu test cho regex `DAN_CHIEU`).

| Dạng | Ví dụ thật | Đích |
| --- | --- | --- |
| `khoản M, Điều N của Quy chế này` | `d23.k3.c`: "khoản 1, Điều 21" | `d21.k1` |
| nhiều điểm | `d7.k1`: "điểm a, b, c, d, đ, e, g, khoản 2, Điều 6" | 7 nút `d6.k2.{a…g}` |
| nhiều điểm, **không có dấu cách / dấu phẩy** | `d36.k3.d`: "điểm a, b,c khoản 3, Điều này" | `d36.k3.{a,b,c}` |
| nhiều khoản | `d50.k3.đ`: "khoản 2, 3, Điều 10"; `d10.k1`: "khoản 2, 3, Điều này" | 2 nút `Khoan` |
| **không có dấu phẩy** giữa khoản và Điều | `d29.k3`: "khoản 2 Điều này" | `d29.k2` |
| `Điều này` (tự tham chiếu) | `d21.k6`, `d38.k8`, `d41.k4` | khoản trong chính điều đó |
| `Điều N` đứng riêng | `d7.k2` "Điều 10"; `d15.k1.a` "Điều 11"; `d29.k3.b` "Điều 22", "Điều 24"; `d46.k2` "Điều 22" | nút `Dieu` (S4) |
| **Bẫy: dẫn chiếu ra ngoài cùng dạng** | `d10.k1`: "khoản 22, Điều 1 Luật sửa đổi, bổ sung một số điều của Luật Giáo dục đại học" | **không** phải `d1.k22`. Regex chỉ nhận khi theo sau là "của Quy chế này", "Điều này", hoặc không có tên văn bản khác; kiểm đích tồn tại |

Ghi chú: `d46.k2` dẫn chiếu "Điều 22" cho việc quy đổi điểm sinh viên CLC, nhưng quy tắc quy đổi cụ thể ("Các điểm từ 4 đến 9 được tăng lên 1 điểm") nằm ở `d38.k9`. `DAN_CHIEU` giữ đúng như văn bản ghi. Nối `d46.k2` với `d38.k9` là việc của lớp ngữ nghĩa.

### 1.4. Lỗi cấu trúc cần sửa ở parser (xem S1)

Có 8 chỗ đã xác nhận trên PDF là đoạn văn riêng (xuống dòng, thụt lề) không đánh số, nằm sau hoặc xen giữa danh sách điểm. Theo nội dung, các đoạn này áp dụng cho cả khoản (hoặc cả điều), nhưng parser gắn vào điểm đứng ngay trước. Cột "Chuyển vào" là đánh giá của Claude, cần bạn xem lại. Cột `sau` và "Vị trí" đã kiểm bằng thứ tự trong `qc3626.json` (trùng thứ tự PDF).

| Đơn vị đang chứa | Đoạn bị gắn nhầm (đầu câu) | Chuyển vào `doan_khong_so` của | `sau` | Vị trí |
| --- | --- | --- | --- | --- |
| `d44.k1.c` | "Tùy theo đặc điểm của từng đơn vị đào tạo, thủ trưởng đơn vị quy định áp dụng một hoặc hai trong ba điều kiện…" | `d44.k1` | `d44.k1.c` | cuối danh sách |
| `d44.k2.d` | "Chậm nhất là 1 tháng sau khi sinh viên có quyết định buộc thôi học…" | `d44.k2` | `d44.k2.d` | **xen giữa** d) và đ) |
| `d35.k4.d` | "Ngoài những nghĩa vụ chung… sinh viên tài năng, chất lượng cao phải thực hiện các nghĩa vụ sau…" | `d35` (mức điều; khác chủ đề với khoản 4) | `d35.k4` | cuối điều |
| `d35.k4.c` | "Các hình thức kỷ luật nói trên do cán bộ coi thi lập biên bản…" | `d35.k4` | `d35.k4.c` | **xen giữa** c) và d) |
| `d36.k3.d` | "Sinh viên nghỉ học tạm thời nếu muốn học tiếp…"; "Các đối tượng ở điểm a, b,c khoản 3…" (2 đoạn) | `d36.k3` | `d36.k3.d` | cuối danh sách |
| `d41.k5.b` | "Trừ các trường hợp đặc biệt do thủ trưởng đơn vị đào tạo quy định…" | `d41.k5` | `d41.k5.b` | cuối danh sách |
| `d32.k2.b` | "Nếu công trình nghiên cứu khoa học do nhiều sinh viên…"; "Đối với các sinh viên có công bố quốc tế…" (2 đoạn) | `d32.k2` | `d32.k2.b` | cuối danh sách |
| `d23.k3.c` | "Sinh viên chỉ được phép không lên lớp đối với học phần xin rút bớt…" | `d23.k3` | `d23.k3.c` | cuối danh sách |

Vấn đề nhỏ khác, không ảnh hưởng schema:
- Tiêu đề khoản dính vào nội dung, ví dụ `d44.k1` = "1. Cảnh báo học vụ Đơn vị đào tạo thực hiện…". Có thể thêm `Khoan.title` sau, không gấp.
- Gạch đầu dòng ("-", "+") không tách thành nút. Ví dụ 7 điều kiện ở `d27.k4.a`. Trích dẫn dừng ở mức điểm là đủ.
- `d29.k3.b`: xuống dòng làm "giáo dục quốc phòng / - an ninh" trông như một gạch đầu dòng mới.

## 2. Lớp ngữ nghĩa (LLM trích theo schema)

### 2.0. Nguyên tắc

1. **Đồ thị dùng để tìm đúng tập khoản. Câu trả lời luôn trích nguyên văn khoản.** Nút ngữ nghĩa không cần chính xác tuyệt đối, chỉ cần đưa truy xuất tới đúng chỗ. Đây là lý do giữ schema vừa phải.
2. **Nguồn bắt buộc.** Mỗi nút ngữ nghĩa có cạnh `NGUON` tới `Khoan`/`Diem`/`Dieu`. Neo4j không cho cạnh nối tới cạnh khác, nên **mỗi cạnh ngữ nghĩa mang thuộc tính `nguon`** (mã đơn vị), ví dụ `TINH_VAO {co: false, nguon: "qc3626.d43.k2"}`.
3. **LLM điền khuôn, code dựng đồ thị.** Với mỗi khoản, LLM xuất JSON theo một khuôn Pydantic (danh sách khái niệm, đối tượng, cây điều kiện, quan hệ). Code tự sinh mã, gộp nút, gắn `NGUON`. LLM không viết Cypher.
4. **6 bảng phân loại (F1) nhập bằng script hoặc tay, không qua LLM** (Q10). Các bảng này ít và có cấu trúc, nhưng là nguồn của nhiều câu hỏi điều kiện.

Thuộc tính chung của mọi nút ngữ nghĩa: `id` (`sem.{nhan}.{slug}`), `ten` (dạng chuẩn), `bi_danh` (danh sách), `nguon_loai` (`trich_xuat` \| `bang` \| `nhap_tay`).

### 2.1. Loại nút (6)

| Nhãn | Thuộc loại này khi… | Thuộc tính riêng | Ví dụ thật |
| --- | --- | --- | --- |
| `KhaiNiem` | Thuật ngữ, đại lượng, loại học phần, hoặc một **mức phân loại** | `loai`: `dai_luong` \| `loai_hoc_phan` \| `muc_phan_loai` \| `khac`; `dinh_nghia`; `gia_tri` (nếu là mức cố định). Mức có thứ bậc thêm **`thang`** (`nam_dao_tao` \| `diem_chu` \| `hoc_luc` \| `hang_tot_nghiep`) và **`thu_tu`** (số nguyên, bảng 2.1a) | "tín chỉ" (định nghĩa `d4.k2`); "điểm trung bình chung tích lũy" (`d42.k5`); "học phần điều kiện" (`d5.k2.g`); "năm thứ hai" `thang: nam_dao_tao, thu_tu: 2` (`d17.k2`); "C+" `gia_tri: 2.5, thang: diem_chu, thu_tu: 5` (`d43.k1`) |
| `DoiTuong` | Ai hoặc phạm vi mà quy định nhắm tới: người, chức danh, đơn vị, loại chương trình, hình thức đào tạo, loại văn bằng | `loai`: `nguoi` \| `chuc_danh` \| `don_vi` \| `chuong_trinh` \| `hinh_thuc` \| `van_bang` | "sinh viên"; "thủ trưởng đơn vị đào tạo"; "trường đại học thành viên"; "CTĐT chất lượng cao" (`d6.k2`); "chính quy"; "kỹ sư" (`d14.k2.a`) |
| `HoatDong` | Thủ tục có người thực hiện hoặc xin, gắn với điều kiện | `mo_ta` | "xét công nhận tốt nghiệp" (`d46.k1`); "chuyển ngành học" (`d28.k1`); "đăng ký học chương trình đào tạo thứ hai" (`d26.k1`); "xếp năm đào tạo" (`d17.k2`) |
| `NhomDieuKien` | Một mệnh đề "nếu … thì …" trong văn bản: gom các điều kiện, **lồng được** | `ket_hop`: `AND` \| `OR`; `mo_ta` (một câu diễn giải) | `d26.k1` (AND), con là `d26.k1.c` (OR của hai nhóm AND); `d44.k2` (OR) |
| `DieuKien` | Mệnh đề nguyên tử, đúng hoặc sai với một sinh viên cụ thể | `mo_ta` (nguyên văn rút gọn). Nếu là số hoặc mức có thứ bậc: `min`, `max`, `min_incl`, `max_incl`, `don_vi`, `thang` (`thang_10` \| `thang_4` \| `tin_chi` \| `ty_le` \| `nam_dao_tao` \| `diem_chu` \| `hoc_luc` \| `hang_tot_nghiep`). Với 4 thang có thứ bậc, `min`/`max` **so trên `thu_tu`** (bảng 2.1a) và `XET` tới đại lượng tương ứng ("năm đào tạo", "điểm học phần"…). Nếu không phải số: `toan_tu` (`la` \| `thuoc` \| `co`). `phu_dinh` (bool); `so_voi` (đại lượng tham chiếu, cho ngưỡng tương đối); `moc` (mốc thời gian) | "từ 40 – 80 tín chỉ" → `min 40, max 80` (`d17.k2`); "dưới 1,40" → `max 1.40, max_incl false` (`d44.k1.a`); "sinh viên các năm tiếp theo và cuối khoá" → `min 4, min_incl true, thang nam_dao_tao`, "cuối khoá" giữ trong `mo_ta` (`d44.k1.a`); "phải đạt từ B trở lên" → `min 6, min_incl true, thang diem_chu` (`d27.k4.a`); "không bị cảnh báo học tập" → `phu_dinh true` (`d28.k1.d`); "không ít hơn 2/3 khối lượng trung bình một học kỳ" → `min 0.667, thang ty_le, so_voi …` (`d21.k1`) |
| `HeQua` | Điều xảy ra với hoặc cho sinh viên khi nhóm điều kiện thỏa: trạng thái, quyền, nghĩa vụ, chế tài, giới hạn | `loai`: `trang_thai` \| `quyen_loi` \| `nghia_vu` \| `che_tai` \| `gioi_han`; `mo_ta`; `gia_tri`, `don_vi` (nếu có số) | "bị cảnh báo học vụ" (`d44.k1`); "bị buộc thôi học" (`d44.k2`); "hạng tốt nghiệp bị hạ một hạng" (`d47.k7`); "chỉ được đăng ký không quá 18 tín chỉ" (`d23.k1.c`) |

Ghi chú: chuỗi trong ngoặc kép ở cột "Ví dụ thật" là `ten` dạng chuẩn của nút, **không phải trích nguyên văn**; nguyên văn xem mã đơn vị đi kèm.

**Bảng 2.1a. Thứ bậc `thu_tu` của các mức** (`KhaiNiem {loai: muc_phan_loai}`; nhập bằng script/tay cùng 6 bảng ở Q10)

| `thang` | Mức → `thu_tu` | Nguồn |
| --- | --- | --- |
| `nam_dao_tao` | năm thứ nhất 1, năm thứ hai 2, năm thứ ba 3, năm thứ tư 4, năm thứ năm 5, năm thứ sáu 6 | `d17.k2` |
| `diem_chu` | F 1, D 2, D+ 3, C 4, C+ 5, B 6, B+ 7, A 8, A+ 9 | `d41.k2`, `d43.k1` |
| `hoc_luc` | Yếu 1, Trung bình 2, Khá 3, Giỏi 4, Xuất sắc 5 | `d45.k2` |
| `hang_tot_nghiep` | Trung bình 1, Khá 2, Giỏi 3, Xuất sắc 4 | `d47.k5` |

`d47.k7` "hạ một hạng" = `thu_tu − 1`, chỉ áp dụng cho hạng Xuất sắc và Giỏi (Xuất sắc 4 → Giỏi 3, Giỏi 3 → Khá 2).

P (đạt không phân mức, `d41.k2.b`) và I, X (`d41.k2.d`) không có `thu_tu`: không so sánh hơn kém với các mức khác.
Mỗi mức nối về đại lượng của thang bằng `LA_MOT` (ví dụ "năm thứ hai" `LA_MOT` "năm đào tạo"), để đồ thị vẫn đi được từ điều kiện theo năm sang bảng xếp năm khi không dùng Cypher (mẫu B).

Mốc phân biệt dễ nhầm:
- **Trạng thái có thể thay đổi theo kỳ** của một sinh viên (năm đào tạo, bị cảnh báo, học lực) là `DieuKien` bên trong nhóm.
- **Phạm vi cố định** (loại chương trình, hình thức, loại văn bằng) gắn bằng `AP_DUNG_CHO`.
- Thủ tục là `HoatDong`. Kết quả của thủ tục là `HeQua`.

Bỏ nhãn `Nguong` của PLAN: ngưỡng thành thuộc tính khoảng của `DieuKien` (Q2).

### 2.2. Loại cạnh (10)

| Cạnh | Từ → Đến | Ý nghĩa | Ví dụ thật |
| --- | --- | --- | --- |
| `NGUON` | mọi nút ngữ nghĩa → `Khoan`/`Diem`/`Dieu` | nêu ở đâu (**bắt buộc**) | — |
| `GOM` | `NhomDieuKien` → `DieuKien` \| `NhomDieuKien` | thành phần của nhóm | `d26.k1` → `d26.k1.a`, `.b`, `.d`, nhóm con `.c` |
| `DAN_DEN` | `NhomDieuKien` → `HeQua` \| `KhaiNiem` (mức phân loại) | nhóm thỏa thì có hệ quả hoặc được xếp mức | `d44.k2` → "bị buộc thôi học"; `d17.k2` (dòng CLC 40–80) → "năm thứ hai" |
| `YEU_CAU` | `HoatDong` → `NhomDieuKien` | thủ tục đòi hỏi nhóm điều kiện | "xét công nhận tốt nghiệp" → nhóm AND `d46.k1` |
| `XET` | `DieuKien` → `KhaiNiem` (đại lượng/mức) \| `HeQua` (trạng thái) | điều kiện kiểm cái gì | "dưới 1,40" → "điểm TBC tích lũy"; "các năm tiếp theo" `{min 4, thang nam_dao_tao}` → "năm đào tạo" (`d44.k1.a`); "không bị cảnh báo học tập" → "bị cảnh báo học vụ" (`d28.k1.d`) |
| `AP_DUNG_CHO` | `NhomDieuKien` \| `HeQua` \| `HoatDong` → `DoiTuong` | chỉ áp dụng cho phạm vi này | `d46.k1.d` vế 2 → "CTĐT tài năng", "CTĐT chất lượng cao" |
| `LA_MOT` | `KhaiNiem`→`KhaiNiem`, `DoiTuong`→`DoiTuong` | loại con → loại cha; mức → đại lượng của thang | "GDTC" → "học phần điều kiện" (`d5.k2.g`); "năm thứ hai" → "năm đào tạo" (`d17.k2`); "Trường ĐH Công nghệ" → "trường đại học thành viên" (Q8) |
| `TINH_VAO {co, boi_canh}` | `KhaiNiem` (loại học phần) → `KhaiNiem` (đại lượng) | có hoặc không tính vào đại lượng. `boi_canh` = `id` của một `HoatDong` mà quy tắc chỉ áp dụng cho nó; **để trống nghĩa là áp dụng chung** | chung: "học phần tự chọn tự do" → "điểm TBC tích lũy" `{co: false}` (`d21.k2.d`); có bối cảnh: "học phần tự chọn tự do" → "số tín chỉ tích lũy" `{co: false, boi_canh: <HoatDong "xếp năm đào tạo">}` (`d17.k2`) |
| `DUNG_DE {co}` | `KhaiNiem` (đại lượng) → `HoatDong` | đại lượng được hoặc không được dùng cho thủ tục | "điểm TBC tích lũy" → "xếp hạng tốt nghiệp" `{co: true}` (`d43.k2`); "điểm thưởng NCKH" → "xếp loại học lực" `{co: false}` (`d32.k1`) |
| `GIAO_QUY_DINH` | `HoatDong` \| `NhomDieuKien` \| `KhaiNiem` → `DoiTuong` | văn bản giao cho chủ thể quy định chi tiết | nhóm OR `d44.k1` → "thủ trưởng đơn vị đào tạo" `{noi_dung: "chọn 1–2 trong 3 điều kiện; số lần cảnh báo không quá 2 lần liên tiếp", nguon: "qc3626.d44.k1"}` |

**Vì sao `TINH_VAO` dùng `boi_canh` thay vì tách đại lượng riêng** (ví dụ "số tín chỉ tích lũy để xếp năm đào tạo"): hai đại lượng tên gần giống nhau dễ bị bước gộp thực thể trùng (Q6) gộp nhầm về một nút, khi đó quy tắc loại trừ riêng của việc xếp năm lại thành quy tắc chung và trả lời sai câu hỏi về tốt nghiệp. Giữ **một** đại lượng "số tín chỉ tích lũy" và đặt ngữ cảnh trên cạnh thì gộp an toàn; truy vấn lọc `boi_canh IS NULL OR boi_canh = <thủ tục đang hỏi>`.

Để sau, chưa cần cho bộ câu hỏi thử: `TINH_TU` (công thức, đã có nguyên văn ở `d43.k2`, `d8.k1.b`); `THAM_QUYEN` (ai phê duyệt từng thủ tục). Khi cần, có thể biểu diễn tạm "được sự đồng ý của X" bằng `DieuKien {toan_tu: "co"}`.

### 2.3. Bốn mẫu đọc văn bản thành đồ thị

**A. Một dòng bảng: `d17.k2` (tr. 14–15), CLC, 40–80 tín chỉ → năm thứ hai**

```
(n:NhomDieuKien {ket_hop:"AND"}) -[:AP_DUNG_CHO]-> (:DoiTuong {ten:"CTĐT chất lượng cao", loai:"chuong_trinh"})
(n) -[:GOM]-> (:DieuKien {mo_ta:"Từ 40 – 80 tín chỉ", min:40, max:80, min_incl:true, max_incl:true, thang:"tin_chi"})
                 -[:XET]-> (sotc:KhaiNiem {ten:"số tín chỉ tích lũy", loai:"dai_luong"})
(n) -[:DAN_DEN]-> (nam2:KhaiNiem {ten:"năm thứ hai", loai:"muc_phan_loai", thang:"nam_dao_tao", thu_tu:2})
(nam2) -[:LA_MOT]-> (nd:KhaiNiem {ten:"năm đào tạo", loai:"dai_luong"})
(hd:HoatDong {ten:"xếp năm đào tạo"})
(:KhaiNiem {ten:"học phần tự chọn tự do"}) -[:TINH_VAO {co:false, boi_canh:hd.id, nguon:"qc3626.d17.k2"}]-> (sotc)
    ← tương tự cho GDTC, GDQP-AN, kỹ năng bổ trợ: d17.k2 loại cả 4 khi xếp năm, không phải loại chung
mọi nút trên -[:NGUON]-> (:Khoan {id:"qc3626.d17.k2"})
```
Bảng có 14 dòng (chuẩn 6, CLC 4, tài năng 4; ô "-" không tạo dòng), do script sinh.

**B. Ngưỡng phụ thuộc mức phân loại: `d44.k1.a` (tr. 46), điều kiện theo năm so trên `thu_tu`**

Nguyên văn: "Điểm trung bình chung tích luỹ đạt dưới 1,20 đối với sinh viên năm thứ nhất, dưới 1,40 đối với sinh viên năm thứ hai, dưới 1,60 đối với sinh viên năm thứ ba hoặc dưới 1,80 đối với sinh viên các năm tiếp theo và cuối khoá". Điểm a sinh **4 nhóm AND**:

| Nhóm | Điều kiện năm (`thang: nam_dao_tao`, so trên `thu_tu`) | Điều kiện TBC tích lũy (`thang: thang_4`) |
| --- | --- | --- |
| năm thứ nhất | `min 1, max 1` | `max 1.20, max_incl false` |
| năm thứ hai | `min 2, max 2` | `max 1.40, max_incl false` |
| năm thứ ba | `min 3, max 3` | `max 1.60, max_incl false` |
| "các năm tiếp theo và cuối khoá" | `min 4`, `max` trống; "cuối khoá" giữ trong `mo_ta` | `max 1.80, max_incl false` |

Một nhóm (năm thứ hai):
```
(m:NhomDieuKien {ket_hop:"AND"})
(m) -[:GOM]-> (:DieuKien {mo_ta:"sinh viên năm thứ hai", min:2, max:2, min_incl:true, max_incl:true, thang:"nam_dao_tao"})
                 -[:XET]-> (nd)                                    ← "năm đào tạo", cùng nút ở mẫu A
(m) -[:GOM]-> (:DieuKien {mo_ta:"dưới 1,40", max:1.40, max_incl:false, thang:"thang_4"})
                 -[:XET]-> (tbc:KhaiNiem {ten:"điểm trung bình chung tích lũy", loai:"dai_luong"})
(m) -[:DAN_DEN]-> (cb:HeQua {ten:"bị cảnh báo học vụ", loai:"trang_thai"})
(p:NhomDieuKien {ket_hop:"OR"}) -[:GOM]-> (m), cùng 3 nhóm năm còn lại của điểm a và các nhóm của điểm b, c   ← p -[:NGUON]-> d44.k1
(p) -[:GIAO_QUY_DINH {noi_dung:"chọn 1–2 trong 3 điều kiện…", nguon:"qc3626.d44.k1"}]-> (:DoiTuong {ten:"thủ trưởng đơn vị đào tạo"})
```
**Cầu nối nhiều bước là `thu_tu` của năm đào tạo.** Câu hỏi chỉ nêu số tín chỉ và điểm TBC. Vector search dễ lấy được `d44.k1` nhưng không lấy `d17.k2`.
- Bằng Cypher (2.4): mẫu A cho ra mức có `thu_tu`, mẫu B so `thu_tu` với khoảng `min`/`max`, nên chạy cho **mọi năm**, kể cả "các năm tiếp theo" vốn không có nút mức riêng.
- Bằng duyệt đồ thị (không Cypher): `d44.k1.a` → (`XET`) "năm đào tạo" ← (`LA_MOT`) "năm thứ hai" ← (`DAN_DEN`) dòng bảng → (`NGUON`) `d17.k2`.

**C. Điều kiện lồng: `d26.k1` (tr. 23–24), học cùng lúc hai CTĐT**

```
(:HoatDong {ten:"đăng ký học chương trình đào tạo thứ hai"}) -[:YEU_CAU]-> (g:NhomDieuKien {ket_hop:"AND"})
(g) -[:AP_DUNG_CHO]-> (:DoiTuong {ten:"chính quy", loai:"hinh_thuc"})
(g) -[:GOM]-> DieuKien a (ngành chính khác), b (đã học ≥ 2 học kỳ), d (có đơn, trả học phí)
(g) -[:GOM]-> (o:NhomDieuKien {ket_hop:"OR"})                         ← điểm c
(o) -[:GOM]-> NhomDieuKien AND { TBC các học phần ≥ 2,5 ; đáp ứng ngưỡng ĐBCL đầu vào CT2 }
(o) -[:GOM]-> NhomDieuKien AND { TBC các học phần ≥ 2,0 ; đáp ứng điều kiện trúng tuyển CT2 }
```

**D. Phủ định trạng thái và từ đồng nghĩa: `d28.k1.d` (tr. 28)**

```
(:HoatDong {ten:"chuyển ngành học"}) -[:YEU_CAU]-> (AND d28.k1) -[:GOM]->
    (:DieuKien {mo_ta:"Không bị cảnh báo học tập hoặc bị xét thi hành kỷ luật", phu_dinh:true}) -[:XET]-> (cb)
```
"Cảnh báo học tập" (`d28.k1.d`), "cảnh báo học vụ" (`d44.k1`) và "cảnh báo kết quả học tập" (`d44.k1.c`, `d26.k6`) phải gộp về cùng nút `cb` (Q6). Nếu không gộp, cầu nối từ chuyển ngành sang xử lý học vụ bị đứt.

### 2.4. Truy vấn minh họa cho câu thử 7 (chưa chạy, sẽ kiểm ở T4)

```cypher
// Sinh viên CLC, 50 tín chỉ tích lũy, TBC tích lũy 1,35: có bị cảnh báo học vụ không?
// tc là số tín chỉ đã trừ các học phần TINH_VAO {co:false, boi_canh: "xếp năm đào tạo"}; truy vấn không tự trừ.
WITH 50 AS tc, 1.35 AS tbc, "CTĐT chất lượng cao" AS ct
// Bước 1: xếp năm đào tạo theo bảng d17.k2 (mẫu A)
MATCH (r1:NhomDieuKien)-[:AP_DUNG_CHO]->(:DoiTuong {ten: ct}),
      (r1)-[:GOM]->(c1:DieuKien)-[:XET]->(:KhaiNiem {ten: "số tín chỉ tích lũy"}),
      (r1)-[:DAN_DEN]->(nam:KhaiNiem {loai: "muc_phan_loai", thang: "nam_dao_tao"})
WHERE (c1.min IS NULL OR tc > c1.min OR (c1.min_incl AND tc = c1.min))
  AND (c1.max IS NULL OR tc < c1.max OR (c1.max_incl AND tc = c1.max))
// Bước 2: nhóm có điều kiện năm chứa nam.thu_tu và ngưỡng TBC thỏa (mẫu B)
MATCH (r2:NhomDieuKien)-[:GOM]->(cn:DieuKien {thang: "nam_dao_tao"}),
      (r2)-[:GOM]->(c2:DieuKien)-[:XET]->(:KhaiNiem {ten: "điểm trung bình chung tích lũy"}),
      (r2)-[:DAN_DEN]->(hq:HeQua)
WHERE (cn.min IS NULL OR nam.thu_tu > cn.min OR (cn.min_incl AND nam.thu_tu = cn.min))
  AND (cn.max IS NULL OR nam.thu_tu < cn.max OR (cn.max_incl AND nam.thu_tu = cn.max))
  AND c2.max IS NOT NULL AND (tbc < c2.max OR (c2.max_incl AND tbc = c2.max))
OPTIONAL MATCH (p:NhomDieuKien)-[:GOM]->(r2), (p)-[g:GIAO_QUY_DINH]->(ai:DoiTuong)
MATCH (r1)-[:NGUON]->(s1), (r2)-[:NGUON]->(s2)
RETURN nam.ten, nam.thu_tu, cn.min, cn.max, c2.max, hq.ten, g.noi_dung, ai.ten, s1.id, s2.id
```
Kết quả mong đợi:

| Đầu vào | Năm (`thu_tu`) | Nhóm khớp | Ngưỡng | Kết luận | Nguồn |
| --- | --- | --- | --- | --- | --- |
| `tc = 50, tbc = 1.35` | năm thứ hai (2) | `min 2, max 2` | 1.40 | bị cảnh báo học vụ, kèm ghi chú giao quyền | `d17.k2`, `d44.k1.a` |
| `tc = 130, tbc = 1.75` | năm thứ tư (4; CLC 116–155) | "các năm tiếp theo" `min 4` | 1.80 | bị cảnh báo học vụ, kèm ghi chú giao quyền | `d17.k2`, `d44.k1.a` |

### 2.5. Chưa biểu diễn ở v1 (chấp nhận, ghi lại để khỏi quên)

- **Leo thang theo số lần vi phạm**: "đã bị khiển trách 1 lần nhưng… vẫn tiếp tục vi phạm" (`d35.k4`); "Bị kỷ luật lần thứ hai vì lý do thi hộ" (`d44.k2.c`). Tạm để trong `mo_ta`.
- **Ngưỡng tương đối** (`d21.k1` 2/3 và 3/2; `d14.k2.b` chuẩn + 15; `d17.k1.d` gấp 2 lần): chỉ lưu `ty_le` và `so_voi`, không tính tự động.
- **Công thức** (`d8.k1.b` học phí, `d43.k2` điểm TBC): giữ trong nguyên văn, không tách.
- **Nghĩa vụ, quyền lợi chung chung** (`d33`, `d35.k1`, `d36.k1`): không trích ngữ nghĩa, chỉ có lớp cấu trúc.

## 3. Câu hỏi thử schema

Đã đối chiếu từng câu với nguyên văn. Các câu 7–10 mới, mỗi câu thử một cơ chế cụ thể của đồ thị.

**Quy tắc chấm** (áp dụng cho bộ câu hỏi; cần bổ sung vào PLAN mục 6 khi duyệt):
1. `source_ids` ghi ở mức **`:DonViNoiDung`**: điểm quy về khoản cha; điều không có khoản thì tính là điều; nội dung nằm ở phần dẫn hoặc `doan_khong_so` mức điều thì tính là điều. Mã do hệ thống trả về cũng quy về mức này trước khi chấm.
2. Chỉ ghi **nguồn bắt buộc** (thiếu thì không trả lời đủ ý), **tối đa 5 nguồn**. Điểm cụ thể (nếu cần) ghi ở cột cuối để tra cứu, không dùng để chấm.
3. Báo cả **Recall@5 và Recall@10** (cùng MRR theo PLAN mục 6).

| # | Loại | Câu hỏi | `source_ids` (mức `:DonViNoiDung`) | Đáp án theo văn bản | Đồ thị cần / giúp gì |
| --- | --- | --- | --- | --- | --- |
| 1 | single | Điểm chữ C+ được quy đổi thành bao nhiêu điểm **theo thang 4**? | `d43.k1` | 2,5 | Bản cũ ghi "điểm số", **mơ hồ**: thang 10 thì C+ là 6,5–6,9 (`d41.k2`). Sửa câu hỏi. Vector đủ trả lời |
| 2 | conditional | SV CTĐT chất lượng cao đã tích lũy 50 tín chỉ là SV năm thứ mấy? | `d17.k2` | Năm thứ hai (CLC: 40–80) | Câu đúng đề (bản trước nghi ngờ nhầm). Cần mẫu A. Lưu ý không tính tự chọn tự do, GDTC, GDQP-AN, KNBT |
| 3a | single | Điểm TBC tích lũy được tính thế nào? | `d43.k2`, `d42.k5` | Trung bình theo trọng số tín chỉ của các học phần **đạt**; làm tròn 2 chữ số | Vector đủ |
| 3b | multi | Những học phần nào không được tính vào điểm TBC tích lũy? | `d5.k2`, `d21.k2`, `d41.k2`, `d42.k5` | Học phần điều kiện: GDTC, GDQP-AN, kỹ năng bổ trợ (`d5.k2.g`); học phần tự chọn tự do (`d21.k2.d`); học phần chỉ yêu cầu đạt (P, `d41.k2.b`); học phần chưa đạt, vì điểm TBC tích lũy chỉ tính học phần đạt (`d42.k5`) | `LA_MOT` (GDTC, GDQP-AN, KNBT → học phần điều kiện) + `TINH_VAO {co:false}` với `boi_canh` trống; định nghĩa đại lượng ở `d42.k5`. **Tách câu 3 cũ thành 3a và 3b**, và thu hẹp 3b: bản trước có 10 nguồn, Recall@5 tối đa chỉ 0,5. Điểm: `d5.k2.g`, `d21.k2.d`, `d41.k2.b`. Tra cứu thêm: `d43.k2` (GDQP-AN, GDTC; không bắt buộc vì `d5.k2.g` đã nêu học phần điều kiện không tính vào điểm TBC tích lũy) |
| 4 | multi | SV CTĐT chất lượng cao cần những điều kiện gì để được xét tốt nghiệp? | `d46.k1`, `d13.k3` | 8 điều kiện a–h; TBC ≥ 2,50; ngoại ngữ bậc 4; điểm e do ĐHQGHN và đơn vị quy định | `YEU_CAU` + `DAN_CHIEU` (`d46.k1.đ` → `d13.k3`) + `AP_DUNG_CHO`. Thêm "CLC" để câu có điều kiện thật. Điểm: `d46.k1.d`, `d13.k3.b` |
| 5 | conditional | Khi nào SV bị buộc thôi học? | `d44.k2`, `d17.k1`, `d35.k4`, `d36.k3` | 5 trường hợp (OR); số lần cảnh báo do thủ trưởng quy định | OR + `DAN_CHIEU` + `GIAO_QUY_DINH`. Câu cũ "khi nào bị xử lý học vụ" quá rộng (gộp cả cảnh báo lẫn buộc thôi học). Điểm: `d17.k1.d` (qua dẫn chiếu ở `d44.k2.b`), `d35.k4.d` (qua `d44.k2.c`) |
| 6 | out_of_scope (giao quyền) | Học phí ngành KHMT năm 2026 là bao nhiêu? | `d8.k1` | Quy chế không nêu mức tiền; chỉ có công thức và giao thủ trưởng xây dựng định mức | **Không phải ngoài phạm vi thuần túy**. Đáp án mong đợi phải nhắc điểm `d8.k1.b`. Xem Q11 |
| 7 | conditional, multi | SV CLC tích lũy 50 tín chỉ, TBC tích lũy 1,35 thì có bị cảnh báo học vụ không? | `d17.k2`, `d44.k1` | Năm thứ hai, ngưỡng < 1,40, nên thuộc diện cảnh báo theo điều kiện a. Nhưng UET chọn 1–2 trong 3 điều kiện, cần đối chiếu quy định UET | **Câu trình diễn chính**: cầu qua `thu_tu` năm đào tạo (mẫu B, 2.4). Điểm: `d44.k1.a` |
| 8 | single (giao quyền) | Ở UET, bị cảnh báo học vụ mấy lần thì bị buộc thôi học? | `d44.k2`, `d44.k1` | QĐ 3626 không nêu số lần; giao thủ trưởng quy định, giới hạn "không quá 2 lần liên tiếp" | `GIAO_QUY_DINH`. Phụ thuộc S1: hiện câu giao quyền bị gắn vào `d44.k1.c`. Điểm: `d44.k2.a` |
| 9 | multi | Học phần GDTC có tính vào điểm TBC tích lũy không, có cần để tốt nghiệp không? | `d5.k2`, `d43.k2`, `d46.k1` | Không tính; nhưng phải đạt mới được tốt nghiệp | `LA_MOT` (GDTC → học phần điều kiện) + `TINH_VAO {co:false}` + `YEU_CAU`. Điểm: `d5.k2.g`, `d46.k1.g` |
| 10 | conditional | SV đang bị cảnh báo học vụ có được chuyển ngành không? | `d28.k1`, `d28.k2` | Không (điều kiện d); ngoài ra từ năm thứ hai không được xét | Mẫu D: phủ định + gộp đồng nghĩa. Điểm: `d28.k1.d` |

Gợi ý: câu ngoài phạm vi thuần túy nên chọn chủ đề mà quy chế hoàn toàn không nhắc (ví dụ ký túc xá, giờ mở cửa thư viện). Kiểm bằng grep trước khi đưa vào bộ câu hỏi.

## 4. Quyết định cần chủ dự án chốt

**S = lớp cấu trúc** (ảnh hưởng parser, cần trước T3). **Q = lớp ngữ nghĩa** (cần trước T4).

| # | Câu hỏi | Phương án | Khuyến nghị + bằng chứng | Quyết định |
| --- | --- | --- | --- | --- |
| S1 | Đoạn không đánh số sau hoặc xen giữa danh sách (1.4)? | A. Giữ nguyên, chấp nhận trích dẫn lệch. B. Sửa tay 8 chỗ qua `overrides`, chuyển vào `doan_khong_so` của `Khoan`/`Dieu` (giữ vị trí `sau`). C. Heuristic tự động theo thụt lề PDF | **B**. Chỉ 8 chỗ. Cơ chế `overrides` hiện chỉ tìm/thay trong một đơn vị, **cần mở rộng parser** để chuyển đoạn (mục 5). C dễ sai: có đoạn mới thuộc đúng điểm, ví dụ "Sinh viên bị kỷ luật đình chỉ thi…" trong `d35.k4.c`. A làm sai trích dẫn câu thử 8 | |
| S2 | Đơn vị đặt vector index? | A. Chỉ `Khoan` (179). B. `Khoan` + phần dẫn của 12 `Dieu` (191 đơn vị, nhãn `:DonViNoiDung`; 192 nếu S1 = B, thêm `d35`) | **B**. Với A, `d16` (học kỳ), `d19` (giờ học), `d51` (hiệu lực), phần dẫn `d44` không bao giờ được truy xuất | |
| S3 (Q1 cũ) | `Diem` là nút riêng? | A. Nút riêng. B. Gộp vào `Khoan` | **A, nhưng không index riêng**. 4 dẫn chiếu trỏ tới mức điểm (`d7.k1`, `d7.k2`, `d36.k3.d`, `d44.k2.c`); 71/262 điểm dưới 80 ký tự, quá ngắn để embedding tốt | |
| S4 (Q5 cũ) | "Điều N" trỏ tới đâu? | A. Nút `Dieu`. B. Mọi `Khoan` của điều | **A**. Đúng văn bản; mở rộng xuống bằng `THUOC` một bước | |
| S5 (Q4 cũ) | Văn bản ngoài? | A. Không tạo nút. B. Tạo `VanBan {trong_pham_vi:false}` cho văn bản **có tên** | **B, chỉ văn bản có tên**: Luật sửa đổi Luật GDĐH (`d10.k1`), Luật Giáo dục, Luật GDĐH, Luật Viên chức (`d33.k1.a`), Khung năng lực ngoại ngữ 6 bậc (`d13.k3`), Quy định chuẩn CTĐT của Bộ (`d6.k1`). 16 cụm "quy định hiện hành" không tên thì không tạo nút. `CAN_CU` cần trang Quyết định 3626 (F10): bạn có file đó không? | |
| Q2 | Ngưỡng là nút hay thuộc tính? | A. Nút `Nguong`. B. Thuộc tính khoảng trên `DieuKien` | **B**. Cả 6 bảng đều là khoảng; nút ngưỡng không có cạnh nào khác. Bất lợi: không hỏi được "quy định nào dùng mốc 35", chấp nhận được. Mức có thứ bậc (năm đào tạo, điểm chữ, học lực, hạng tốt nghiệp) so qua `thu_tu` (bảng 2.1a) | |
| Q3 | Loại chương trình là thực thể? | A. Thuộc tính. B. `DoiTuong {loai:chuong_trinh}` + `LA_MOT` | **B**. 4 quy định đổi ngưỡng theo chương trình (F2); một ngưỡng áp dụng cho nhiều chương trình (`d46.k1.d`: tài năng và CLC) | |
| Q6 | Gộp thực thể trùng? | A. Danh sách tay. B. Embedding. C. LLM tự gộp | **A + B gợi ý, bạn duyệt**. Ví dụ thật: cảnh báo học vụ / học tập / kết quả học tập. Biến thể chính tả NFC không xử lý được: "tích luỹ" (2 lần) / "tích lũy" (44), "khoá"/"khóa", "học kì"/"học kỳ", "đăng kí"/"đăng ký", "qui"/"quy", "kĩ sư". Chuẩn hóa các biến thể này cho **khóa tìm kiếm và bí danh**, không sửa nguyên văn trích dẫn | |
| Q7 | Biểu diễn AND/OR? | A. Mặc định AND (đề xuất trước, **bỏ**). B. `NhomDieuKien` lồng nhau | **B**. Dấu hiệu: "có đủ các điều kiện sau" là AND; "một trong các trường hợp/lỗi/diện" là OR. Nhưng `d44.k1` không có dấu hiệu nào (thực chất là OR): LLM phải suy luận, người phải kiểm | |
| Q8 (mới) | Tri thức nền về UET? | A. Không có. B. Nhập tay vài bộ ba, `nguon_loai = nhap_tay`: UET `LA_MOT` trường đại học thành viên; thủ trưởng đơn vị đào tạo tại UET = Hiệu trưởng | **B**. Không có thì không trả lời được ai duyệt hay ai ký ở UET (F7, 10 đơn vị). Cần một nguồn trích dẫn được cho các bộ ba này; bạn chọn nguồn | |
| Q9 (mới) | Phạm vi trích ngữ nghĩa ở T4? | A. Cả 51 điều. B. 34 điều liên quan trực tiếp đến sinh viên trước (4–8, 13, 14, 16–29, 32, 35–39, 41–47), phần còn lại chỉ có lớp cấu trúc | **B**. Giảm chi phí LLM và công kiểm tay; các điều về giảng viên, tổ chức (`d2`, `d33`, `d40`…) ít câu hỏi. Mở rộng sau nếu còn thời gian | |
| Q10 (mới) | 6 bảng phân loại trích bằng gì? | A. LLM như các khoản khác. B. Script hoặc nhập tay, kiểm 100% | **B**. Khoảng 50 dòng (14 + 10 + 9 + 5 + 4 + 7), nguồn của nhiều câu điều kiện; sai một dòng là sai đáp án | |
| Q11 (mới, đánh giá) | Gắn nhãn câu hỏi về nội dung bị giao quyền thế nào? | A. Thêm loại thứ 5 `delegated` (sửa PLAN mục 6). B. Giữ 4 loại, thêm trường `delegated: true` | **B**. Ít thay đổi PLAN, vẫn đo riêng được tỷ lệ trả lời đúng "giao cho UET" | |

## 5. Nếu duyệt: việc cho Claude Code

Trước khi code: sửa `docs/PLAN.md` theo các quyết định đã chốt (quy tắc trong CLAUDE.md): mục 3 (loại nút ngữ nghĩa: bỏ "ngưỡng", thêm `HoatDong`, `NhomDieuKien`; vector index trên `:DonViNoiDung`), mục 6 (quy tắc chấm ở mục 3 của file này; trường `delegated` nếu Q11 = B), tiêu chí T4 (định nghĩa lại "bộ ba đúng" khi có điều kiện lồng).

1. **S1: cần mở rộng parser**, không chỉ thêm mục vào file. Cơ chế `overrides` hiện chỉ tìm/thay chữ trong `own_text` của một đơn vị. Thêm thao tác **chuyển đoạn**: cắt đoạn khỏi điểm, đưa vào `doan_khong_so` của `Khoan`/`Dieu` kèm `sau`, dựng lại `text` của cha theo thứ tự gốc. Áp cho 8 chỗ ở 1.4; kiểm riêng 2 chỗ xen giữa (`d44.k2`, `d35.k4`) và chỗ mức điều (`d35`). Chạy lại parser: vẫn 51/179/262, không cảnh báo.
2. **Bảng bị dàn dòng:** thêm overrides tách dòng cho `d41.k2.a` (thang 10 → điểm chữ), `d32.k2.a`, `d32.k2.b` (mức điểm thưởng), cùng cách đã làm với `d43.k1`.
3. **Regex `DAN_CHIEU`:** viết test từ **Phụ lục A** (22 cụm). Mong đợi: 21 cụm nội bộ sinh đúng **31 cạnh**; cụm bẫy `d10.k1` không sinh cạnh nội bộ; 0 cạnh trỏ tới đơn vị không tồn tại.
4. **S2:** đánh dấu `:DonViNoiDung` (191; 192 nếu S1 = B).
5. **Q6:** hàm chuẩn hóa khóa tìm kiếm (biến thể chính tả), có test.
6. **T4 (không làm trước T4):** chạy thử trích xuất ngữ nghĩa trên 5 điều **`d26`, `d28`, `d43`, `d44`, `d46`**, kiểm tay trước khi chạy phần còn lại của Q9. Bảng `d17.k2` và các bảng phân loại khác nhập bằng script (Q10), không qua LLM.

## Phụ lục A. 22 cụm dẫn chiếu trong QĐ 3626 (dữ liệu test cho `DAN_CHIEU`)

Quét ngày 18/10/2026 trên `qc3626.json` bằng mẫu `Điều\s+(\d+|này)` trong `own_text` mọi đơn vị (bỏ tiêu đề điều). Không có dẫn chiếu ngầm (chỉ ghi "khoản N"/"điểm x" mà không kèm "Điều"). Mọi đích đã kiểm là có trong `qc3626.json`.

| # | Nguồn | Cụm nguyên văn | Đích mong đợi | Số cạnh |
| --- | --- | --- | --- | --- |
| 1 | `d7.k1` | "điểm a, b, c, d, đ, e, g, khoản 2, Điều 6 của Quy chế này" | `d6.k2.a`, `.b`, `.c`, `.d`, `.đ`, `.e`, `.g` | 7 |
| 2 | `d7.k2` | "điểm a, khoản 2, Điều 6 của Quy chế này" | `d6.k2.a` | 1 |
| 3 | `d7.k2` | "Điều 10 của Quy chế này" | `d10` | 1 |
| 4 | `d8.k2.b` | "khoản 2, Điều 2 của Quy chế này" | `d2.k2` | 1 |
| 5 | `d10.k1` | "khoản 22, Điều 1 Luật sửa đổi, bổ sung một số điều của Luật Giáo dục đại học" | **bẫy**: không có cạnh nội bộ (nếu S5 = B: 1 cạnh tới `VanBan` ngoài) | 0 |
| 6 | `d10.k1` | "khoản 2, 3, Điều này" | `d10.k2`, `d10.k3` | 2 |
| 7 | `d15.k1.a` | "Điều 11 của Quy chế này" | `d11` | 1 |
| 8 | `d21.k6` | "khoản 1, Điều này" | `d21.k1` | 1 |
| 9 | `d23.k3.c` | "khoản 1, Điều 21 của Quy chế này" | `d21.k1` | 1 |
| 10 | `d29.k3` | "khoản 2 Điều này" (không có dấu phẩy) | `d29.k2` | 1 |
| 11 | `d29.k3.b` | "Điều 22" (không có "của Quy chế này") | `d22` | 1 |
| 12 | `d29.k3.b` | "Điều 24 của Quy chế này" | `d24` | 1 |
| 13 | `d36.k3.d` (sau S1: `d36.k3`) | "điểm a, b,c khoản 3, Điều này" | `d36.k3.a`, `.b`, `.c` | 3 |
| 14 | `d38.k8` | "khoản 2, Điều này" | `d38.k2` | 1 |
| 15 | `d39.k2` | "khoản 2, Điều 41 của Quy chế này" | `d41.k2` | 1 |
| 16 | `d41.k4` | "khoản 2, Điều này" | `d41.k2` | 1 |
| 17 | `d44.k2.b` | "khoản 1, Điều 17 của Quy chế này" | `d17.k1` | 1 |
| 18 | `d44.k2.c` | "điểm d, khoản 4, Điều 35 của Quy chế này" | `d35.k4.d` | 1 |
| 19 | `d44.k2.đ` | "khoản 3, Điều 36 của Quy chế này" | `d36.k3` | 1 |
| 20 | `d46.k1.đ` | "khoản 3, Điều 13 của Quy chế này" | `d13.k3` | 1 |
| 21 | `d46.k2` | "Điều 22" ("như quy định ở Điều 22 để xem xét…") | `d22` | 1 |
| 22 | `d50.k3.đ` | "khoản 2, 3, Điều 10 của Quy chế này" | `d10.k2`, `d10.k3` | 2 |

Tổng: 22 cụm = 21 nội bộ (**31 cạnh**) + 1 ra ngoài. Cụm 11 và 21 không có "của Quy chế này" nhưng vẫn là nội bộ, nên regex phải nhận cả trường hợp "không có tên văn bản khác theo sau" (mục 1.3).

## Lịch sử

| Ngày | Phiên bản | Thay đổi |
| --- | --- | --- |
| 17/10/2026 | v1 khung | Tạo khung; điền lớp cấu trúc theo PLAN + parser |
| 17/10/2026 | v1 đề xuất | Đề xuất phần 2 (chưa đối chiếu văn bản) |
| 17/10/2026 | v1 sửa | Đối chiếu toàn văn 51 điều và PDF: sửa ngưỡng thành khoảng; bỏ "mặc định AND", thêm `NhomDieuKien`; thêm `GIAO_QUY_DINH`, `TINH_VAO`, `DUNG_DE`, `XET`, điều kiện phủ định; thêm S1–S2 (lỗi cấu trúc), F10–F11; sửa câu thử 1, 3, 4, 5, 6; thêm câu 7–10; thêm Q8–Q11 |
| 18/10/2026 | v1 sửa sau rà soát | `TINH_VAO` thêm `boi_canh`; mức có thứ bậc thêm `thang`/`thu_tu` (bảng 2.1a), sửa mẫu B và truy vấn 2.4 cho mọi năm, thêm ví dụ `d44.k1.a` "các năm tiếp theo", `d27.k4.a` "từ B trở lên"; thay `Khoan.doan_sau` bằng `doan_khong_so` cho `Khoan`/`Dieu`; thêm quy tắc chấm, đổi câu 3b, quy `source_ids` về `:DonViNoiDung`; trích lại nguyên văn `d44.k2.c`, `d38.k9`; sửa câu Neo4j ở 2.0; thêm Phụ lục A; mục 5: sửa PLAN, mở rộng parser, sửa bảng dàn dòng, chạy thử T4 trên 5 điều |
| 18/10/2026 | v1 sửa sau kiểm tra (Cowork) | Đồng ý 3 thay đổi kéo theo (`d35` gắn `:DonViNoiDung`, 192 đơn vị; bước sửa PLAN ở mục 5; thang `hoc_luc`). Câu 3b: `source_ids` = `d5.k2`, `d21.k2`, `d41.k2`, `d42.k5`, đáp án thêm học phần P và học phần chưa đạt, `d43.k2` chuyển sang tra cứu; thêm thang `hang_tot_nghiep` (bảng 2.1a, `d47.k7` = `thu_tu − 1`); ghi chú cột "Ví dụ thật" là `ten` dạng chuẩn |