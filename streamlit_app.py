# qrcar_app.py
import streamlit as st
import pandas as pd
import gspread
import qrcode
import io
import zipfile
import os
from oauth2client.service_account import ServiceAccountCredentials

# === Cấu hình kết nối Google Sheet ===
scope = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]
creds_dict = st.secrets["google_service_account"]
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
client = gspread.authorize(creds)

# === Google Sheet URL ===
SHEET_URL = "https://docs.google.com/spreadsheets/d/18fQqPJ5F9VZdWvkuQq5K7upQHeC7UfZX"
sheet = client.open_by_url(SHEET_URL).sheet1

# === Cấu hình app ===
PASSWORD = "123456"
QR_LINK_PREFIX = "https://qrcarump.streamlit.app/?qr_id="
QR_FOLDER = "qr_images"
EXCEL_FILE = "thong_tin_xe.xlsx"
ZIP_FILE = "qr_all.zip"

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

# === Hàm xử lý ===
def get_data():
    data = sheet.get_all_records()
    df = pd.DataFrame(data)
    df = df.rename(columns=COLUMN_MAP)
    return df

def append_row(row):
    sheet.append_row(row)

def create_qr(data: str):
    qr_img = qrcode.make(data)
    buf = io.BytesIO()
    qr_img.save(buf, format='PNG')
    buf.seek(0)
    return buf

def create_qr_images(df):
    if 'qr_id' not in df.columns:
        raise KeyError("Thiếu cột 'qr_id' trong dữ liệu")
    if not os.path.exists(QR_FOLDER):
        os.makedirs(QR_FOLDER)
    for _, row in df.iterrows():
        qr_id = row['qr_id']
        info_link = QR_LINK_PREFIX + qr_id
        img = qrcode.make(info_link)
        img.save(os.path.join(QR_FOLDER, f"qr_{qr_id}.png"))

def create_zip():
    with zipfile.ZipFile(ZIP_FILE, 'w') as zipf:
        for filename in os.listdir(QR_FOLDER):
            zipf.write(os.path.join(QR_FOLDER, filename), arcname=filename)

def export_excel(df):
    df.to_excel(EXCEL_FILE, index=False)

# === Giao diện ===
st.set_page_config(page_title="Quản lý xe bằng QR", layout="wide")

with st.sidebar:
    st.image("background.png", width=200)
    menu = st.radio("\U0001F4CD Chọn chức năng", [
        "\U0001F4CB Tải dữ liệu về máy",
        "\U0001F697 Đăng ký xe mới",
        "\U0001F50D Tra cứu từ QR",
        "\U0001F4C4 Xem danh sách xe"
    ])

if menu == "\U0001F4CB Tải dữ liệu về máy":
    df = get_data()
    export_excel(df)
    create_qr_images(df)
    create_zip()
    st.success("\u2705 Dữ liệu và mã QR đã được tạo.")
    st.download_button("\U0001F4E5 Tải Excel", open(EXCEL_FILE, "rb"), file_name=EXCEL_FILE)
    st.download_button("\U0001F4E6 Tải tất cả mã QR (.zip)", open(ZIP_FILE, "rb"), file_name=ZIP_FILE)
    st.subheader("\U0001F4C4 Danh sách xe")
    st.dataframe(df, use_container_width=True, height=600)

elif menu == "\U0001F697 Đăng ký xe mới":
    df = get_data()
    st.subheader("\U0001F697 Nhập thông tin xe mới")
    tendonvi_list = df['tendonvi'].dropna().unique().tolist()
    tendonvi = st.selectbox("Tên đơn vị", sorted(tendonvi_list))
    madonvi = df[df['tendonvi'] == tendonvi]['madonvi'].iloc[0] if tendonvi else ""

    with st.form("form_dangky"):
        ten = st.text_input("Họ tên")
        bsx = st.text_input("Biển số")
        st.text_input("Mã đơn vị", madonvi, disabled=True)
        submit = st.form_submit_button("\u2795 Đăng ký")

    if submit:
        if not all([ten, bsx, tendonvi]):
            st.error("\u274C Vui lòng nhập đầy đủ")
        else:
            stt = len(df) + 1
            qr_id = f"QR{stt:03}"
            append_row([stt, ten, bsx, qr_id, madonvi, tendonvi, '', '', ''])
            qr_url = QR_LINK_PREFIX + qr_id
            img = create_qr(qr_url)
            st.success(f"\u2705 Đăng ký thành công với mã QR: {qr_id}")
            st.image(img, caption=f"QR cho {qr_id}", use_container_width=True)
            st.download_button("\U0001F4BE Tải mã QR", img, file_name=f"qr_{qr_id}.png")

elif menu == "\U0001F50D Tra cứu từ QR":
    st.subheader("\U0001F50D Tra cứu thông tin xe từ mã QR")
    qr_id = st.query_params.get("qr_id", [""])[0]
    if not qr_id:
        st.warning("\u274C Không có mã QR trong URL")
    else:
        pw = st.text_input("\U0001F511 Nhập mật khẩu để xem thông tin", type="password")
        if pw == PASSWORD:
            df = get_data()
            row = df[df['qr_id'] == qr_id]
            if not row.empty:
                st.success("\u2705 Thông tin xe:")
                st.write(row.iloc[0])
            else:
                st.error("\u274C Không tìm thấy xe với mã này")
        elif pw:
            st.error("\u274C Mật khẩu không đúng")

elif menu == "\U0001F4C4 Xem danh sách xe":
    df = get_data()
    st.subheader("\U0001F4C4 Danh sách toàn bộ xe")
    st.dataframe(df, use_container_width=True)
