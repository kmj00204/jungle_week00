from flask import Flask, render_template, request, redirect, url_for, jsonify, session, render_template_string
from pymongo import MongoClient, ReturnDocument
import datetime
from zoneinfo import ZoneInfo
import bcrypt
import os
from werkzeug.utils import secure_filename
from bson import ObjectId
import math
import json
#
from dotenv import load_dotenv
from email.message import EmailMessage
import smtplib, uuid
from  datetime import timezone
#
load_dotenv()

app = Flask(__name__)
app.secret_key = "secret_key"

# client = MongoClient("localhost", 27017)
client = MongoClient("mongodb://test:1234@localhost:27017/")

db = client.simple_board_db
posts_collection = db.posts
participants_collection = db.participant
reply_collection = db.reply

app.config["UPLOAD_FOLDER"] = "./static/uploads"

# ---------------------------
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USER = "hdh853@gmail.com"
SMTP_PASS = ""

EMAIL_CLOSED_HTML = """
<!doctype html>
<html lang="ko">
  <body style="font-family:system-ui,-apple-system,Segoe UI,Roboto; line-height:1.6;">
    <p>안녕하세요,</p>
    <p><strong>{{ title }}</strong> 모임이 <b>마감</b>되었습니다.</p>
    {% if closing_date %}
    <p>마감일: {{ closing_date }}</p>
    {% endif %}
    <p>참여해 주셔서 감사합니다!</p>
  </body>
</html>
"""

def send_email(*, to, subject, text=None, html=None, bcc=None, reply_to=None):

    """단건 전송 유틸"""
    to_header = ", ".join(to) if isinstance(to, list) else (to or SMTP_USER)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"]    = SMTP_USER
    msg["To"]      = to_header
    if reply_to:
        msg["Reply-To"] = reply_to

    if html:
        msg.set_content(text or "")
        msg.add_alternative(html, subtype="html")
    else:
        msg.set_content(text or "")

    if bcc:
        msg["Bcc"] = ", ".join(bcc) if isinstance(bcc, list) else bcc

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as s:
        s.ehlo() 
        s.starttls() 
        s.ehlo()
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)

def send_bulk_individually(recipients, subject, text=None, html=None):
    
    """수신자 노출 방지/실패 분리 위해 1명씩 개별 발송"""
    ok, fail, errors = 0, 0, []
    for r in recipients:
        try:
            send_email(to=r, subject=subject, text=text, html=html)
            ok += 1
        except Exception as e:
            fail += 1
            errors.append({"recipient": r, "error": str(e)})
    return {"ok": ok, "fail": fail, "errors": errors}

def collect_participant_emails(oid):
    """
    수신자 수집 규칙:
    1) participants.email 이 우선
    2) 없으면 participants.user_id → users.email 조인
    """
    emails = []

    p_docs = list(participants_collection.find({"post_id": str(oid)}, {"_id": 0, "user_id": 1}))

    user_ids = [p.get("user_id") for p in p_docs if p.get("user_id")]


    if user_ids:
        u_docs = list(db.users.find({"id": {"$in": user_ids}}, {"_id": 0, "email": 1}))
        emails = [u["email"] for u in u_docs if u.get("email")]

    # 중복/빈값 제거
    emails = sorted({e.strip() for e in emails if e and isinstance(e, str)})
    return emails

def render_close_email(post, closing_date_str):
    """제목/본문/HTML 생성 (closing_date 사용)"""
    title   = post.get("title", "모집글")
    subject = f"[공지] '{title}' 마감 안내"
    text    = f"""안녕하세요,
'{title}' 모임이 마감되었습니다.
마감일: {closing_date_str}
참여해 주셔서 감사합니다."""
    html    = render_template_string(EMAIL_CLOSED_HTML, title=title, closing_date=closing_date_str)
    return subject, text, html

