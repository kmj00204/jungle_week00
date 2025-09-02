from flask import Flask, render_template, request, redirect, url_for, jsonify, session
from pymongo import MongoClient
from datetime import datetime
import bcrypt
import os
from werkzeug.utils import secure_filename
from bson import ObjectId

app = Flask(__name__)
app.secret_key = "secret_key"

client = MongoClient("mongodb://test:1234@localhost:27017/")
db = client.simple_board_db
posts_collection = db.posts

app.config["UPLOAD_FOLDER"] = "./static/uploads"


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
    return render_template("mypage.html")


@app.route("/join", methods=["GET", "POST"])
def join():
    if request.method == "GET":
        return render_template("join.html")

    id = request.form.get("id")
    username = request.form.get("username")
    pw = request.form.get("pw")
    pw1 = request.form.get("pw1")

    if pw != pw1:
        return render_template("join.html", error="비밀번호가 일치하지 않습니다.")
    if db.users.find_one({"id": id}):
        return render_template("join.html", error="이미 존재하는 ID입니다.")

    hashed_pw = bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt())
    db.users.insert_one({"id": id, "username": username, "pw": hashed_pw})

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
        sort_option = [("deadline", 1)]
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

    return render_template("post_detail.html", post=post)


@app.route("/post/new", methods=["GET", "POST"])
def new_post():
    if request.method == "POST":
        title = request.form.get("title")
        deadline = request.form.get("deadline")
        required = request.form.get("required")
        author = session["user"]
        viewcount = 0
        status = 0
        category = request.form.get("category")

        picture = request.files.get("picture")
        picture_url = None

        if picture:
            filename = secure_filename(picture.filename)
            picture.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
            picture_url = f"/static/uploads/{filename}"  # 정적 경로로 접근 가능하도록

        post = {
            "title": title,
            "picture": picture_url,
            "author": author,
            "viewcount": viewcount,
            "deadline": deadline,
            "required": required,
            "status": status,
            "category": category,
            "statu": 1,
        }
        posts_collection.insert_one(post)
        return redirect("/")
    return render_template("new_post.html")


@app.route("/post/participate/<id>", methods=["GET"])
def participate(id):
    userid = session.get("user")
    if not userid:
        return redirect(url_for("login"))

    post = posts_collection.find_one({"_id": ObjectId(id)})
    if not post:
        return "게시글을 찾을 수 없습니다.", 404

    participants = post.get("participants", [])

    if userid in participants:
        return redirect(url_for("post", id=id, msg="already"))
    else:
        posts_collection.update_one(
            {"_id": ObjectId(id)}, {"$push": {"participants": userid}}
        )
        return redirect(url_for("post", id=id, msg="success"))


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
