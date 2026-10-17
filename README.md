# GraphRAG hỏi đáp quy chế đào tạo UET

Chatbot trả lời câu hỏi về quy chế đào tạo Trường ĐH Công nghệ (UET – ĐHQGHN), dùng đồ thị tri thức (GraphRAG). Kế hoạch chi tiết: [docs/PLAN.md](docs/PLAN.md).

## Yêu cầu

- Python 3.12
- Docker Desktop (có Docker Compose)

Mọi lệnh dưới đây chạy từ thư mục gốc của repo. Lệnh nào khác nhau giữa PowerShell và Git Bash thì có ghi riêng.

## 1. Môi trường Python

Tạo venv (giống nhau ở cả hai shell; `py` là Python Launcher của Windows, chọn đúng bản 3.12):

```
py -3.12 -m venv .venv
```

Kích hoạt venv:

```powershell
# PowerShell
.venv\Scripts\Activate.ps1
```

```bash
# Git Bash
source .venv/Scripts/activate
```

> PowerShell báo lỗi "running scripts is disabled": chạy một lần
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` rồi kích hoạt lại.

Cài thư viện (giống nhau ở cả hai shell):

```
pip install -r requirements.txt
```

## 2. Tạo file .env

```powershell
# PowerShell
Copy-Item .env.example .env
```

```bash
# Git Bash
cp .env.example .env
```

Mở `.env` và điền:

```
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=<mật khẩu tối thiểu 8 ký tự>
```

`NEO4J_USER` phải là `neo4j` (Neo4j Community không cho đổi tên người dùng ban đầu). File `.env` đã nằm trong `.gitignore`, không commit.

## 3. Chạy Neo4j

```
docker compose up -d
```

Lần đầu Docker tải image `neo4j:5.26-community` và plugin APOC, mất vài phút. Xem log cho đến khi có dòng `Started.`:

```
docker compose logs -f neo4j
```

Mở Neo4j Browser tại <http://localhost:7474>, đăng nhập bằng `NEO4J_USER` / `NEO4J_PASSWORD` trong `.env`.

Dữ liệu lưu ở `./neo4j/` (đã gitignore). Dừng: `docker compose down` (dữ liệu vẫn giữ).

> Mật khẩu chỉ được áp dụng khi tạo CSDL lần đầu. Muốn đổi mật khẩu trong `.env` thì phải
> `docker compose down`, xóa thư mục `neo4j/data`, rồi `docker compose up -d` lại (mất dữ liệu).

## 4. Kiểm tra kết nối

Với venv đã kích hoạt:

```
python scripts/check_neo4j.py
```

Kết quả mong đợi:

```
Kết nối bolt://localhost:7687 thành công. RETURN 1 -> 1
```

## 5. Tách văn bản quy chế

Đặt PDF (có lớp chữ) vào `data/raw/core/`, rồi với venv đã kích hoạt:

```
python src/ingest/parse_regulation.py
```

Kết quả: `data/processed/<mã văn bản>.json` (ví dụ `qc3626.json`) gồm các đơn vị chương / điều / khoản / điểm,
mỗi đơn vị có mã ổn định (`qc3626.d12.k2`), số trang và nguyên văn. Script in danh sách điều theo chương
và báo `ĐẠT` khi đủ 9 chương, Điều 1→51 liên tiếp.

Công thức và bảng bị vỡ khi trích chữ được sửa tay trong `data/raw/core/<mã văn bản>.overrides.json`
(cặp `find`/`replace` kèm số trang và lý do). Script cảnh báo nếu còn ký tự lạ chưa được sửa.

## Cấu trúc thư mục

Xem [docs/PLAN.md](docs/PLAN.md) mục 7.
