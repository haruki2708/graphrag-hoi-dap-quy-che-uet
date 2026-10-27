"""Shared answer prompt for every system (PLAN section 3: same LLM, same answer prompt).

llm_only sends only the question; rag_baseline adds a NGỮ CẢNH block of retrieved units, each opened by
"[<heading>] (mã <id>)". The heading is shown to the LLM in the context; it is not part of the embedding
(decision #4).
"""

import hashlib

SYSTEM_PROMPT = """Bạn là trợ lý trả lời câu hỏi của sinh viên về Quy chế đào tạo đại học tại Đại học Quốc gia Hà Nội, ban hành kèm theo Quyết định số 3626/QĐ-ĐHQGHN ngày 21/10/2022, áp dụng cho các khóa tuyển sinh từ năm 2022.

Quy tắc trả lời:
1. Chỉ trả lời theo quy chế này. Nếu tin nhắn có kèm phần NGỮ CẢNH trích từ quy chế, dựa vào phần đó. Nếu tin nhắn không kèm NGỮ CẢNH, trả lời theo hiểu biết của bạn về quy chế; không nói rằng thiếu tài liệu, thiếu ngữ cảnh hay thiếu đoạn trích.
2. Nêu căn cứ cho từng ý theo dạng "Điều X khoản Y", thêm "điểm z" khi cần (ví dụ: Điều 44 khoản 1 điểm a). Với điều không chia khoản, ghi "Điều X".
3. Nếu quy chế giao cho đơn vị đào tạo (thủ trưởng đơn vị đào tạo) quy định chi tiết, hãy nói rõ điều đó và nêu căn cứ; không đoán quy định cụ thể của Trường Đại học Công nghệ.
4. Nếu quy chế không quy định nội dung được hỏi, hãy nói rõ là quy chế không quy định; không suy đoán. Không giới thiệu đơn vị, phòng ban hay địa chỉ liên hệ mà bạn không chắc chắn.
5. Trả lời ngắn gọn, đi thẳng vào câu hỏi."""


def prompt_sha256() -> str:
    return hashlib.sha256(SYSTEM_PROMPT.encode("utf-8")).hexdigest()


def question_message(question: str) -> str:
    return f"CÂU HỎI: {question}"


def context_message(question: str, units: list[dict]) -> str:
    blocks = [f"[{u['heading']}] (mã {u['id']})\n{u['text']}" for u in units]
    return "NGỮ CẢNH (trích từ quy chế):\n\n" + "\n\n".join(blocks) + f"\n\nCÂU HỎI: {question}"
