import streamlit as st
import pandas as pd
import urllib.parse
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import qrcode
import re
from PIL import Image
from io import BytesIO

# ========== GIAO DIỆN ==========
st.markdown("""
    <style>
        .block-container {
            padding-top: 1rem;
            padding-bottom: 1rem;
        }
    </style>
""", unsafe_allow_html=True)

st.title("🚗 QR Car Management")

menu = [
    "📋 Xem danh sách",
    "🔍 Tìm kiếm xe",
    "➕ Đăng ký xe mới",
    "✏️ Cập nhật xe",
    "🗑️ Xóa xe",
    "📱 Tạo mã QR",
    "📤 Xuất ra Excel",
    "🔐 Quản lý mật khẩu QR",
    "🔓 Giải mã QR"
]
default_tab = "🔓 Giải mã QR" if "id" in st.query_params else menu[0]
choice = st.sidebar.radio("📌 Chọn chức năng", menu, index=menu.index(default_tab))

# ========== HÀM TIỆN ÍCH ==========
def format_name(name):
    return ' '.join(word.capitalize() for word in name.strip().split())

def format_plate(plate):
    plate = re.sub(r'[^a-zA-Z0-9]', '', plate).upper()
    if len(plate) >= 8:
        return f"{plate[:2]}{plate[2]}-{plate[3:6]}.{plate[6:]}"
    return plate

def normalize_plate(plate):
    return re.sub(r'[^a-zA-Z0-9]', '', plate).lower()

# ========== LẤY BIỂN SỐ TỪ URL ==========
bien_so_qr = st.query_params["id"][0] if "id" in st.query_params else None

# ========== GOOGLE SHEET ==========
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
try:
    creds_dict = dict(st.secrets["google_service_account"])
    creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n").strip()
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
except Exception as e:
    st.error(f"❌ Lỗi khởi tạo Google Credentials: {e}")
    st.stop()

SHEET_ID = "1a_pMNiQbD5yO58abm4EfNMz7AbQTBmG8QV3yEN500uc"
try:
    sheet = client.open_by_key(SHEET_ID).worksheet("Sheet1")
except Exception as e:
    st.error(f"❌ Lỗi mở Google Sheet: {e}")
    st.stop()

# ========== TẢI DỮ LIỆU ==========
try:
    data = sheet.get_all_records()
    df = pd.DataFrame(data)
except Exception as e:
    st.error(f"❌ Lỗi tải dữ liệu: {e}")
    st.stop()

# ========== CÁC CHỨC NĂNG ==========
if choice == "📋 Xem danh sách":
    st.subheader("Danh sách xe")
    st.dataframe(df)

elif choice == "🔍 Tìm kiếm xe":
    st.subheader("🔍 Tìm kiếm xe theo biển số")
    bien_so_input = st.text_input("Nhập biển số xe cần tìm")
    if bien_so_input:
        bien_so_norm = normalize_plate(bien_so_input)
        df["Biển số chuẩn hóa"] = df["Biển số"].apply(normalize_plate)
        ket_qua = df[df["Biển số chuẩn hóa"] == bien_so_norm]
        if ket_qua.empty:
            st.warning("🚫 Không tìm thấy xe nào khớp với biển số đã nhập.")
        else:
            st.success(f"✅ Tìm thấy {len(ket_qua)} xe khớp.")
            st.dataframe(ket_qua.drop(columns=["Biển số chuẩn hóa"]), use_container_width=True)

