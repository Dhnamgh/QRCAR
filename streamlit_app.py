import streamlit as st
import pandas as pd
import qrcode
from PIL import Image, ImageDraw, ImageFont
import io
import os
import zipfile
import re
import smtplib
from email.message import EmailMessage

# Cấu hình
QR_LINK_PREFIX = "https://ump.edu.vn/thongtinxe"
FONT_PATH = "arial.ttf"
LOGO_PATH = "D:/CAR/background.png"
EMAIL_DOMAIN = "@ump.edu.vn"

# Khởi tạo dữ liệu trong session
if "df" not in st.session_state:
    st.session_state.df = pd.DataFrame()

# Hàm chuẩn hóa họ tên
def chuan_hoa_ho_ten(text):
    return ' '.join([w.capitalize() for w in text.strip().lower().split()])

# Hàm chuẩn hóa biển số xe
def chuan_hoa_bien_so(text):
    text = str(text).strip().upper()
    text = re.sub(r"[^A-Z0-9]", "", text)
    match = re.match(r"^(\d{2}[A-Z])(\d{3})(\d{2})$", text)
    if match:
        return f"{match.group(1)}-{match.group(2)}.{match.group(3)}"
    return text

# Hàm tạo mã QR
def tao_ma_qr(ma_the, ma_donvi):
    qr = qrcode.QRCode(box_size=10, border=2)
    full_link = f"{QR_LINK_PREFIX}?id={ma_the}"
    qr.add_data(full_link)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype(FONT_PATH, 20)
        bbox = draw.textbbox((0, 0), ma_donvi, font=font)
    except Exception:
        font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), ma_donvi)

    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    img_width, img_height = img.size
    x = (img_width - text_width) // 2
    y = img_height - text_height - 10
    draw.text((x, y), ma_donvi, font=font, fill="black")
    return img

# Gửi email thông báo
def gui_email(nguoi_nhan, tieu_de, noi_dung, qr_data):
    try:
        msg = EmailMessage()
        msg['Subject'] = tieu_de
        msg['From'] = 'he-thong@ump.edu.vn'
        msg['To'] = nguoi_nhan
        msg.set_content(noi_dung)
        msg.add_attachment(qr_data, maintype='image', subtype='png', filename='ma_qr.png')

        with smtplib.SMTP('localhost') as server:
            server.send_message(msg)
        return True
    except Exception:
        return False

# Layout
st.set_page_config(layout="wide")
col1, col2 = st.columns([1, 6])
with col1:
    if os.path.exists(LOGO_PATH):
        st.image(LOGO_PATH, width=130)
with col2:
    st.markdown("""
        <h1 style='margin-bottom: 0; color: navy;'>Phần mềm Quản lý xe ra vào cơ quan</h1>
        <h3 style='margin-top: 0;'>Cơ sở 217 Hồng Bàng - Đại học Y Dược TP.HCM</h3>
    """, unsafe_allow_html=True)

menu = st.sidebar.radio("📌 Chọn chức năng", [
    "📥 Tải dữ liệu", 
    "📄 Dữ liệu hiện tại", 
    "🆕 Đăng ký xe mới", 
    "🔍 Tra cứu", 
    "📧 Thông báo kết quả đăng ký",
    "💬 Góp ý cải tiến",
    "🔎 Thông tin xe từ mã QR"
])

if menu == "📥 Tải dữ liệu":
    st.header("📥 Tải tập tin dữ liệu xe (Excel)")
    file = st.file_uploader("Tải lên tập tin Excel (.xlsx)", type=["xlsx"])
    if file:
        df = pd.read_excel(file)
        required_cols = ["STT", "Họ tên", "Biển số", "Mã thẻ", "Mã đơn vị", "Tên đơn vị", "Chức vụ", "Số điện thoại", "Email"]
        for col in required_cols:
            if col not in df.columns:
                df[col] = ""
        df = df[required_cols]
        df["Biển số"] = df["Biển số"].astype(str).apply(chuan_hoa_bien_so)

        for i, row in df.iterrows():
            if not row["Mã thẻ"] or pd.isna(row["Mã thẻ"]):
                ma_donvi = row["Mã đơn vị"]
                stt = row["STT"]
                try:
                    df.at[i, "Mã thẻ"] = f"{ma_donvi}{int(stt):03}"
                except:
                    df.at[i, "Mã thẻ"] = ""

        st.session_state.df = df.copy()
        st.success("✅ Tải dữ liệu thành công.")
        st.dataframe(df)

