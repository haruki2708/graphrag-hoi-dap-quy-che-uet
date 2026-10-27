# PLAN — GraphRAG hỏi đáp quy chế đào tạo UET

> Bản kế hoạch dành cho Claude Code. Tiến độ và quyết định được theo dõi chính trên Notion của chủ dự án.
> Cập nhật lần cuối: 27/10/2026 (chốt #10 bộ câu hỏi; schema S1, S2, Q11 = B: vector index trên `:DonViNoiDung`, trường câu hỏi mới). Khi chủ dự án chốt một quyết định ở mục 4, sửa file này trước rồi mới code theo.

## 1. Mục tiêu

Chatbot trả lời câu hỏi tiếng Việt về quy chế đào tạo của Trường ĐH Công nghệ (UET – ĐHQGHN).
Ngữ cảnh cho LLM được lấy từ **đồ thị tri thức** dựng từ văn bản quy chế (GraphRAG), không chỉ từ đoạn văn giống câu hỏi.
Phải **chứng minh bằng số liệu** đồ thị giúp trả lời tốt hơn RAG thường.

Mỗi câu trả lời gồm: (1) câu trả lời ngắn; (2) căn cứ "Điều X, khoản Y, [tên văn bản]"; (3) đoạn trích nguyên văn.
Không tìm thấy căn cứ → nói rõ là không tìm thấy, không đoán.

## 2. Phạm vi dữ liệu

| Văn bản | Trạng thái |
| --- | --- |
| Quy chế đào tạo đại học ĐHQGHN, QĐ 3626/QĐ-ĐHQGHN ngày 21/10/2022 — 9 chương, 51 điều | Văn bản lõi, làm trước. Chưa kiểm tra văn bản sửa đổi sau 2022 |
| Quy định, hướng dẫn riêng của UET (học bổng, tốt nghiệp, POHE…) | Chưa có danh sách. Chỉ thêm khi pipeline đã ổn và còn thời gian |
| Thông tư 08/2021/TT-BGDĐT (quy chế đào tạo đại học của Bộ) | Tùy chọn, tuần 5 |

**Không dùng dữ liệu sinh viên thật** (điểm, họ tên, mã SV). Cần demo cá nhân hóa → dữ liệu giả lập.

## 3. Kiến trúc

Luồng A — dựng chỉ mục (chạy một lần):
1. `src/ingest/`: đọc PDF/DOCX → chuẩn hóa Unicode (NFC) → tách chương / điều / khoản / điểm bằng regex → `data/processed/*.json`, mỗi nút có mã ổn định (ví dụ `qc3626.d12.k2`).
2. `src/kg/` lớp cấu trúc (quy tắc, không dùng LLM): nút `VanBan`, `Chuong`, `Dieu`, `Khoan`; cạnh `THUOC`, `DAN_CHIEU` (bắt "theo quy định tại Điều X" bằng regex), `SUA_DOI`.
3. `src/kg/` lớp ngữ nghĩa (LLM, theo schema đã duyệt): khái niệm, đối tượng, điều kiện, ngưỡng, hệ quả; mỗi thực thể/quan hệ giữ liên kết về `Khoan` nguồn. Có bước gộp thực thể trùng.
4. Vector index trên các đơn vị nội dung `:DonViNoiDung` (192 đơn vị = 179 `Khoan` + phần dẫn của 12 `Dieu` + `d35`; `docs/schema_v1.md` S2), không chỉ `Khoan` (và mô tả thực thể nếu cần).

Luồng B — hỏi đáp (mỗi câu hỏi):
1. `src/retrieval/`: tìm điểm vào bằng vector (+ từ khóa) trên khoản/thực thể.
2. Mở rộng 1–2 bước theo cạnh trên đồ thị; Text2Cypher cho câu hỏi đếm/liệt kê.
3. `src/qa/`: LLM sinh câu trả lời từ ngữ cảnh đã gom, kèm trích dẫn mã khoản.

Ba hệ để so sánh (cùng LLM, cùng prompt sinh câu trả lời):
- `llm_only`: LLM không có tài liệu.
- `rag_baseline`: chỉ vector index trên `:DonViNoiDung` → sinh câu trả lời.
- `graphrag`: luồng B đầy đủ (hybrid vector + đồ thị).
- (tùy chọn) `lightrag`: LightRAG đóng gói sẵn.

## 4. Quyết định kỹ thuật

**Claude Code không tự chọn các mục chưa chốt.** Nếu việc đang làm phụ thuộc vào mục chưa chốt: dừng, nêu các phương án + khuyến nghị, hỏi chủ dự án.

| # | Quyết định | Khuyến nghị / lựa chọn | Trạng thái |
| --- | --- | --- | --- |
| 1 | Khung GraphRAG | Tự xây trên Neo4j: lớp cấu trúc bằng quy tắc; lớp ngữ nghĩa bằng prompt riêng theo `docs/schema_v1.md` (JSON ép schema, mỗi bộ ba gắn mã khoản nguồn), không dùng `SimpleKGPipeline`. Truy xuất và sinh câu trả lời dùng gói `neo4j-graphrag` (`VectorCypherRetriever`/`HybridCypherRetriever`, `Text2CypherRetriever`, lớp `GraphRAG`). LightRAG làm hệ so sánh nếu kịp (T7). Microsoft GraphRAG chỉ đọc paper | **Đã chốt** (18/10/2026, thầy đồng ý) |
| 2 | Nguồn LLM | Sinh câu trả lời (cả 3 hệ): Claude Haiku 5.5 (`claude-haiku-5-5`), effort `medium` cho lượt chính thức (chốt 27/10/2026, sau lượt thử 5 câu dev; mặc định của vai `answer` trong `configs/llm.toml`). Trích xuất KG: mặc định Haiku 5.5; đầu T4 so với Sonnet 5.5 (`claude-sonnet-5-5`) trên 5 điều rồi chọn theo tỷ lệ bộ ba đúng. Giám khảo: GLM-5.3-Flash (`z-ai/glm-5.3-flash`) qua NVIDIA NIM free, structured output; xác nhận chính thức sau hiệu chỉnh 30 câu (độ trùng với chấm tay ≥ ~85–90%). Dự phòng: cùng GLM-5.3-Flash trả phí qua Z.ai; sau đó Gemini 3.8/3.6 Flash. Mỗi lượt đánh giá chỉ dùng một giám khảo. Gọi LLM qua `src/llm/`, provider/model đọc từ cấu hình; ghi model ID + nhà cung cấp + ngày vào mọi file kết quả. Haiku 5.5 không nhận temperature/top_p/top_k → mỗi cấu hình đánh giá chạy 3 lần, báo trung bình | **Đã chốt** (18/10/2026; đổi giám khảo 24/10/2026) |
| 3 | CSDL đồ thị | Neo4j 5.26 Community (LTS) + APOC qua Docker; NetworkX chỉ là dự phòng | **Đã chốt** (17/10/2026) |
| 4 | Embedding | `BAAI/bge-m3`, revision `5617a9f61b028005a4858fdac845db406aefb181`; nhúng nguyên văn `text` của đơn vị, **không** ghép heading; không tiền tố truy vấn; cấu hình ở `configs/retrieval.toml`. Căn cứ: so 4 cấu hình trên dev v1, `eval/results/2026-10-27_embedding_compare_dev.json` | **Đã chốt** (27/10/2026) |
| 5 | Phạm vi văn bản | Linh hoạt theo tiến độ: làm QĐ 3626 trước, thêm văn bản khi pipeline đã ổn | **Đã chốt** |
| 6 | Giao diện | Streamlit | Chưa chốt |
| 7 | Thời lượng | 10 tuần, 17/10–25/12/2026 | **Đã chốt** |
| 8 | Ngôn ngữ code, comment, commit | Comment, docstring, commit message tiếng Anh. Tên biến/hàm/file tiếng Anh, trừ thuật ngữ quy chế giữ tiếng Việt không dấu theo `docs/schema_v1.md`. `docs/`, README, báo cáo và trao đổi bằng tiếng Việt. Comment tiếng Việt cũ chỉ dịch khi sửa file đó | **Đã chốt** (18/10/2026) |
| 9 | Nơi ghi nhật ký | Notion cho theo dõi; trong repo dùng `docs/weekly/wN.md` | Chưa chốt |
| 10 | Quy mô bộ câu hỏi | Khoảng 100 câu, đủ 4 loại. Tách dev/test ngay từ v1. Không dùng LLM (Claude, GPT) sinh câu hỏi hay gợi ý chủ đề. v1: `test` 20 câu `manual` do chủ dự án tự soạn, khóa ở commit `d9f9ddf`, chỉ chạy ở T9; `dev` = 30 câu `manual` + 11 câu thử ở `docs/schema_v1.md` mục 3 (`origin: generated`). T6 bổ sung đến ~100; câu `test` mới vẫn do chủ dự án tự soạn. Nếu sau này có câu LLM sinh thì chỉ vào `dev`. Kết quả trên `dev` báo tách theo `origin` | **Đã chốt** (27/10/2026) |

## 5. Kế hoạch 10 tuần

Đã chốt: T1 bắt đầu thứ Bảy 17/10/2026, T10 kết thúc 25/12/2026. Mỗi tuần từ thứ Bảy đến thứ Sáu.
"Xong khi" là tiêu chí kiểm chứng được — chỉ báo xong khi đã chạy và thấy kết quả.

| Tuần | Mục tiêu | Việc chính | Xong khi |
| --- | --- | --- | --- |
| T1 · 17–23/10 | Dữ liệu sạch, môi trường | Repo, `CLAUDE.md`, `docker-compose.yml` cho Neo4j; script đọc văn bản và tách chương/điều/khoản; schema v1 bản nháp (chủ dự án làm) | Script đếm đúng 51 điều, khớp mục lục; Neo4j Browser mở được; repo có commit; có bản nháp schema v1 |
| T2 · 24–30/10 | Baseline | `rag_baseline` (chunk theo `:DonViNoiDung`, top-k, trích dẫn); `llm_only`; so 2 embedding; bộ câu hỏi v1 (~70 câu, 4 loại: ~50 dev, ~20 test); script đánh giá | Script đánh giá chạy hết phần dev của bộ câu hỏi v1 cho 2 baseline, xuất CSV; có Recall@5 |
| T3 · 31/10–6/11 | Lớp cấu trúc | Schema (LLM gợi ý, chủ dự án duyệt); nạp VanBan–Chuong–Dieu–Khoan; cạnh DAN_CHIEU; vector index | Cypher đếm ra 51 điều; truy vấn "khoản nào dẫn chiếu Điều X" đúng trên 5 ví dụ kiểm tay |
| T4 · 7–13/11 | Lớp ngữ nghĩa | Trích thực thể–quan hệ theo schema, chạy thử 5 điều trước; gộp thực thể trùng; xuất mẫu 30 bộ ba để kiểm tay | Tỷ lệ bộ ba đúng ghi trong `docs/weekly/w4.md` |
| T5 · 14–20/11 | Pipeline GraphRAG | Điểm vào bằng vector; mở rộng 1–2 bước; Text2Cypher; hybrid | Trả lời hết tập dev không lỗi; mọi trích dẫn trỏ tới mã khoản có thật |
| T6 · 21–27/11 | Số liệu so sánh | Bộ câu hỏi v2 (~100 câu), tách dev/test; chạy các hệ trên dev; phân tích lỗi theo loại câu | Bảng kết quả theo 4 loại câu; 10 lỗi tiêu biểu có nguyên nhân |
| T7 · 28/11–4/12 | Phân tích lỗi, cải tiến | Sửa theo 10 lỗi tiêu biểu (prompt, schema, truy xuất); chạy lại trên dev; nếu còn thời gian: thêm văn bản hoặc chạy LightRAG | Bảng trước/sau trên dev; mỗi thay đổi có lý do và kết quả |
| T8 · 5–11/12 | Demo | UI Streamlit (chat, trích dẫn, đoạn gốc); README cài và chạy; đóng băng code cuối tuần | Người khác clone repo, làm theo README và chạy được demo |
| T9 · 12–18/12 | Kết quả cuối | Chạy tập test **đúng một lần**; hỗ trợ viết báo cáo | Kết quả test lưu kèm mã commit |
| T10 · 19–25/12 | Nộp | Báo cáo, slide, video demo; **không thêm tính năng** | Tag `v1.0` |

## 6. Đánh giá

- Bộ câu hỏi: `data/eval/questions_v*.jsonl`, mỗi dòng: `id`, `question`, `answer`, `source_ids`, `type` ∈ {`single`, `multi`, `conditional`, `out_of_scope`}, `split` ∈ {`dev`, `test`}, `delegated` (bool: nội dung quy chế giao cho đơn vị đào tạo quy định; `docs/schema_v1.md` Q11), `origin` ∈ {`manual`, `generated`}, `ref_points` (mã điểm cụ thể để tra cứu, không dùng để chấm).
- Quy tắc chấm (`docs/schema_v1.md` mục 3): `source_ids` ghi ở mức `:DonViNoiDung` (điểm quy về khoản cha; điều không có khoản, hoặc nội dung ở phần dẫn / `doan_khong_so` mức điều, thì tính là điều); chỉ ghi nguồn bắt buộc, tối đa 5. Mã do hệ thống trả về cũng quy về mức này trước khi chấm.
- Retrieval: Recall@5, Recall@10, MRR trên `source_ids`.
- Nguồn gốc câu và tách dev/test (quyết định #10): `test` do chủ dự án tự soạn (`origin: manual`), khóa từ lúc tách, chỉ chạy ở T9; không dùng LLM sinh câu hỏi; câu `generated` là 11 câu thử ở `docs/schema_v1.md` mục 3, chỉ vào `dev` (nếu sau này có câu LLM sinh cũng chỉ vào `dev`). Kết quả trên `dev` báo tách theo `origin`.
- Câu trả lời: độ chính xác (so đáp án, có thể LLM-as-judge), faithfulness, trích dẫn đúng, từ chối đúng với `out_of_scope`.
- Chỉ chỉnh prompt/tham số trên `dev`. `test` chỉ chạy ở T9.
- Mọi kết quả lưu `eval/results/<ngày>_<hệ>_<split>.csv` kèm commit hash và cấu hình.

## 7. Cấu trúc thư mục

```text
README.md, CLAUDE.md, .gitignore, .env.example, requirements.txt, docker-compose.yml
data/raw/          # văn bản gốc
data/processed/    # JSON đã tách điều khoản
data/eval/         # bộ câu hỏi
src/ingest/  src/kg/  src/retrieval/  src/qa/  src/app/  src/llm/
eval/              # script + results/
notebooks/
scripts/           # script tiện ích (kiểm tra kết nối, ...)
configs/           # cấu hình (llm.toml: provider/model cho từng vai)
tests/             # unit test (unittest; chạy: python -m unittest discover -s tests -t .)
docs/PLAN.md       # file này
docs/weekly/       # w1.md … w10.md
```