def send_post_closing_notifications(
    post_id: str,
    *,
    notify: bool = True,
    dry_run: bool = False,
    require_closed: bool = True,  # True면 status가 'closed'일 때만 발송
):
    """
    ✅ 역할: 주어진 post_id로 참여자 이메일을 조회하여 '마감 안내' 메일만 발송
    - 이 함수는 '상태 변경'을 절대 하지 않음 (마감 처리는 외부에서 이미 완료했다고 가정)
    - require_closed=True면 게시글 status가 'closed'가 아니면 발송하지 않음
    - dry_run=True면 실제 발송 없이 대상/미리보기만 반환
    """

    now = datetime.datetime.now(timezone.utc)

    # 1) post 조회 (존재/상태 점검)
    try:
        oid = ObjectId(post_id)
    except Exception:
        return {"ok": False, "msg": "유효하지 않은 ObjectId"}

    post = db.posts.find_one({"_id": oid})
    if not post:
        return {"ok": False, "msg": "게시글이 존재하지 않습니다."}

    # if require_closed and post.get("status") != "closed":
    #     return {"ok": False, "msg": "게시글이 'closed' 상태가 아니어서 발송하지 않습니다.", "status": post.get("status")}

    # 2) 수신자 이메일 수집
    
    emails = collect_participant_emails(oid)  # 기존 헬퍼 재사용

    # 3) 메일 콘텐츠 준비 (closing_date 표준화)
    closing_dt = post.get("closing_date") or post.get("closing_time") or now
    if isinstance(closing_dt, datetime.datetime):
        closing_date_str = closing_dt.strftime("%Y-%m-%d %H:%M:%S")
    else:
        # 문자열/기타 타입 대비
        closing_date_str = str(closing_dt)

    subject, text, html = render_close_email(post, closing_date_str)

    # 4) dry-run: 실제 발송 없이 프리뷰만
    if dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "emails": emails,
            "subject": subject,
            "html_preview": html[:4000],
        }

    # 5) 실제 발송
    sent_summary = None
    if notify and emails:
        sent_summary = send_bulk_individually(emails, subject, text=text, html=html)

    # 6) 발송 로그 (상태 변경 없음)
    db.mail_logs.insert_one({
        "post_id": oid,
        "subject": subject,
        "emails": emails,
        "result": sent_summary,
        "notified": bool(notify and emails),
        "created_at": now
    })

    return {
        "ok": True,
        "emails": len(emails),
        "sent": sent_summary
    }


# ----------------------------

@app.route("/")
def index():
    if "user" not in session:
        return redirect(url_for("login"))

    page = int(request.args.get("page", 1))
    per_page = 10

    total_posts = posts_collection.count_documents({})
    posts = list(
        posts_collection.find({})
        .sort("created_at", -1)
        .skip((page - 1) * per_page)
        .limit(per_page)
    )
    total_pages = (total_posts + per_page - 1) // per_page

    # 현재 요청 쿼리 복사 (딕셔너리 형태로)
    query_params = request.args.to_dict()
    query_params.pop("page", None)  # 기존 페이지 제거

    return render_template(
        "index.html",
        posts=posts,
        page=page,
        total_pages=total_pages,
        query_params=query_params,
    )


