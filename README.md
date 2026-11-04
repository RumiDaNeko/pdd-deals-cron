# pdd-deals-cron

Repo này chỉ có một nhiệm vụ: cứ 30 phút một lần, nó gọi Worker **pdd-deals** trên Cloudflare để Worker kiểm tra deal Pinduoduo mới và báo qua Telegram.

Lý do phải tách ra đây: account Cloudflare đã dùng hết 5 cron trigger của gói Free. GitHub Actions trên repo public thì chạy miễn phí.

## Cách hoạt động

```
GitHub Actions (*/30 * * * *)
  └─ POST https://pdd-deals.harumi.workers.dev/run   (Authorization: Bearer RUN_TOKEN)
       └─ Worker đọc deal Pinduoduo từ smzdm.com, lọc theo danh sách theo dõi,
          gửi deal mới qua Telegram, nhớ deal đã gửi trong KV
```

Muốn xem deal trực tiếp, mở https://pdd-deals.harumi.workers.dev/?q=蓝牙耳机&max=100. Từ khoá viết bằng tiếng Trung, `max` là giá tối đa tính bằng tệ.

## Cấu hình

| Ở đâu | Tên | Ghi chú |
|---|---|---|
| Secret của repo | `RUN_TOKEN` | Phải trùng với secret `RUN_TOKEN` của Worker. Sai token thì Worker trả 403 và workflow báo lỗi. |

Danh sách từ khoá theo dõi (`WATCHES`) và token Telegram được cấu hình ở phía Worker, không nằm trong repo này.

## Chạy tay

Vào tab **Actions**, chọn **check-deals**, rồi bấm **Run workflow**. Hoặc chạy:

```bash
gh workflow run check-deals.yml
```

## Lưu ý

- GitHub có thể chạy lịch trễ vài phút khi hệ thống đông. Repo public không có hoạt động gì trong 60 ngày cũng sẽ bị tắt lịch chạy; khi đó vào Actions bật lại là được.
- Đổi token: tạo token mới, chạy `wrangler secret put RUN_TOKEN` cho Worker, rồi cập nhật secret `RUN_TOKEN` của repo này.