elif menu == "📄 Dữ liệu hiện tại":
    df = st.session_state.get("df", pd.DataFrame())
    if df.empty:
        st.warning("⚠️ Chưa có dữ liệu. Vui lòng tải lên trước.")
    else:
        st.header("📄 Danh sách xe hiện tại")
        st.dataframe(df)

        to_excel = io.BytesIO()
        with pd.ExcelWriter(to_excel, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False)
        st.download_button("📥 Tải dữ liệu Excel", to_excel.getvalue(), file_name="dsxe_capnhat.xlsx", mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

        if st.button("📦 Tạo và tải mã QR cho toàn bộ xe"):
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w") as zip_file:
                for _, row in df.iterrows():
                    ma_the = str(row["Mã thẻ"])
                    ma_donvi = str(row["Mã đơn vị"])
                    img = tao_ma_qr(ma_the, ma_donvi)
                    img_byte_arr = io.BytesIO()
                    img.save(img_byte_arr, format='PNG')
                    zip_file.writestr(f"{ma_the}.png", img_byte_arr.getvalue())

            st.download_button(
                label="📥 Tải tất cả mã QR (.zip)",
                data=zip_buffer.getvalue(),
                file_name="ma_qr_tatca.zip",
                mime="application/zip"
            )

elif menu == "🔎 Thông tin xe từ mã QR":
    df = st.session_state.get("df", pd.DataFrame())
    params = st.query_params
    ma_the = params.get("id", [None])[0]

    if not ma_the:
        st.warning("⚠️ Không có mã thẻ nào được cung cấp từ liên kết.")
    else:
        password_input = st.text_input("🔒 Nhập mật khẩu để xem thông tin xe", type="password")
        correct_password = "ump123"  # có thể thay bằng mã hóa hoặc lưu ở nơi khác

        if password_input == correct_password:
            xe_info = df[df["Mã thẻ"] == ma_the]
            if xe_info.empty:
                st.error(f"Không tìm thấy thông tin xe với mã thẻ: {ma_the}")
            else:
                st.success(f"✅ Thông tin xe có mã thẻ: {ma_the}")
                st.dataframe(xe_info.reset_index(drop=True))
        elif password_input:
            st.error("🚫 Mật khẩu không đúng.")


elif menu == "🆕 Đăng ký xe mới":
    st.header("🆕 Đăng ký xe mới")
    df = st.session_state.get("df", pd.DataFrame())
    if df.empty:
        st.warning("⚠️ Vui lòng tải dữ liệu trước.")
    else:
        with st.form("form_dk"):
            ho_ten = st.text_input("Họ tên")
            bien_so = st.text_input("Biển số xe")
            ten_donvi = st.selectbox("Tên đơn vị", sorted(df["Tên đơn vị"].dropna().unique()))
            chuc_vu = st.text_input("Chức vụ")
            so_dt = st.text_input("Số điện thoại")
            email = st.text_input("Email (chỉ nhập trước @ump.edu.vn)")
            submitted = st.form_submit_button("Đăng ký")

        if submitted:
            ho_ten = chuan_hoa_ho_ten(ho_ten)
            bien_so = chuan_hoa_bien_so(bien_so)
            ma_donvi = df[df["Tên đơn vị"] == ten_donvi]["Mã đơn vị"].iloc[0]
            next_stt = df["STT"].max() + 1
            ma_the = f"{ma_donvi}{int(next_stt):03}"

            new_row = {
                "STT": next_stt,
                "Họ tên": ho_ten,
                "Biển số": bien_so,
                "Mã thẻ": ma_the,
                "Mã đơn vị": ma_donvi,
                "Tên đơn vị": ten_donvi,
                "Chức vụ": chuc_vu,
                "Số điện thoại": so_dt,
                "Email": email + EMAIL_DOMAIN
            }
            st.session_state.df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
            img = tao_ma_qr(ma_the, ma_donvi)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            st.image(buf.getvalue(), caption="Mã QR của bạn")
            st.download_button("📥 Tải mã QR", buf.getvalue(), file_name="ma_qr.png", mime="image/png")
            st.success("✅ Đăng ký thành công.")

elif menu == "🔍 Tra cứu":
    st.header("🔍 Tra cứu thông tin xe")
    df = st.session_state.get("df", pd.DataFrame())
    bien_so_input = st.text_input("Nhập biển số xe cần tra cứu")
    if bien_so_input:
        bien_so_input = chuan_hoa_bien_so(bien_so_input)
        ket_qua = df[df["Biển số"] == bien_so_input]
        if not ket_qua.empty:
            st.write("✅ Tìm thấy thông tin xe:")
            st.dataframe(ket_qua)
        else:
            st.error("🚫 Không tìm thấy thông tin xe này.")

elif menu == "📧 Thông báo kết quả đăng ký":
    st.header("📧 Gửi thông báo kết quả đăng ký")
    df = st.session_state.get("df", pd.DataFrame())
    email_ten = st.text_input("Nhập tên email người nhận (trước @ump.edu.vn)")
    ket_qua = st.radio("Kết quả đăng ký", ["Đã duyệt", "Không duyệt"])
    ly_do = ""
    if ket_qua == "Không duyệt":
        ly_do = st.text_area("Nhập lý do không duyệt")

    if st.button("Gửi Email"):
        nguoi_nhan = email_ten + EMAIL_DOMAIN
        noi_dung = f"Thông báo kết quả đăng ký: {ket_qua}."
        if ket_qua == "Không duyệt":
            noi_dung += f"\nLý do: {ly_do}"

        ket_qua_df = df[df["Email"] == nguoi_nhan]
        if not ket_qua_df.empty:
            ma_the = ket_qua_df.iloc[0]["Mã thẻ"]
            ma_donvi = ket_qua_df.iloc[0]["Mã đơn vị"]
            qr_img = tao_ma_qr(ma_the, ma_donvi)
            buf = io.BytesIO()
            qr_img.save(buf, format="PNG")
            sent = gui_email(nguoi_nhan, "Kết quả đăng ký xe", noi_dung, buf.getvalue())
            if sent:
                st.success("✅ Gửi email thành công.")
            else:
                st.error("🚫 Gửi email thất bại.")
        else:
            st.warning("⚠️ Không tìm thấy người nhận trong danh sách đăng ký.")

elif menu == "💬 Góp ý cải tiến":
    st.header("💬 Góp ý cải tiến hệ thống")
    ykien = st.text_area("Nhập nội dung góp ý")
    if st.button("Gửi góp ý"):
        st.success("✅ Cảm ơn bạn đã góp ý. Chúng tôi sẽ tiếp thu và cải tiến hệ thống.")
