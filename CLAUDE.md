# CLAUDE.md

Dự án: chatbot GraphRAG hỏi đáp quy chế đào tạo Trường ĐH Công nghệ (UET – ĐHQGHN). Đồ án môn Dự án, 10 tuần (17/10–25/12/2026).

**Đọc `docs/PLAN.md` trước khi làm bất cứ việc gì.** Đó là nguồn sự thật về phạm vi, kiến trúc, kế hoạch tuần và tiêu chí hoàn thành.

## Tuần hiện tại

T1 (17–23/10/2026) — xem dòng T1 trong `docs/PLAN.md` mục 5. Chủ dự án sẽ tự cập nhật dòng này mỗi tuần.

## Quy tắc bắt buộc

- Trả lời và viết comment, docstring, commit message mô tả bằng **tiếng Việt**; tên biến, hàm, file bằng tiếng Anh.
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

Chưa có — bổ sung khi có môi trường (T1).
