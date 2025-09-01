from flask import Flask, render_template, request, redirect, url_for, jsonify
from pymongo import MongoClient
from datetime import datetime

app = Flask(__name__)

client = MongoClient("mongodb://test:1234@localhost:27017/")
db = client.simple_board_db
posts_collection = db.posts

@app.route('/')
def index():
    posts = list(posts_collection.find({}, {'_id': False}).sort('created_at', -1))
    return render_template('index.html', posts=posts)

@app.route('/post/new', methods=['GET', 'POST'])
def new_post():
    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')

        if not title or not content:
            return "제목과 내용을 모두 입력해주세요.", 400

        post = {
            'title': title,
            'content': content,
            'created_at': datetime.utcnow()
        }
        posts_collection.insert_one(post)
        return redirect(url_for('index'))
    
    return render_template('new_post.html')

@app.route('/post/delete', methods=['POST'])
def delete_post():
    title = request.form.get('title')
    content = request.form.get('content')

    result = posts_collection.delete_one({'title': title, 'content': content})
    if result.deleted_count == 1:
        return jsonify({'result': 'success'})
    else:
        return jsonify({'result': 'fail', 'msg': '게시글을 찾을 수 없습니다.'})

if __name__ == '__main__':
    app.run('0.0.0.0', port=5000, debug=True)
