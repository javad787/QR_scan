from flask import Flask, render_template, request, jsonify, send_file, redirect, url_for, session
import pandas as pd
import cv2
import os
import base64
from io import BytesIO
import datetime
import numpy as np
import qrcode
from PIL import Image
import logging
import secrets

app = Flask(__name__)
app.secret_key = secrets.token_hex(16)

# Configure logging
logging.basicConfig(filename='app.log', level=logging.DEBUG,
                    format='%(asctime)s %(levelname)s: %(message)s')

# Folder configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FOLDER = os.path.join(BASE_DIR, "data")
PHOTOS_FOLDER = os.path.join(DATA_FOLDER, "photos")
QRCODES_FOLDER = os.path.join(DATA_FOLDER, "qrcodes")
ATTENDANCE_FOLDER = os.path.join(DATA_FOLDER, "attendance")
CSV_COLUMNS = ["name", "phone_number", "photo"]

# Ensure folders exist
for folder in [DATA_FOLDER, PHOTOS_FOLDER, QRCODES_FOLDER, ATTENDANCE_FOLDER]:
    os.makedirs(folder, exist_ok=True)

# Mock user database (replace with real DB in production)
USERS = {"admin": "password123"}  # username: password

# Load all CSV files from the data folder
def load_csv_files():
    try:
        students = []
        for file in os.listdir(DATA_FOLDER):
            if file.endswith(".csv") and file.startswith("students_"):
                file_path = os.path.join(DATA_FOLDER, file)
                try:
                    df = pd.read_csv(file_path)
                    if all(col in df.columns for col in CSV_COLUMNS):
                        students.append(df[CSV_COLUMNS])
                    else:
                        app.logger.warning(f"Skipping {file}: Missing required columns")
                except Exception as e:
                    app.logger.error(f"Error reading {file}: {e}")
                    continue
        return pd.concat(students, ignore_index=True) if students else pd.DataFrame(columns=CSV_COLUMNS)
    except Exception as e:
        app.logger.error(f"Error loading CSV files: {e}")
        return pd.DataFrame(columns=CSV_COLUMNS)

# Find student by phone number (full or last 4 digits)
def find_student(phone_number, partial=False):
    try:
        df = load_csv_files()
        if partial:
            phone_str = str(phone_number)[-4:]
            student = df[df["phone_number"].astype(str).str.endswith(phone_str)]
        else:
            student = df[df["phone_number"].astype(str) == str(phone_number)]
        return student.iloc[0].to_dict() if not student.empty else None
    except Exception as e:
        app.logger.error(f"Error finding student {phone_number}: {e}")
        return None

# Load attendance data for a specific session
def load_attendance(course_id, session_id):
    try:
        attendance_file = os.path.join(ATTENDANCE_FOLDER, f"attendance_course_{course_id}_session_{session_id}.csv")
        if os.path.exists(attendance_file):
            return pd.read_csv(attendance_file)
        return pd.DataFrame(columns=["name", "phone_number", "status", "timestamp"])
    except Exception as e:
        app.logger.error(f"Error loading attendance for course {course_id}, session {session_id}: {e}")
        return pd.DataFrame(columns=["name", "phone_number", "status", "timestamp"])

