# pdd-deals-cron

Phần việc nặng của bot săn deal [@pdd_discounts_bot](https://t.me/pdd_discounts_bot). Cứ 10 phút một lần, GitHub Actions:

1. **Crawl** deal mới trên 6 sàn Trung Quốc (Pinduoduo, Tmall, JD, Taobao, Douyin, Vipshop) từ API của smzdm.com, tối đa 10 trang mỗi sàn. Có khoảng 900 deal mỗi giờ.
2. **Dịch** tên sản phẩm sang tiếng Việt qua endpoint dịch công khai của Google (`translate.googleapis.com`, không cần key), 40 tên mỗi request. Đây là endpoint không chính thức: nếu bị chặn, deal vẫn được gửi với tên tiếng Trung. (Đã thử opus-mt-zh-vi, dịch bắc cầu zh→en→vi và NLLB-200 chạy trên GitHub: tên sản phẩm bị dịch sai hoặc lặp chữ, nên bỏ.)
3. **Đẩy** từng gói 10 deal sang Worker `pdd-deals` trên Cloudflare. Worker lọc theo bộ lọc của từng người đăng ký rồi gửi album Telegram.

Tách ra đây vì Cloudflare Workers gói Free chỉ cho 50 request ra ngoài mỗi lần chạy; GitHub Actions trên repo public thì miễn phí và không giới hạn thời gian chạy.

## Cách hoạt động

```
GitHub Actions (*/10 * * * *)  crawl.py
  ├─ GET  /cursor  → mốc thời gian deal cuối cùng đã gửi
  ├─ crawl smzdm (7 mall id, nhiều trang) → deal mới hơn mốc đó
  ├─ dịch tiêu đề (Google translate, 40 tên/request)
  └─ POST /send  (10 deal/lần, Authorization: Bearer RUN_TOKEN)
        └─ Worker: lọc theo từng người (danh mục, từ khoá, giá, sàn) → album Telegram
           cập nhật mốc thời gian khi gói đó đã tới đủ mọi người
```

Chạy thử ở máy, không gửi gì:

```bash
python crawl.py --dry-run --no-translate
```

## Cấu hình

| Ở đâu | Tên | Ghi chú |
|---|---|---|
| Secret của repo | `RUN_TOKEN` | Phải trùng với secret `RUN_TOKEN` của Worker. Sai token thì Worker trả 403 và workflow báo lỗi. |

Bộ lọc của từng người, token Telegram và danh sách người đăng ký nằm ở phía Worker (D1), không nằm trong repo này.

## Chạy tay

Vào tab **Actions**, chọn **check-deals**, rồi bấm **Run workflow**. Hoặc chạy:

```bash
gh workflow run check-deals.yml
```

## Lưu ý

- GitHub có thể chạy lịch trễ vài phút khi hệ thống đông. Repo public không có hoạt động gì trong 60 ngày cũng sẽ bị tắt lịch chạy; khi đó vào Actions bật lại là được.
- Đổi token: tạo token mới, chạy `wrangler secret put RUN_TOKEN` cho Worker, rồi cập nhật secret `RUN_TOKEN` của repo này.
