import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ===================== GOOGLE SHEET KẾT NỐI =====================
scope = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/drive"]

try:
    # Đọc credentials từ Streamlit secrets
    creds_dict = dict(st.secrets["google_service_account"])  # copy để cho phép sửa
    
    # Xử lý private_key cho đúng định dạng PEM
    pk = creds_dict["private_key"].replace("\\n", "\n").strip()
    if not pk.startswith("-----BEGIN PRIVATE KEY-----"):
        pk = "-----BEGIN PRIVATE KEY-----\n" + pk
    if not pk.endswith("-----END PRIVATE KEY-----"):
        pk = pk + "\n-----END PRIVATE KEY-----"
    creds_dict["private_key"] = pk

    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
except Exception as e:
    st.error(f"❌ Lỗi khởi tạo Google Credentials: {e}")
    st.stop()

# ===================== MỞ GOOGLE SHEET =====================
SHEET_ID = "18fQqPJ5F9VZdWvkuQq5K7upQHeC7UfZX"
try:
    sheet = client.open_by_key(SHEET_ID).sheet1
except Exception as e:
    st.error(f"❌ Lỗi mở Google Sheet: {e}")
    st.stop()

# ===================== GIAO DIỆN STREAMLIT =====================
st.title("🚗 QR Car Management")

menu = ["📋 Xem danh sách", "➕ Đăng ký xe mới"]
choice = st.sidebar.selectbox("Chọn chức năng", menu)

if choice == "📋 Xem danh sách":
    st.subheader("Danh sách xe")
    try:
        data = sheet.get_all_records()
        st.dataframe(data)
    except Exception as e:
        st.error(f"❌ Lỗi tải dữ liệu: {e}")

elif choice == "➕ Đăng ký xe mới":
    st.subheader("Đăng ký xe mới")
    col1, col2 = st.columns(2)
    with col1:
        bien_so = st.text_input("Biển số xe")
        mau_son = st.text_input("Màu sơn")
    with col2:
        chu_so_huu = st.text_input("Chủ sở hữu")
        don_vi = st.text_input("Tên đơn vị")

    if st.button("Lưu thông tin"):
        try:
            sheet.append_row([bien_so, mau_son, chu_so_huu, don_vi])
            st.success("✅ Đã lưu thông tin xe thành công!")
        except Exception as e:
            st.error(f"❌ Lỗi khi lưu: {e}")