# Save attendance with duplicate checking
def save_attendance(student, status, course_id, session_id):
    try:
        attendance_file = os.path.join(ATTENDANCE_FOLDER, f"attendance_course_{course_id}_session_{session_id}.csv")
        attendance = load_attendance(course_id, session_id)
        if str(student["phone_number"]) in attendance["phone_number"].astype(str).values:
            return False, "Duplicate attendance entry for this student"
        new_entry = {
            "name": student["name"],
            "phone_number": student["phone_number"],
            "status": status,
            "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        attendance = pd.concat([attendance, pd.DataFrame([new_entry])], ignore_index=True)
        attendance.to_csv(attendance_file, index=False)
        app.logger.info(f"Attendance recorded for {student['name']} ({status})")
        return True, "Attendance recorded successfully"
    except Exception as e:
        app.logger.error(f"Error saving attendance for {student['name']}: {e}")
        return False, str(e)

# Get attendance history for a student
def get_attendance_history(phone_number):
    try:
        history = []
        for file in sorted(os.listdir(ATTENDANCE_FOLDER), reverse=True)[:5]:
            if file.endswith(".csv"):
                df = pd.read_csv(os.path.join(ATTENDANCE_FOLDER, file))
                student_record = df[df["phone_number"].astype(str) == str(phone_number)]
                if not student_record.empty:
                    history.append({
                        "session": file.replace("attendance_", "").replace(".csv", ""),
                        "status": student_record.iloc[0]["status"],
                        "timestamp": student_record.iloc[0]["timestamp"]
                    })
        return history
    except Exception as e:
        app.logger.error(f"Error getting attendance history for {phone_number}: {e}")
        return []

# Generate QR code for phone number
def generate_qr_code(phone_number):
    try:
        qr = qrcode.QRCode(version=1, box_size=10, border=4)
        qr.add_data(phone_number)
        qr.make(fit=True)
        img = qr.make_image(fill="black", back_color="white")
        qr_path = os.path.join(QRCODES_FOLDER, f"{phone_number}.png")
        img.save(qr_path)
        app.logger.info(f"QR code generated for {phone_number}")
        return qr_path
    except Exception as e:
        app.logger.error(f"Error generating QR code for {phone_number}: {e}")
        return None

# Login required decorator
def login_required(f):
    def wrap(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    wrap.__name__ = f.__name__
    return wrap

# Route for login
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if username in USERS and USERS[username] == password:
            session['logged_in'] = True
            app.logger.info(f"User {username} logged in")
            return redirect(url_for("setup_session"))
        app.logger.warning(f"Failed login attempt for {username}")
        return render_template("login.html", error="Invalid credentials")
    return render_template("login.html")

# Route for logout
@app.route("/logout")
def logout():
    session.pop('logged_in', None)
    app.logger.info("User logged out")
    return redirect(url_for("login"))

# Route for session setup
@app.route("/setup_session", methods=["GET", "POST"])
@login_required
def setup_session():
    if request.method == "POST":
        course_id = request.form.get("course_id")
        session_id = request.form.get("session_id")
        if course_id and session_id and course_id.isalnum() and session_id.isalnum():
            return redirect(url_for("index", course_id=course_id, session_id=session_id))
        app.logger.warning(f"Invalid course_id or session_id: {course_id}, {session_id}")
        return render_template("setup_session.html", error="Course ID and Session ID must be alphanumeric")
    return render_template("setup_session.html")

# Route for QR code scanning page
@app.route("/")
@login_required
def index():
    course_id = request.args.get("course_id", "1")
    session_id = request.args.get("session_id", "1")
    return render_template("index.html", course_id=course_id, session_id=session_id)

# Route for processing QR code
@app.route("/scan", methods=["POST"])
@login_required
def scan_qr():
    try:
        course_id = request.form["course_id"]
        session_id = request.form["session_id"]
        image_data = request.form["image"].split(",")[1]
        image_bytes = base64.b64decode(image_data)
        nparr = np.frombuffer(image_bytes, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        detector = cv2.QRCodeDetector()
        data, bbox, _ = detector.detectAndDecode(image)
        if data:
            phone_number = data
            student = find_student(phone_number)
            if student:
                photo_path = os.path.join(PHOTOS_FOLDER, student["photo"])
                if os.path.exists(photo_path):
                    with open(photo_path, "rb") as img_file:
                        photo_data = base64.b64encode(img_file.read()).decode("utf-8")
                        student["photo_data"] = f"data:image/jpg;base64,{photo_data}"
                    student["history"] = get_attendance_history(phone_number)
                    app.logger.info(f"Student found via QR: {student['name']}")
                    return jsonify({"success": True, "student": student})
                else:
                    app.logger.error(f"Photo not found at {photo_path}")
                    return jsonify({"success": False, "error": f"Photo not found at {photo_path}"})
            else:
                app.logger.warning(f"Student not found for QR code: {phone_number}")
                return jsonify({"success": False, "error": "Student not found"})
        else:
            app.logger.warning("No QR code detected in image")
            return jsonify({"success": False, "error": "No QR code detected"})
    except Exception as e:
        app.logger.error(f"Error processing QR code: {e}")
        return jsonify({"success": False, "error": str(e)})

# Route for searching by last 4 digits
@app.route("/search", methods=["POST"])
@login_required
def search_student():
    try:
        phone_digits = request.form.get("phone_digits")
        course_id = request.form.get("course_id")
        session_id = request.form.get("session_id")
        if phone_digits and len(phone_digits) >= 4 and phone_digits.isdigit():
            student = find_student(phone_digits, partial=True)
            if student:
                photo_path = os.path.join(PHOTOS_FOLDER, student["photo"])
                if os.path.exists(photo_path):
                    with open(photo_path, "rb") as img_file:
                        photo_data = base64.b64encode(img_file.read()).decode("utf-8")
                        student["photo_data"] = f"data:image/jpg;base64,{photo_data}"
                    student["history"] = get_attendance_history(student["phone_number"])
                    app.logger.info(f"Student found via search: {student['name']}")
                    return jsonify({"success": True, "student": student})
                else:
                    app.logger.error(f"Photo not found at {photo_path}")
                    return jsonify({"success": False, "error": f"Photo not found at {photo_path}"})
            else:
                app.logger.warning(f"Student not found for digits: {phone_digits}")
                return jsonify({"success": False, "error": "Student not found"})
        app.logger.warning(f"Invalid phone digits: {phone_digits}")
        return jsonify({"success": False, "error": "At least 4 digits required"})
    except Exception as e:
        app.logger.error(f"Error searching student: {e}")
        return jsonify({"success": False, "error": str(e)})

# Route for accepting/rejecting/exiting attendance
@app.route("/attendance", methods=["POST"])
@login_required
def update_attendance():
    try:
        data = request.json
        phone_number = data["phone_number"]
        status = data["status"]  # entry, exit, or reject
        course_id = data["course_id"]
        session_id = data["session_id"]
        student = find_student(phone_number)
        if student:
            success, message = save_attendance(student, status, course_id, session_id)
            if success:
                app.logger.info(f"Attendance updated: {student['name']} - {status}")
                return jsonify({"success": True, "message": message})
            return jsonify({"success": False, "error": message})
        app.logger.warning(f"Student not found for attendance update: {phone_number}")
        return jsonify({"success": False, "error": "Student not found"})
    except Exception as e:
        app.logger.error(f"Error updating attendance: {e}")
        return jsonify({"success": False, "error": str(e)})

# Route for attendance list page
@app.route("/attendance_list")
@login_required
def attendance_list():
    try:
        course_id = request.args.get("course_id", "1")
        session_id = request.args.get("session_id", "1")
        attendance = load_attendance(course_id, session_id)
        present_students = attendance[attendance["status"] == "entry"].to_dict(orient="records")
        rejected_students = attendance[attendance["status"] == "reject"].to_dict(orient="records")
        exited_students = attendance[attendance["status"] == "exit"].to_dict(orient="records")
        all_students = load_csv_files()
        present_phones = attendance[attendance["status"] == "entry"]["phone_number"].astype(str)
        absent_students = all_students[~all_students["phone_number"].astype(str).isin(present_phones)].to_dict(orient="records")
        return render_template(
            "attendance_list.html",
            present_students=present_students,
            rejected_students=rejected_students,
            exited_students=exited_students,
            absent_students=absent_students,
            course_id=course_id,
            session_id=session_id
        )
    except Exception as e:
        app.logger.error(f"Error rendering attendance list: {e}")
        return render_template("error.html", error=str(e))

# Route for downloading attendance lists
@app.route("/download_present")
@login_required
def download_present():
    try:
        course_id = request.args.get("course_id", "1")
        session_id = request.args.get("session_id", "1")
        attendance = load_attendance(course_id, session_id)
        present_students = attendance[attendance["status"] == "entry"]
        output = BytesIO()
        present_students[["name", "phone_number", "status", "timestamp"]].to_csv(output, index=False)
        output.seek(0)
        app.logger.info(f"Downloaded present students for course {course_id}, session {session_id}")
        return send_file(
            output,
            mimetype="text/csv",
            as_attachment=True,
            download_name=f"present_students_course_{course_id}_session_{session_id}.csv"
        )
    except Exception as e:
        app.logger.error(f"Error downloading present students: {e}")
        return jsonify({"success": False, "error": str(e)})

@app.route("/download_rejected")
@login_required
def download_rejected():
    try:
        course_id = request.args.get("course_id", "1")
        session_id = request.args.get("session_id", "1")
        attendance = load_attendance(course_id, session_id)
        rejected_students = attendance[attendance["status"] == "reject"]
        output = BytesIO()
        rejected_students[["name", "phone_number", "status", "timestamp"]].to_csv(output, index=False)
        output.seek(0)
        app.logger.info(f"Downloaded rejected students for course {course_id}, session {session_id}")
        return send_file(
            output,
            mimetype="text/csv",
            as_attachment=True,
            download_name=f"rejected_students_course_{course_id}_session_{session_id}.csv"
        )
    except Exception as e:
        app.logger.error(f"Error downloading rejected students: {e}")
        return jsonify({"success": False, "error": str(e)})

@app.route("/download_exited")
@login_required
def download_exited():
    try:
        course_id = request.args.get("course_id", "1")
        session_id = request.args.get("session_id", "1")
        Attendance = load_attendance(course_id, session_id)
        exited_students = attendance[attendance["status"] == "exit"]
        output = BytesIO()
        exited_students[["name", "phone_number", "status", "timestamp"]].to_csv(output, index=False)
        output.seek(0)
        app.logger.info(f"Downloaded exited students for course {course_id}, session {session_id}")
        return send_file(
            output,
            mimetype="text/csv",
            as_attachment=True,
            download_name=f"exited_students_course_{course_id}_session_{session_id}.csv"
        )
    except Exception as e:
        app.logger.error(f"Error downloading exited students: {e}")
        return jsonify({"success": False, "error": str(e)})

@app.route("/download_absent")
@login_required
def download_absent():
    try:
        course_id = request.args.get("course_id", "1")
        session_id = request.args.get("session_id", "1")
        all_students = load_csv_files()
        attendance = load_attendance(course_id, session_id)
        present_phones = attendance[attendance["status"] == "entry"]["phone_number"].astype(str)
        absent_students = all_students[~all_students["phone_number"].astype(str).isin(present_phones)]
        output = BytesIO()
        absent_students[["name", "phone_number"]].to_csv(output, index=False)
        output.seek(0)
        app.logger.info(f"Downloaded absent students for course {course_id}, session {session_id}")
        return send_file(
            output,
            mimetype="text/csv",
            as_attachment=True,
            download_name=f"absent_students_course_{course_id}_session_{session_id}.csv"
        )
    except Exception as e:
        app.logger.error(f"Error downloading absent students: {e}")
        return jsonify({"success": False, "error": str(e)})

# Route for downloading QR code
@app.route("/download_qr/<phone_number>")
@login_required
def download_qr(phone_number):
    try:
        qr_path = os.path.join(QRCODES_FOLDER, f"{phone_number}.png")
        if os.path.exists(qr_path):
            app.logger.info(f"Downloaded QR code for {phone_number}")
            return send_file(qr_path, as_attachment=True, download_name=f"qr_{phone_number}.png")
        app.logger.error(f"QR code not found for {phone_number}")
        return jsonify({"success": False, "error": "QR code not found"})
    except Exception as e:
        app.logger.error(f"Error downloading QR code for {phone_number}: {e}")
        return jsonify({"success": False, "error": str(e)})

# Route for resetting attendance
@app.route("/reset_attendance", methods=["POST"])
@login_required
def reset_attendance():
    try:
        course_id = request.form.get("course_id")
        session_id = request.form.get("session_id")
        attendance_file = os.path.join(ATTENDANCE_FOLDER, f"attendance_course_{course_id}_session_{session_id}.csv")
        if os.path.exists(attendance_file):
            os.remove(attendance_file)
            app.logger.info(f"Attendance reset for course {course_id}, session {session_id}")
        return redirect(url_for("attendance_list", course_id=course_id, session_id=session_id))
    except Exception as e:
        app.logger.error(f"Error resetting attendance: {e}")
        return jsonify({"success": False, "error": str(e)})

# Route for deleting a student
@app.route("/delete_student", methods=["POST"])
@login_required
def delete_student():
    try:
        phone_number = request.form.get("phone_number")
        course_id = request.form.get("course_id")
        session_id = request.form.get("session_id")
        all_students = load_csv_files()
        if str(phone_number) in all_students["phone_number"].astype(str).values:
            all_students = all_students[all_students["phone_number"].astype(str) != str(phone_number)]
            csv_path = os.path.join(DATA_FOLDER, "students.csv")
            all_students.to_csv(csv_path, index=False)
            # Delete associated photo and QR code
            photo_path = os.path.join(PHOTOS_FOLDER, f"{phone_number}_*.jpg")
            qr_path = os.path.join(QRCODES_FOLDER, f"{phone_number}.png")
            for path in [photo_path, qr_path]:
                if os.path.exists(path):
                    os.remove(path)
            app.logger.info(f"Student deleted: {phone_number}")
            return redirect(url_for("attendance_list", course_id=course_id, session_id=session_id))
        app.logger.warning(f"Student not found for deletion: {phone_number}")
        return jsonify({"success": False, "error": "Student not found"})
    except Exception as e:
        app.logger.error(f"Error deleting student: {e}")
        return jsonify({"success": False, "error": str(e)})

# Route for adding students
@app.route("/add_student", methods=["GET", "POST"])
@login_required
def add_student():
    try:
        course_id = request.args.get("course_id", "1")
        session_id = request.args.get("session_id", "1")
        if request.method == "POST":
            if "csv_file" in request.files and request.files["csv_file"].filename:
                file = request.files["csv_file"]
                if file.filename.endswith(".csv"):
                    try:
                        df = pd.read_csv(file)
                        if all(col in df.columns for col in CSV_COLUMNS):
                            for _, row in df.iterrows():
                                photo_file = row["photo"]
                                if not os.path.exists(os.path.join(PHOTOS_FOLDER, photo_file)):
                                    app.logger.error(f"Photo {photo_file} not found")
                                    return jsonify({"success": False, "error": f"Photo {photo_file} not found"})
                                generate_qr_code(row["phone_number"])
                            csv_path = os.path.join(DATA_FOLDER, f"students_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
                            df.to_csv(csv_path, index=False)
                            app.logger.info(f"CSV uploaded and processed")
                            return redirect(url_for("index", course_id=course_id, session_id=session_id))
                        else:
                            app.logger.warning("CSV missing required columns")
                            return jsonify({"success": False, "error": "CSV missing required columns: name, phone_number, photo"})
                    except Exception as e:
                        app.logger.error(f"Error processing CSV: {e}")
                        return jsonify({"success": False, "error": str(e)})
                else:
                    app.logger.warning("Invalid file format for CSV upload")
                    return jsonify({"success": False, "error": "Invalid file format. Please upload a CSV file."})
            else:
                name = request.form.get("name")
                phone_number = request.form.get("phone_number")
                photo = request.files.get("photo")
                if name and phone_number and photo and photo.filename:
                    if not phone_number.isdigit():
                        app.logger.warning(f"Invalid phone number: {phone_number}")
                        return jsonify({"success": False, "error": "Phone number must be digits only"})
                    photo_filename = f"{phone_number}_{photo.filename}"
                    photo_path = os.path.join(PHOTOS_FOLDER, photo_filename)
                    photo.save(photo_path)
                    new_student = pd.DataFrame([{
                        "name": name,
                        "phone_number": phone_number,
                        "photo": photo_filename
                    }])
                    generate_qr_code(phone_number)
                    csv_path = os.path.join(DATA_FOLDER, "students.csv")
                    if os.path.exists(csv_path):
                        existing = pd.read_csv(csv_path)
                        updated = pd.concat([existing, new_student], ignore_index=True)
                        updated.to_csv(csv_path, index=False)
                    else:
                        new_student.to_csv(csv_path, index=False)
                    app.logger.info(f"Student added: {name}")
                    return redirect(url_for("index", course_id=course_id, session_id=session_id))
                else:
                    app.logger.warning("Missing required fields for manual student addition")
                    return jsonify({"success": False, "error": "All fields (name, phone_number, photo) are required"})
        return render_template("add_student.html", course_id=course_id, session_id=session_id)
    except Exception as e:
        app.logger.error(f"Error adding student: {e}")
        return render_template("error.html", error=str(e))

if __name__ == "__main__":
    app.run(debug=True)