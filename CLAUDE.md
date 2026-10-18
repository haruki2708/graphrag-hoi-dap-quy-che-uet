# CLAUDE.md

Dự án: chatbot GraphRAG hỏi đáp quy chế đào tạo Trường ĐH Công nghệ (UET – ĐHQGHN). Đồ án môn Dự án, 10 tuần (17/10–25/12/2026).

**Đọc `docs/PLAN.md` trước khi làm bất cứ việc gì.** Đó là nguồn sự thật về phạm vi, kiến trúc, kế hoạch tuần và tiêu chí hoàn thành.

## Tuần hiện tại

T1 (17–23/10/2026) — xem dòng T1 trong `docs/PLAN.md` mục 5. Chủ dự án sẽ tự cập nhật dòng này mỗi tuần.

## Đầu mỗi phiên làm việc

1. Đọc `docs/weekly/wN.md` của tuần hiện tại, nhất là phần việc còn lại.
2. Chạy `git status`; có thay đổi chưa commit hoặc `main` lệch `origin/main` thì báo trước.
3. Báo ngắn: đang tuần mấy, việc nào xong, việc nào còn. Sau đó mới làm.

Schema đồ thị: `docs/schema_v1.md`. Mục nào ở cột "Quyết định" (S1–S5, Q2–Q11) còn trống thì không code theo mục đó; nêu phương án và hỏi.

## Quy tắc bắt buộc

- Trả lời chủ dự án bằng tiếng Việt. Comment, docstring, commit message bằng **tiếng Anh**. Tên biến, hàm, file bằng tiếng Anh, trừ thuật ngữ của văn bản quy chế (VanBan, Dieu, Khoan, Diem, DAN_CHIEU, doan_khong_so…) giữ tiếng Việt không dấu theo `docs/schema_v1.md` — không đổi tên các thuật ngữ này. Tài liệu trong `docs/` và `README.md` viết tiếng Việt. Comment tiếng Việt cũ: chỉ dịch khi đang sửa file đó, không tạo commit riêng để dịch.
- **Hỏi trước khi**: cài hoặc gỡ thư viện, tạo/sửa `docker-compose.yml`, xóa hay đổi tên file, chạy lệnh git làm thay đổi lịch sử (reset, rebase, force), gọi API trả phí trên toàn bộ dữ liệu.
- **Không tự `git push`**, không tự tạo tag. Khi một phần đã xong và đã kiểm chứng: ghi rõ "phần này push được", liệt kê file nên `git add` và đề xuất commit message dạng `loại(phần): mô tả` (feat, fix, docs, eval, chore).
- Không bao giờ ghi API key vào code hay commit `.env`. Biến môi trường khai báo trong `.env.example` (không có giá trị thật).
- Các quyết định ở `docs/PLAN.md` mục 4 đang **chưa chốt**: không tự chọn. Nêu phương án + khuyến nghị và hỏi.
- Chỉ báo "xong" sau khi đã chạy và thấy kết quả khớp tiêu chí "Xong khi" của tuần. Chưa đạt thì nói rõ cái gì chưa đạt, cái gì bị bỏ qua.
- Gọi LLM trả phí: chạy thử trên vài điều trước, cache kết quả vào `data/cache/` (phải có trong `.gitignore`), ước lượng chi phí trước khi chạy toàn bộ.
- Tập `test` của bộ đánh giá chỉ được chạy ở T9. Mọi tinh chỉnh dùng tập `dev`.
- Không dùng dữ liệu sinh viên thật.

## Cuối mỗi phiên làm việc

Cập nhật `docs/weekly/wN.md` (N = tuần hiện tại): đã làm gì, số đo (nếu có), vướng mắc, việc tiếp theo.

## Cách chạy

Chi tiết trong `README.md`. Tóm tắt (từ thư mục gốc repo, Python 3.12, venv tại `.venv/`):

- Neo4j: `docker compose up -d` (Neo4j 5.26 Community + APOC; Browser http://localhost:7474)
- Kiểm tra kết nối: `.venv/Scripts/python scripts/check_neo4j.py` → `RETURN 1 -> 1`
- Tách văn bản: `.venv/Scripts/python src/ingest/parse_regulation.py` → `data/processed/qc3626.json` (51 điều, báo `ĐẠT`)