@app.route("/clear")
def clear():
    posts_collection.drop()
    return redirect(url_for("index"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        id = request.form.get("id")
        pw = request.form.get("pw")

        user = db.users.find_one({"id": id})
        if user and bcrypt.checkpw(pw.encode("utf-8"), user["pw"]):
            session["user"] = id
            return redirect(url_for("index"))
        else:
            return render_template(
                "login.html", error="로그인 실패: 아이디 또는 비밀번호 오류"
            )

    return render_template("login.html")


@app.route("/mypage", methods=["GET"])
def mypage():
    per_page = 3
    # 내가 작성한 글
    allMyPosts = list(db.posts.find({"author": session["user"]}))

    # 페이징
    page_myPosts = int(request.args.get("page_myPosts", 1))

    # 페이징 고려해 목록 추출
    myPosts = list(
        db.posts.find({"author": session["user"]})
        .skip((page_myPosts - 1) * per_page)
        .limit(per_page)
    )

    total_posts_myPosts = len(allMyPosts)
    total_pages_myPosts = (total_posts_myPosts + per_page - 1) // per_page

    # 내가 참석한 글
    # 참석자 테이블에서 post의 id목록 조회 -> 조회한 post id로 post목록 조회
    # 1. participant에서 post_id 목록 조회
    post_ids = participants_collection.find(
        {"user_id": session["user"]}, {"post_id": 1, "_id": 0}
    )
    post_ids = [p["post_id"] for p in post_ids]

    # 타입 맞추기: posts._id가 ObjectId라면 문자열을 ObjectId로 변환
    post_ids = [
        ObjectId(x) if isinstance(x, str) else x for x in post_ids if x is not None
    ]

    # 2. posts 테이블에서 해당 post 목록 조회
    allApplyPosts = list(db.posts.find({"_id": {"$in": post_ids}}))

    # 페이징
    page_applyPosts = int(request.args.get("page_applyPosts", 1))
    # per_page = 10

    # 페이징 고려해 목록 추출
    applyPosts = list(
        db.posts.find({"_id": {"$in": post_ids}})
        .skip((page_applyPosts - 1) * per_page)
        .limit(per_page)
    )

    total_posts_applyPosts = len(allApplyPosts)
    total_pages_applyPosts = (total_posts_applyPosts + per_page - 1) // per_page

    # 현재 요청 쿼리 복사 (딕셔너리 형태로)
    query_params = request.args.to_dict()
    query_params.pop("page", None)  # 기존 페이지 제거

    # AJAX(부분 요청) 여부: fetch에서 보낸 헤더로 구분
    is_partial = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    ctx = dict(
        myPosts=myPosts,
        page_myPosts=page_myPosts,
        total_pages_myPosts=total_pages_myPosts,
        applyPosts=applyPosts,
        page_applyPosts=page_applyPosts,
        total_pages_applyPosts=total_pages_applyPosts,
        query_params=query_params,
    )

    if is_partial:
        return render_template("partials/_apply_wrap.html", **ctx)  # 조각만 반환

    return render_template(
        "mypage.html",
        myPosts=myPosts,
        per_page=per_page,
        page_myPosts=page_myPosts,
        total_pages_myPosts=total_pages_myPosts,
        page_applyPosts=page_applyPosts,
        total_pages_applyPosts=total_pages_applyPosts,
        query_params=query_params,
        applyPosts=applyPosts,
    )


@app.route("/api/ranking")
def get_top_ranking():
    category = request.args.get("category", "전체")

    query = {}
    if category != "전체":
        query["category"] = category

    posts = list(posts_collection.find(query).sort("viewcount", -1).limit(3))

    # JSON 직렬화를 위해 datetime과 ObjectId 처리
    for post in posts:
        post["_id"] = str(post["_id"])
        post["created_at"] = (
            post.get("created_at", "").strftime("%Y-%m-%d %H:%M")
            if post.get("created_at")
            else ""
        )
        post["viewcount"] = post.get("viewcount", 0)

    return jsonify(posts)


@app.route("/join", methods=["GET", "POST"])
def join():
    if request.method == "GET":
        return render_template("join.html")

    id = request.form.get("id")
    username = request.form.get("username")
    pw = request.form.get("pw")
    pw1 = request.form.get("pw1")
    email = request.form.get("email")

    if pw != pw1:
        return render_template("join.html", error="비밀번호가 일치하지 않습니다.")
    if db.users.find_one({"id": id}):
        return render_template("join.html", error="이미 존재하는 ID입니다.")

    hashed_pw = bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt())
    db.users.insert_one({"id": id, "username": username, "pw": hashed_pw, "email" : email})

    return redirect("/")


@app.route("/search", methods=["GET"])
def search():
    if "user" not in session:
        return redirect(url_for("login"))

    category = request.args.get("category")
    keyword = request.args.get("search")
    sort = request.args.get("sort")
    page = int(request.args.get("page", 1))
    per_page = 10

    query = {}
    if category and category != "전체":
        query["category"] = category

    if keyword:
        query["$or"] = [
            {"title": {"$regex": keyword, "$options": "i"}},
            {"content": {"$regex": keyword, "$options": "i"}},
        ]

    sort_option = [("created_at", -1)]
    if sort == "closest":
        sort_option = [("closing_date", 1)]
    elif sort == "viewcount":
        sort_option = [("viewcount", -1)]

    total_posts = posts_collection.count_documents(query)
    posts = list(
        posts_collection.find(
            query,
        )
        .sort(sort_option)
        .skip((page - 1) * per_page)
        .limit(per_page)
    )
    total_pages = (total_posts + per_page - 1) // per_page

    # 여기서 query_params 만들어서 넘겨주기
    query_params = request.args.to_dict()
    query_params.pop("page", None)  # page 파라미터는 따로 처리하니까 빼기

    return render_template(
        "index.html",
        posts=posts,
        selected_category=category,
        search=keyword,
        sort=sort,
        page=page,
        total_pages=total_pages,
        query_params=query_params,  # 추가!
    )


@app.route("/logout")
def logout():
    session.pop("user", None)
    return redirect("/")


@app.route("/post/<id>", methods=["GET"])
def post(id):
    # 게시글을 먼저 조회 (존재 확인 및 기존 viewcount 확보)
    post = posts_collection.find_one({"_id": ObjectId(id)})
    if not post:
        return "게시글을 찾을 수 없습니다.", 404

    # 조회수 1 증가
    posts_collection.update_one({"_id": ObjectId(id)}, {"$inc": {"viewcount": 1}})

    # 업데이트 후 최신 데이터로 다시 조회 (선택사항)
    post["viewcount"] += 1  # 이미 기존 데이터를 가져왔으므로 수동으로 1 증가시켜도 무방

    # 참여자 목록
    participants = list(participants_collection.find({"post_id": id}))

    # 댓글 목록
    replies = list(reply_collection.find({"post_id": id}))

    # 본인 글 여부
    isMyPost = (
        db.posts.find_one({"_id": ObjectId(id), "author": session["user"]}, {"_id": 1})
        is not None
    )

    # 신청이력
    alreadyApply = (
        participants_collection.find_one(
            {"post_id": id, "user_id": session["user"]}, {"_id": 1}
        )
        is not None
    )

    shapes_json = ""

    if post.get("category") == "시설이용":
        doc = posts_collection.find_one({"_id": ObjectId(id)}, {"rect": 1, "_id": 0})
        rect_value = (doc or {}).get("rect", "[]")
        shapes = json.loads(rect_value)  # ← 중요
        shapes_json = json.dumps(shapes)  # 문자열로 변환

    return render_template(
        "postDetail.html",
        msg=request.args.get("msg"),
        post=post,
        isMyPost=isMyPost,
        alreadyApply=alreadyApply,
        participants=participants,
        replies=replies,
        shapes_json=shapes_json,
    )


@app.route("/post/new", methods=["GET", "POST"])
def new_post():
    if request.method == "POST":
        title = request.form.get("title")
        author = request.form.get("author")
        required = request.form.get("required")
        viewcount = 0
        status = 0
        category = request.form.get("category")
        content = request.form.get("content")

        closing_date = request.form.get("closing_date")
        closing_time = request.form.get("closing_time")
        start_date = request.form.get("start_date")
        start_time = request.form.get("start_time")
        distance = request.form.get("distance")
        runningPoints = request.form.get("runningPoints")
        dest = request.form.get("dest")
        taxi_dest = request.form.get("taxi_destination")
        taxi_fee = request.form.get("fare")
        if taxi_fee:
            taxi_fee = str(math.floor(float(taxi_fee[:-2])))
        else:
            taxi_fee = None
        dest_lat = request.form.get("dest_lat")
        dest_lng = request.form.get("dest_lng")
        facility = request.form.get("facility")
        facilityDetail = request.form.get("facilityDetail")

        # 사진 있는 케이스 구분
        # picture = request.files.get("picture")
        # picture_url = None

        # if picture:
        #     filename = secure_filename(picture.filename)
        #     picture.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
        #     picture_url = f"/static/uploads/{filename}"  # 정적 경로로 접근 가능하도록

        # 시설정보
        # facility = request.form.get("facility")
        # facilityDetail = request.form.get("facilityDetail")
        rect = request.form.get("rect")

        # elif(category == "기타"):
        picture = request.files.get("picture")
        picture_url = None

        if picture:
            filename = secure_filename(picture.filename)
            picture.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
            picture_url = "/static/uploads/{filename}"  # 정적 경로로 접근 가능하도록

        post = {
            "title": title,
            # "picture": picture_url,
            "author": author,
            "viewcount": viewcount,
            "required": required,
            "category": category,
            "statu": 1,
            "status": status,
            "closing_date": closing_date,
            "closing_time": closing_time,
            "start_date": start_date,
            "start_time": start_time,
            "content": content,
            "created_at": datetime.datetime.now(ZoneInfo("Asia/Seoul")),
            "distance": distance,
            "runningPoints": runningPoints,
            "dest": dest,
            "taxi_dest": taxi_dest,
            "taxi_fee": taxi_fee,
            "dest_lat": dest_lat,
            "dest_lng": dest_lng,
            "facility": facility,
            "facilityDetail": facilityDetail,
            "rect": rect,
            "picture": picture_url,
            # "facility" : facility,
            # "facilityDetail" : facilityDetail
        }
        posts_collection.insert_one(post)
        return redirect("/")
    return render_template("createPost.html")


@app.route("/post/participate/<id>", methods=["POST"])
def participate(id):
    userid = session.get("user")
    if not userid:
        return redirect(url_for("login"))

    post = posts_collection.find_one({"_id": ObjectId(id)})
    if not post:
        return "게시글을 찾을 수 없습니다.", 404

    # 참석한 적 있는지
    alreadyApply = (
        db.participants.find_one({"post_id": id, "user_id": userid}, {"_id": 1})
        is not None
    )

    if alreadyApply:
        return redirect(url_for("post", id=id, msg="already"))
    else:
        TZ = "Asia/Seoul"
        res = posts_collection.find_one_and_update(
            {
                "_id": ObjectId(id),
                "$expr": {
                    "$and": [
                        # 1) 마감 전이어야 한다: close_date + close_time > NOW
                        {
                            "$gt": [
                                {
                                    "$dateFromString": {
                                        "dateString": {
                                            # "YYYY-MM-DD HH:mm"로 합쳐서 파싱
                                            "$concat": [
                                                {
                                                    "$ifNull": [
                                                        "$closing_date",
                                                        "9999-12-31",
                                                    ]
                                                },
                                                " ",
                                                {"$ifNull": ["$closing_time", "23:59"]},
                                            ]
                                        },
                                        "format": "%Y-%m-%d %H:%M",
                                        "timezone": TZ,
                                    }
                                },
                                # 비교 기준 NOW (분 단위로 잘라서 미세초 차이 방지)
                                {
                                    "$dateTrunc": {
                                        "date": "$$NOW",
                                        "unit": "minute",
                                        "timezone": TZ,
                                    }
                                },
                            ]
                        },
                        # 2) 좌석 조건: required - status > 0
                        {
                            "$gt": [
                                {
                                    "$subtract": [
                                        {"$toInt": {"$ifNull": ["$required", 0]}},
                                        {"$toInt": {"$ifNull": ["$status", 0]}},
                                    ]
                                },
                                0,
                            ]
                        },
                    ]
                },
                # 3) (선택) 동일 유저 중복 방지
                "applicants": {"$ne": userid},
            },
            {"$inc": {"status": 1}},
            return_document=ReturnDocument.AFTER,
        )

        if res is not None:
            participants_collection.insert_one(
                {
                    "user_id": userid,
                    "post_id": id,
                    "participated_time": datetime.datetime.now(ZoneInfo("Asia/Seoul")),
                }
            )
            return redirect(url_for("mypage"))
        else:
            doc = posts_collection.find_one(
                {"_id": ObjectId(id)},
                {"required": 1, "status": 1, "closing_date": 1, "closing_time": 1},
            )
            close_date_str = doc.get("closing_date")
            close_time_str = doc.get("closing_time")

            close_datetime = datetime.datetime.strptime(
                f"{close_date_str} {close_time_str}", "%Y-%m-%d %H:%M"
            )

            if datetime.datetime.now() > close_datetime:
                msg = "모집 기간이 지났습니다."
                return redirect(url_for("post", id=id, msg=msg))

            req = int(doc.get("required", 0)) if doc else 0
            stat = int(doc.get("status", 0)) if doc else 0
            msg = (
                "모집인원이 다 찼습니다"
                if (req - stat) <= 0
                else "오류가 발생했습니다. 개발자에게 문의주세요"
            )
            return redirect(url_for("post", id=id, msg=msg))


@app.route("/post/cancel/<id>", methods=["POST"])
def cancel_post(id):
    userid = session.get("user")
    if not userid:
        return redirect(url_for("login"))

    post = posts_collection.find_one({"_id": ObjectId(id)})
    if not post:
        return "게시글을 찾을 수 없습니다.", 404

    # 참석한 적 있는지
    alreadyApply = (
        db.participants.find_one({"post_id": id, "user_id": userid}, {"_id": 1})
        is not None
    )

    if not alreadyApply:
        posts_collection.update_one({"_id": ObjectId(id)}, {"$inc": {"status": -1}})

        result = participants_collection.delete_one({"post_id": id, "user_id": userid})
        if result.deleted_count == 1:
            return redirect(url_for("mypage"))
        else:
            return "신청 중 에러 발생.", 404
    else:
        return jsonify({"result": "fail", "msg": "참여 이력을 찾을 수 없습니다."})


@app.route("/post/close/<id>", methods=["POST"])
def close_post(id):
    result = posts_collection.update_one({"_id": ObjectId(id)}, {"$set": {"statu": 0}})
    if result.modified_count > 0:
        send_post_closing_notifications(id, notify=True, dry_run=False, require_closed=True)       
        return redirect(url_for("mypage"))
    else:
        return redirect(url_for("post", id=id, msg="마감 실패"))


@app.route("/post/update/<id>", methods=["POST"])
def update_post(id):
    result = posts_collection.update_one(
        {"_id": ObjectId(id)},
        {
            "$set": {
                "title": request.form.get("title"),
                "required": request.form.get("required"),
                "category": request.form.get("category"),
                "content": request.form.get("content"),
                "closing_date": request.form.get("closing_date"),
                "closing_time": request.form.get("closing_time"),
                "start_date": request.form.get("start_date"),
                "start_time": request.form.get("start_time"),
                "rect": request.form.get("rect"),
            }
        }
    )

    if result.modified_count > 0:
        return redirect(url_for("mypage"))
    else:
        return redirect(url_for("post", id=id, msg="수정 실패"))



@app.route("/ajax/reply", methods=["POST"])
def insert_reply():
    data = request.get_json()
    reply_collection.insert_one(
        {
            "post_id": data["postId"],
            "user_id": data["userId"],
            "replyContent": data["replyContent"],
            "created_at": data["created_at"],
        }
    )

    return jsonify(ok=True, data=data)


@app.route("/post/delete", methods=["POST"])
def delete_post():
    title = request.form.get("title")
    content = request.form.get("content")

    result = posts_collection.delete_one({"title": title, "content": content})
    if result.deleted_count == 1:
        return redirect("/")
    else:
        return jsonify({"result": "fail", "msg": "게시글을 찾을 수 없습니다."})


if __name__ == "__main__":
    app.run("0.0.0.0", port=5000, debug=True)
