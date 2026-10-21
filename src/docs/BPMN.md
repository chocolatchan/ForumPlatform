# Forum Platform - Business Process Model (BPMN) Status

**Phiên bản:** 1.1 (Task 002 Baseline)  
**Trạng thái:** Formal Executable BPMN 2.0 XML Engine Deferred to MVP+; Canonical Process Flows Maintained via PlantUML Activity Diagrams

---

## 1. Quyết định phạm vi BPMN cho MVP

Trong giai đoạn MVP, hệ thống không tích hợp một workflow engine runtime cồng kềnh (như Camunda hay Zeebe). Thay vào đó:
1. Toàn bộ các quy trình nghiệp vụ (Business Processes) cốt lõi được mô hình hóa và duy trì chuẩn xác bằng **PlantUML Activity Diagrams** tại [`forum_activity_diagrams.puml`](file:///home/chocolat/Documents/Project/ForumPlatform/src/docs/forum_activity_diagrams.puml).
2. Việc xuất sang sơ đồ BPMN 2.0 XML chuẩn để phục vụ kiểm toán quy trình doanh nghiệp được định vị cho giai đoạn MVP+.

---

## 2. Bảng ánh xạ quy trình nghiệp vụ (Business Process Mapping)

| Mã quy trình | Tên quy trình | Service điều phối | Tài liệu mô hình hóa | Trạng thái triển khai |
|---|---|---|---|---|
| **BP-01** | Đăng ký tài khoản với xác thực OTP (DEC-001) | User Service | `Forum_Activity_01_Registration_OTP` | Chuẩn hóa, neutral 202 response |
| **BP-02** | Đăng nhập, quản lý session và chống brute-force | User Service, Gateway | `Forum_Activity_02_Login_Session` | Rate limiting, opaque cookie session |
| **BP-03** | Soạn thảo và xuất bản bài viết (kèm ảnh) (DEC-003) | Content Service | `Forum_Activity_03_Create_Thread` | Outbox event, attachment validation |
| **BP-04** | Bình luận và phản hồi phân cấp (tối đa 2 cấp) | Content Service | `Forum_Activity_04_Comment_Reply` | Thread lock check, in-app notification |
| **BP-05A** | Đánh giá nội dung (Vote & Reputation Ledger) | Interaction Service | `Forum_Activity_05_Vote_BestAnswer` | Idempotent vote, net ledger delta |
| **BP-05B** | Chọn câu trả lời hay nhất (Best Answer Q&A) | Content Service | `Forum_Activity_05B_Mark_Best_Answer` | Owner verification, reputation reward |
| **BP-06A** | Báo cáo vi phạm nội dung & tài khoản (DEC-004) | Moderation Service | `Forum_Activity_06_Report_Moderation` | Targets: thread, comment, account |
| **BP-06B** | Quy trình thẩm định và xử lý kiểm duyệt của Moderator | Moderation Service | `Forum_Activity_06B_Moderator_Resolution` | Correlation with report_id, outbox event |
| **BP-07** | Tìm kiếm bài viết toàn văn (FTS) | Search Service | `Forum_Activity_07_Search` | Opaque cursor, visibility check |
| **BP-08** | Khử trùng lượt xem và đồng bộ View Count (DEC-005) | Interaction -> Content | `Forum_Activity_08_View_Count_Propagation` | 30m window, Transactional Outbox |

---

## 3. Tiêu chí chuyển tiếp sang Formal BPMN 2.0

Khi hệ thống mở rộng quy trình kiểm duyệt đa bước (multi-stage moderation), kháng cáo (appeal workflows), hoặc tích hợp KYC doanh nghiệp:
- BPMN 2.0 XML definitions sẽ được lưu trữ tại `src/docs/bpmn/*.bpmn`.
- File này sẽ đóng vai trò mục lục và chỉ dẫn tích hợp cho engine điều phối quy trình nghiệp vụ.
