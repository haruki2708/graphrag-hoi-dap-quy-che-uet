"""Kiểm tra kết nối Neo4j: đọc thông tin từ .env, chạy `RETURN 1` và in kết quả.

Chạy từ thư mục gốc repo:  python scripts/check_neo4j.py
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase
from neo4j.exceptions import AuthError, ServiceUnavailable

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_VARS = ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD")


def main() -> int:
    # Console Windows có thể không dùng UTF-8 → ép để in được tiếng Việt
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    load_dotenv(REPO_ROOT / ".env")
    missing = [name for name in REQUIRED_VARS if not os.getenv(name)]
    if missing:
        print(f"Thiếu biến trong .env: {', '.join(missing)}", file=sys.stderr)
        return 1

    uri = os.environ["NEO4J_URI"]
    auth = (os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"])

    try:
        with GraphDatabase.driver(uri, auth=auth) as driver:
            driver.verify_connectivity()
            records, _, _ = driver.execute_query("RETURN 1 AS result")
            result = records[0]["result"]
    except AuthError:
        print("Sai tên người dùng hoặc mật khẩu Neo4j.", file=sys.stderr)
        return 1
    except ServiceUnavailable:
        print(f"Không kết nối được {uri}. Neo4j đã chạy chưa?", file=sys.stderr)
        return 1

    print(f"Kết nối {uri} thành công. RETURN 1 -> {result}")
    return 0 if result == 1 else 1


if __name__ == "__main__":
    sys.exit(main())
