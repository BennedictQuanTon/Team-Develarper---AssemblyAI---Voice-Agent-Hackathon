# 🧠 Báo Cáo Đo Lường Benchmark: Local LLM (qwen2.5:3b) vs Cloud Gemini

> **Mô hình thử nghiệm**: `qwen2.5:3b` chạy trực tiếp qua Ollama trên máy cục bộ  
> **Kiến trúc**: Local Function Calling Loop (0ms Network latency, Không bị giới hạn 15 RPM)  
> **Dữ liệu JSON chi tiết**: [`reports/benchmark_local_qwen_eval.json`](file:///Users/davark/Downloads/Everything/Github/Team-Develarper---AssemblyAI---Voice-Agent-Hackathon/reports/benchmark_local_qwen_eval.json)

---

## 🚀 1. Bảng So Sánh Hiệu Năng: Local Qwen vs Cloud Gemini

| Chỉ số hiệu năng | Cloud Gemini (Có Token Bucket 15 RPM) | Local qwen2.5:3b (Không Rate Limit) | Mức độ cải thiện |
| :--- | :---: | :---: | :---: |
| **Độ trễ suy luận LLM (LLM Inference)** | **~2,700 – 5,500 ms / vòng** | **2022.5 ms (~2.02s)** | **Nhanh hơn 2.0 lần** |
| **Hàng đợi Token Bucket (Phase 5)** | **4,000 – 8,000 ms (Bắt chờ)** | **0 GIÂY (Xóa bỏ 100%)** | **Tiết kiệm 4–8 giây!** |
| **Độ trễ cảm nhận (Perceived TTFB)** | **~1,000 ms** | **800.0 ms** | Tức thì (~0.8s) |
| **Độ trễ trả lời thật (Actual TTFB)** | **~11,634 ms** | **6552.9 ms (~6.55s)** | **Nhanh hơn 1.8 lần** |
| **Tổng thời gian mỗi Turn (Avg E2E)** | **12,411 ms (12.4s)** | **6553.5 ms (~6.55s)** | **Nhanh hơn 1.9 lần!** |
| **Nguy cơ lỗi Rate Limit 429** | Thường trực nguy cơ | **0% (Infinite Quota)** | Tuyệt đối an toàn |

---

## 🎭 2. Chi Tiết Từng Lượt Thoại

### 🔹 Turn 1: Phase 1: Specialty Inquiry
* **Khách nói**: *"What are your house specialties here?"*
* **Thời gian suy luận của Qwen**: **2312.88 ms**
* **Full E2E Turn 1**: **8220.37 ms**
* **Nội dung AI trả lời**: *"Grilled Seabass and Grilled River Prawns are our house specialties."*

### 🔹 Turn 2: Phase 2: Order & Modifier & Place
* **Khách nói**: *"I'll take the first one with no green onions, please place the order."*
* **Thời gian suy luận của Qwen**: **1732.06 ms**
* **Full E2E Turn 2**: **4886.59 ms**
* **Công cụ đã gọi**: `['add_item', 'readback']`
* **Nội dung AI trả lời**: *"Order readback: 1x Grilled Seabass (no green onions) - $16.00. Place order now?"*