elif choice == "➕ Đăng ký xe mới":
    st.subheader("Đăng ký xe mới")
    don_vi_map = dict(zip(df["Tên đơn vị"], df["Mã đơn vị"]))
    ten_don_vi_list = sorted(don_vi_map.keys())
    col1, col2 = st.columns(2)
    with col1:
        ho_ten = st.text_input("Họ tên")
        bien_so = st.text_input("Biển số xe")
        ten_don_vi = st.selectbox("Tên đơn vị", ten_don_vi_list)
        ma_don_vi = don_vi_map.get(ten_don_vi, "")
    with col2:
        chuc_vu = st.text_input("Chức vụ")
        so_dien_thoai = st.text_input("Số điện thoại")
        email = st.text_input("Email")

    filtered = df["Mã thẻ"].dropna()[df["Mã thẻ"].str.startswith(ma_don_vi)]
    if not filtered.empty:
        numbers = filtered.str.extract(f"{ma_don_vi}(\d{{3}})")[0].dropna().astype(int)
        next_number = max(numbers) + 1
    else:
        next_number = 1
    ma_the = f"{ma_don_vi}{next_number:03d}"

    st.markdown(f"🔐 **Mã thẻ tự sinh:** `{ma_the}`")
    st.markdown(f"🏢 **Mã đơn vị:** `{ma_don_vi}`")

    if st.button("Lưu thông tin"):
        ho_ten = format_name(ho_ten)
        bien_so = format_plate(bien_so)
        if not ho_ten or not bien_so:
            st.warning("⚠️ Vui lòng nhập ít nhất Họ tên và Biển số xe.")
        elif bien_so in df["Biển số"].apply(format_plate).values:
            st.error("❌ Biển số xe đã tồn tại!")
        else:
            stt = len(df) + 1
            sheet.append_row([
                stt, ho_ten, bien_so, ma_the, ma_don_vi,
                ten_don_vi, chuc_vu, so_dien_thoai, email
            ])
            st.success(f"✅ Đã lưu thông tin xe thành công!\n🔐 Mã thẻ: `{ma_the}`")

elif choice == "✏️ Cập nhật xe":
    st.subheader("✏️ Cập nhật xe")
    bien_so_input = st.text_input("Nhập biển số xe cần cập nhật")
    if bien_so_input:
        bien_so_norm = normalize_plate(bien_so_input)
        df["Biển số chuẩn hóa"] = df["Biển số"].apply(normalize_plate)
        ket_qua = df[df["Biển số chuẩn hóa"] == bien_so_norm]
        if ket_qua.empty:
            st.error("❌ Không tìm thấy biển số xe!")
        else:
            st.success(f"✅ Tìm thấy {len(ket_qua)} xe khớp.")
            st.dataframe(ket_qua.drop(columns=["Biển số chuẩn hóa"]), use_container_width=True)
            index = ket_qua.index[0]
            row = ket_qua.iloc[0]
            st.markdown("### 📝 Nhập thông tin mới để cập nhật")
            col1, col2 = st.columns(2)
            with col1:
                ho_ten_moi = st.text_input("Họ tên", value=row["Họ tên"])
                bien_so_moi = st.text_input("Biển số xe", value=row["Biển số"])
                ten_don_vi_moi = st.text_input("Tên đơn vị", value=row["Tên đơn vị"])
                ma_don_vi_moi = st.text_input("Mã đơn vị", value=row["Mã đơn vị"])
            with col2:
                chuc_vu_moi = st.text_input("Chức vụ", value=row["Chức vụ"])
                so_dien_thoai_moi = st.text_input("Số điện thoại", value=row["Số điện thoại"])
                email_moi = st.text_input("Email", value=row["Email"])
            if st.button("Cập nhật"):
                sheet.update(f"A{index+2}:I{index+2}", [[
                    row["STT"],
                    ho_ten_moi,
                    bien_so_moi,
                    row["Mã thẻ"],
                    ma_don_vi_moi,
                    ten_don_vi_moi,
                    chuc_vu_moi,
                    so_dien_thoai_moi,
                    email_moi
                ]])
                st.success("✅ Đã cập nhật thông tin xe thành công!")

elif choice == "🗑️ Xóa xe":
    st.subheader("🗑️ Xóa xe khỏi danh sách")
    bien_so_input = st.text_input("Nhập biển số xe cần xóa")
    if bien_so_input:
        try:
            bien_so_norm = normalize_plate(bien_so_input)
            df = df.copy()
            df["Biển số chuẩn hóa"] = df["Biển số"].astype(str).apply(normalize_plate)
            ket_qua = df[df["Biển số chuẩn hóa"] == bien_so_norm]

            if ket_qua.empty:
                st.error("❌ Không tìm thấy biển số xe!")
            else:
                st.success(f"✅ Tìm thấy {len(ket_qua)} xe khớp.")
                st.dataframe(ket_qua.drop(columns=["Biển số chuẩn hóa"]), use_container_width=True)

                index = ket_qua.index[0]
                row = ket_qua.iloc[0]

                if st.button("Xác nhận xóa"):
                    sheet.delete_rows(index + 2)  # +2 vì dòng header
                    st.success(f"🗑️ Đã xóa xe có biển số `{row['Biển số']}` thành công!")

        except Exception as e:
            st.error(f"⚠️ Lỗi khi xử lý: {e}")

