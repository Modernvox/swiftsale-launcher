# oauth_callback.py

from flask import Flask, request, jsonify
import json
import os

app = Flask(__name__)

# Temporary file for storing the OAuth authorization code
# Heroku dynos can only write to /tmp at runtime
CODE_FILE = "/tmp/auth_code.json"

@app.route('/callback')
def callback():
    """
    OAuth redirect endpoint. Receives the authorization code and state,
    saves them to a file for later retrieval, and informs the user.
    """
    code = request.args.get('code')
    state = request.args.get('state')
    if code:
        # Write code and state to temporary storage
        with open(CODE_FILE, 'w') as f:
            json.dump({'code': code, 'state': state}, f)
        return (
            "Authentication successful!<br>"
            "You may close this window and return to the SwiftSale app."
        )
    return "Error: No authorization code received.", 400

@app.route('/get-auth-code')
def get_auth_code():
    """
    Polling endpoint for the desktop app.
    Returns the stored code and state, then cleans up the file.
    """
    if os.path.exists(CODE_FILE):
        with open(CODE_FILE, 'r') as f:
            data = json.load(f)
        os.remove(CODE_FILE)
        return jsonify(data)
    return jsonify({'error': 'No authorization code available'}), 404

if __name__ == '__main__':
    # Listen on PORT (default 5001) and all interfaces for Heroku compatibility
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port)
