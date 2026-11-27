# Bộ chuẩn dóng tay (han-nghia)

Mỗi file được chép nguyên từ `assignment_submission/align50/<work>/`. Nội dung không sửa (sha1 trùng bản gốc), chỉ
đổi tên theo dạng `<work>_p<trang đầu>-<trang cuối>_<người dóng>.xlsx`. Khoảng trang lấy theo ô "Khoảng trang PDF"
trong sheet `HUONG_DAN` của từng file.

| File | Nguồn | Người dóng | Trang PDF | Hàng Hán | sha1 |
|---|---|---|---|---|---|
| `CPO_18_p205-254_Quan.xlsx` | `align50/CPO_18/align50_CPO_18_Quan.xlsx` (commit `84e79d2`) | Nguyễn Minh Quân | 205–254 | 137 | `0d9d3b65` |
| `CPO_19_p14-63_Dat.xlsx` | `align50/CPO_19/align50_CPO_19_Dat.xlsx` (trước tên `..._dryrun.xlsx`; = `data/CPO_19/07_export/align50_CPO_19_dryrun.xlsx`, commit `dc05a52`) | Đạt (theo tên file; ô "Thành viên" trong file chưa điền) | 14–63 | 233 | `db0baf38` |
| `CPO_20_p5-56_Nhi.xlsx` | `align50/CPO_20/align50_CPO_20_Nhi.xlsx` (commit `e9c3d94`) | Nguyễn Thị Yến Nhi | 5–56 | 225 | `404af4d7` |

- Ngày 2026-11-28, người dùng đổi tên file CPO_19 từ `dryrun` sang `Dat`, xác nhận đây là bản chốt. Nội dung không
  đổi (cùng sha1).
- `scripts/eval_han_nghia.py --gold` (không kèm giá trị) đọc `eval/gold/{work}_*.xlsx`.
- Script đọc sheet `DONG_HANG_LAM_VIEC`, cột ID, Trang, Hán, Dịch nghĩa.
- Khi thành viên sửa file trong `align50/`, chép lại vào đây và chạy lại eval. Không chép file khoá `~$` của Excel.
