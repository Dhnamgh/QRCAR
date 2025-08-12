# streamlit_app.py — phiên bản đầy đủ đã sửa lỗi secrets/private_key & mở Sheet bằng KEY
import streamlit as st
import pandas as pd
import gspread
import qrcode
import io
import zipfile
import os
from oauth2client.service_account import ServiceAccountCredentials

st.set_page_config(page_title="Quản lý xe bằng QR", layout="wide")

# === KẾT NỐI GOOGLE SHEETS (đÃ SỬA LỖI) ===
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]

try:
    # tạo bản sao dict (st.secrets là read-only)
    creds_dict = dict(st.secrets["google_service_account"])
    # nếu private_key đang là chuỗi có ký tự \n, chuyển về xuống dòng thật
    pk = creds_dict.get("private_key", "")
    if "\\n" in pk:
        creds_dict["private_key"] = pk.replace("\\n", "\n")

    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, SCOPE)
    client = gspread.authorize(creds)
except Exception as e:
    st.error(f"❌ Lỗi khởi tạo Google Credentials: {e}")
    st.stop()

# === GOOGLE SHEET ID (dùng open_by_key thay vì open_by_url) ===
SHEET_ID = "18fQqPJ5F9VZdWvkuQq5K7upQHeC7UfZX"  # giữa /d/ và /edit
try:
    sheet = client.open_by_key(SHEET_ID).sheet1
except Exception as e:
    st.error(f"❌ Không mở được Google Sheet (ID={SHEET_ID}): {e}")
    st.info("🛠️ Kiểm tra: 1) Sheet đã share cho service account 2) Đúng SHEET_ID 3) Đã bật Sheets API/Drive API.")
    st.stop()

# === CẤU HÌNH APP ===
PASSWORD = "123456"
QR_LINK_PREFIX = "https://qrcarump.streamlit.app/?qr_id="   # domain của app
QR_FOLDER = "qr_images"
EXCEL_FILE = "thong_tin_xe.xlsx"
ZIP_FILE = "qr_all.zip"

# Map cột gốc (VN) -> tên ngắn dùng trong app
COLUMN_MAP = {
    'STT': 'stt',
    'Họ tên': 'ten',
    'Biển số': 'bsx',
    'Mã thẻ': 'qr_id',
    'Mã đơn vị': 'madonvi',
    'Tên đơn vị': 'tendonvi',
    'Chức vụ': 'chucvu',
    'Số điện thoại': 'dienthoai',
    'Email': 'email'
}

# === HÀM CHUẨN HÓA ===
def normalize_name(s: str) -> str:
    s = (s or "").strip().lower()
    if not s:
        return s
    parts = [p for p in s.split() if p]
    cap = []
    for p in parts:
        if len(p) == 0:
            continue
        first = p[0].upper().replace("Đ", "Đ").replace("đ", "Đ")
        rest = p[1:]
        cap.append(first + rest)
    return " ".join(cap)

def normalize_plate(bsx: str) -> str:
    if not bsx:
        return ""
    s = "".join(ch for ch in bsx.upper() if ch.isalnum())
    # Kỳ vọng dạng: 2 số + 1 chữ + còn lại là số. Ví dụ 51B01565 -> 51B-015.65
    if len(s) >= 6 and s[0:2].isdigit() and s[2].isalpha():
        head = s[:3]
        tail = "".join(ch for ch in s[3:] if ch.isdigit())
        if len(tail) == 5:
            return f"{head}-{tail[:3]}.{tail[3:]}"
        elif len(tail) == 6:
            return f"{head}-{tail[:3]}.{tail[3:]}"
        else:
            return f"{head}-{tail}"
    return bsx.upper()

# === HÀM LÀM VIỆC VỚI SHEET ===
def get_data() -> pd.DataFrame:
    data = sheet.get_all_records()
    df = pd.DataFrame(data)
    # rename an toàn: chỉ đổi những cột đang có
    rename_map = {k: v for k, v in COLUMN_MAP.items() if k in df.columns}
    df = df.rename(columns=rename_map)
    # đảm bảo có các cột chính
    for col in ['stt', 'ten', 'bsx', 'qr_id', 'madonvi', 'tendonvi', 'chucvu', 'dienthoai', 'email']:
        if col not in df.columns:
            df[col] = None
    return df

def append_row(stt, ten, bsx, qr_id, madonvi, tendonvi, chucvu="", dienthoai="", email=""):
    # thứ tự cột theo Google Sheet gốc (VN)
    row = [stt, ten, bsx, qr_id, madonvi, tendonvi, chucvu, dienthoai, email]
    sheet.append_row(row, value_input_option="USER_ENTERED")

# === HÀM QR / XUẤT FILE ===
def create_qr(data: str) -> io.BytesIO:
    qr_img = qrcode.make(data)
    buf = io.BytesIO()
    qr_img.save(buf, format='PNG')
    buf.seek(0)
    return buf

def create_qr_images(df: pd.DataFrame):
    if 'qr_id' not in df.columns:
        raise KeyError("Thiếu cột 'qr_id' trong dữ liệu")
    if not os.path.exists(QR_FOLDER):
        os.makedirs(QR_FOLDER)
    for _, row in df.iterrows():
        qr_id = str(row['qr_id'])
        if not qr_id:
            continue
        info_link = QR_LINK_PREFIX + qr_id
        img = qrcode.make(info_link)
        img.save(os.path.join(QR_FOLDER, f"qr_{qr_id}.png"))

