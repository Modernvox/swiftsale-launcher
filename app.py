# callback_server/app.py
from flask import Flask, request
import json
import os

app = Flask(__name__)

# File to store the authorization code (temporary storage)
CODE_FILE = "/tmp/auth_code.json"  # On Heroku, use /tmp for temporary storage

@app.route('/callback')
def callback():
    code = request.args.get('code')
    state = request.args.get('state')
    if code:
        # Save the code and state to a file
        with open(CODE_FILE, 'w') as f:
            json.dump({'code': code, 'state': state}, f)
        return "Authentication successful! You can close this window and return to SwiftSale."
    return "Error: No authorization code received.", 400

@app.route('/get-auth-code')
def get_auth_code():
    if os.path.exists(CODE_FILE):
        with open(CODE_FILE, 'r') as f:
            data = json.load(f)
        os.remove(CODE_FILE)  # Clean up after retrieval
        return json.dumps(data)
    return json.dumps({'error': 'No authorization code available'}), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5001)))