elif choice == "📱 Tạo mã QR":
    st.subheader("📱 Tạo mã QR cho xe")

    # Chọn xe cần tạo mã
    bien_so_input = st.text_input("📋 Nhập biển số xe để tạo mã QR")
    if bien_so_input:
        try:
            bien_so_norm = normalize_plate(bien_so_input)

            df = df.copy()
            df["Biển số chuẩn hóa"] = df["Biển số"].astype(str).apply(normalize_plate)
            ket_qua = df[df["Biển số chuẩn hóa"] == bien_so_norm]

            if ket_qua.empty:
                st.error("❌ Không tìm thấy xe có biển số này!")
            else:
                row = ket_qua.iloc[0]

                # Tạo link QR dùng biển số đã chuẩn hóa
                import urllib.parse
                link = f"https://qrcarump.streamlit.app/?id={normalize_plate(row['Biển số'])}"

                # Tạo mã QR
                import qrcode
                import io
                img = qrcode.make(link)
                buf = io.BytesIO()
                img.save(buf)
                st.image(buf.getvalue(), caption=f"Mã QR cho xe {row['Biển số']}", use_column_width=True)

                # Cho phép tải về
                st.download_button(
                    label="📥 Tải mã QR",
                    data=buf.getvalue(),
                    file_name=f"QR_{row['Biển số']}.png",
                    mime="image/png"
                )

                # Hiển thị thông tin xe
                st.success("✅ Thông tin xe:")
                st.dataframe(row.to_frame().T, use_container_width=True)

        except Exception as e:
            st.error(f"⚠️ Lỗi khi xử lý: {e}")

elif choice == "🔓 Giải mã QR":
    st.subheader("🔓 Giải mã thông tin xe từ mã QR")

    bien_so_url = st.query_params["id"][0] if "id" in st.query_params else ""
    bien_so_input = bien_so_url if bien_so_url else st.text_input("📋 Nhập biển số xe")
    mat_khau_input = st.text_input("🔑 Nhập mật khẩu", type="password")

    if bien_so_input and mat_khau_input:
        bien_so_norm = normalize_plate(bien_so_input)
        df["Biển số chuẩn hóa"] = df["Biển số"].astype(str).apply(normalize_plate)
        ket_qua = df[df["Biển số chuẩn hóa"] == bien_so_norm]

        if ket_qua.empty:
            st.error("❌ Không tìm thấy xe!")
        else:
            mat_khau_dung = st.session_state.get("mat_khau_qr", "qr@217hb")
            if mat_khau_input == mat_khau_dung:
                row = ket_qua.iloc[0]
                st.success("✅ Mật khẩu đúng. Thông tin xe:")
                st.write(f"""
                - Biển số: {row['Biển số']}
                - Họ tên: {row['Họ tên']}
                - Mã thẻ: {row['Mã thẻ']}
                - Đơn vị: {row['Tên đơn vị']}
                - Chức vụ: {row['Chức vụ']}
                - SĐT: {row['Số điện thoại']}
                - Email: {row['Email']}
                """)
            else:
                st.error("❌ Sai mật khẩu.")

elif choice == "🔐 Quản lý mật khẩu QR":
    st.subheader("🔐 Quản lý mật khẩu QR")

    if "mat_khau_qr" not in st.session_state:
        st.session_state["mat_khau_qr"] = "qr@217hb"

    mat_khau_hien_tai = st.session_state["mat_khau_qr"]
    st.info(f"🔐 Mật khẩu hiện tại đang dùng: `{mat_khau_hien_tai}`")

    mat_khau_moi = st.text_input("🔄 Nhập mật khẩu mới", type="password")

    if st.button("✅ Cập nhật mật khẩu"):
        if mat_khau_moi.strip() == "":
            st.warning("⚠️ Mật khẩu không được để trống.")
        else:
            st.session_state["mat_khau_qr"] = mat_khau_moi.strip()
            st.success(f"✅ Đã cập nhật mật khẩu QR thành `{mat_khau_moi.strip()}`")

elif choice == "📤 Xuất ra Excel":
    st.subheader("📤 Tải danh sách xe dưới dạng Excel")
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='DanhSachXe')
        writer.close()
        processed_data = output.getvalue()

    st.download_button(
        label="📥 Tải Excel",
        data=processed_data,
        file_name="DanhSachXe.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