def create_zip():
    with zipfile.ZipFile(ZIP_FILE, 'w') as zipf:
        for filename in os.listdir(QR_FOLDER):
            path = os.path.join(QR_FOLDER, filename)
            if os.path.isfile(path):
                zipf.write(path, arcname=filename)

def export_excel(df: pd.DataFrame):
    df.to_excel(EXCEL_FILE, index=False)

# === SIDEBAR ===
with st.sidebar:
    if os.path.exists("background.png"):
        st.image("background.png", width=200)
    menu = st.radio("📍 Chọn chức năng", [
        "📥 Tải dữ liệu và mã QR",
        "🆕 Đăng ký xe mới",
        "🔎 Tra cứu từ QR",
        "📚 Xem danh sách xe"
    ])

# === 1) TẢI DỮ LIỆU & MÃ QR ===
if menu == "📥 Tải dữ liệu và mã QR":
    df = get_data()
    export_excel(df)
    create_qr_images(df)
    create_zip()
    st.success("✅ Đã tạo Excel và toàn bộ mã QR (.zip)")
    st.download_button("⬇️ Tải Excel", open(EXCEL_FILE, "rb"), file_name=EXCEL_FILE)
    st.download_button("⬇️ Tải tất cả mã QR (.zip)", open(ZIP_FILE, "rb"), file_name=ZIP_FILE)
    st.subheader("📚 Danh sách xe")
    st.dataframe(df, use_container_width=True, height=600)

# === 2) ĐĂNG KÝ XE MỚI ===
elif menu == "🆕 Đăng ký xe mới":
    df = get_data()
    st.subheader("🆕 Nhập thông tin xe mới")

    # nguồn đơn vị từ sheet
    tendonvi_list = sorted([x for x in df['tendonvi'].dropna().unique().tolist() if x != ""])
    tendonvi = st.selectbox("Tên đơn vị", [""] + tendonvi_list, index=0)
    madonvi = ""
    if tendonvi:
        rows_dn = df[df['tendonvi'] == tendonvi]
        madonvi = rows_dn['madonvi'].dropna().astype(str).iloc[0] if not rows_dn.empty else ""

    with st.form("form_dangky", clear_on_submit=True):
        ten = st.text_input("Họ tên")
        bsx = st.text_input("Biển số")
        st.text_input("Mã đơn vị (tự động)", madonvi, disabled=True)
        submit = st.form_submit_button("➕ Đăng ký")

    if submit:
        ten_norm = normalize_name(ten)
        bsx_norm = normalize_plate(bsx)

        if not all([ten_norm, bsx_norm, tendonvi]):
            st.error("❌ Vui lòng nhập đầy đủ thông tin.")
        else:
            # chống trùng biển số
            existing_bs = df['bsx'].fillna("").apply(normalize_plate)
            if bsx_norm in set(existing_bs):
                st.error("⚠️ Biển số này đã tồn tại trong hệ thống.")
            else:
                stt = int(pd.to_numeric(df['stt'], errors='coerce').max() or 0) + 1
                qr_id = f"QR{stt:03}"
                append_row(stt, ten_norm, bsx_norm, qr_id, madonvi, tendonvi)
                st.success(f"✅ Đăng ký thành công. Mã QR: {qr_id}")
                qr_url = QR_LINK_PREFIX + qr_id
                img = create_qr(qr_url)
                st.image(img, caption=f"QR cho {qr_id}", use_container_width=True)
                st.download_button("💾 Tải mã QR", img, file_name=f"qr_{qr_id}.png")

# === 3) TRA CỨU TỪ QR ===
elif menu == "🔎 Tra cứu từ QR":
    st.subheader("🔎 Tra cứu thông tin xe từ mã QR")
    # đọc query param qr_id
    qp = st.query_params
    qr_id = qp.get("qr_id", "")
    if isinstance(qr_id, list):
        qr_id = qr_id[0] if qr_id else ""
    qr_id = str(qr_id)

    if not qr_id:
        st.info("ℹ️ Vui lòng mở liên kết có chứa tham số ?qr_id=... (quét từ QR)")
    else:
        pw = st.text_input("🔐 Nhập mật khẩu để xem thông tin", type="password")
        if pw == PASSWORD:
            df = get_data()
            row = df[df['qr_id'].astype(str) == qr_id]
            if not row.empty:
                r = row.iloc[0]
                st.success("✅ Thông tin xe:")
                st.write({
                    "Họ tên": r['ten'],
                    "Biển số": r['bsx'],
                    "Mã thẻ": r['qr_id'],
                    "Tên đơn vị": r['tendonvi'],
                    "Mã đơn vị": r['madonvi'],
                    "Chức vụ": r['chucvu'],
                    "Số điện thoại": r['dienthoai'],
                    "Email": r['email']
                })
            else:
                st.error("❌ Không tìm thấy xe với mã này.")
        elif pw:
            st.error("🔒 Mật khẩu không đúng.")

# === 4) XEM DANH SÁCH XE ===
elif menu == "📚 Xem danh sách xe":
    df = get_data()
    st.subheader("📚 Danh sách toàn bộ xe")
    st.dataframe(df, use_container_width=True, height=650)
