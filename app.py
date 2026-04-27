from flask import Flask, jsonify

app = Flask(__name__)

@app.route('/')
def home():
    return {"status": "server running"}

@app.route('/api/test')
def test():
    return {"message": "API working"}

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
