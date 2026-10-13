# Thư mục `vasp_sql/llm/` — Kết nối & Quản lý Mô hình Ngôn ngữ Lớn

Thư mục này chịu trách nhiệm quản lý việc giao tiếp với các mô hình ngôn ngữ lớn (LLM) qua giao thức chuẩn OpenAI API, hỗ trợ Gate F và cơ chế sửa lỗi ngữ nghĩa Hướng 2.

---

## Chi tiết các file và nhiệm vụ

### 1. `client.py` — `LLMClient`
* **Nhiệm vụ:** Client gọi LLM tối ưu, độc lập và nhẹ nhàng (chỉ sử dụng thư viện chuẩn `urllib`, không phụ thuộc vào gói `openai` nặng).
* **Các tính năng nổi bật:**
  * **Cấu hình động:** Tự động đọc biến môi trường từ file `.env` (`VT_BASE_URL`, `VT_API_KEY`, `VT_MODEL_LLM`, `VT_MODEL_JUDGE`).
  * **Tương thích LiteLLM / Qwen:** Tự động trích xuất nội dung từ cả hai trường `content` và `reasoning_content` (xử lý trường hợp mô hình suy luận đưa câu trả lời vào reasoning stream).
  * **Bộ nhớ đệm trên đĩa (Disk Caching):** Mọi yêu cầu và phản hồi được lưu vết trong file đệm JSONL theo mã băm SHA-256 của prompt. Khi chạy lại cùng một câu hỏi/thực nghiệm, hệ thống sẽ đọc trực tiếp từ cache mà không tốn thêm token và chi phí API và kết quả có thể kiểm tra lại (reproducibility).
  * **Theo dõi & Kiểm soát tài nguyên:** Tự động đếm số lượt gọi API, độ trễ thời gian và kích thước phản hồi để phục vụ báo cáo khoa học.
  * **Cơ chế Retry thông minh:** Tự động thử lại khi gặp sự cố mạng hoặc lỗi quá tải tạm thời với số lần thử cấu hình được (`max_retries`).
