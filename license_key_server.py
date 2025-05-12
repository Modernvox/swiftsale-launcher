import hashlib
import random
import string
import sqlite3
from flask import Flask, request, render_template_string
import sendgrid
from sendgrid.helpers.mail import Mail, Email, To, Content

app = Flask(__name__)

SENDGRID_API_KEY = "your_sendgrid_api_key"
SENDER_EMAIL = "your_email@example.com"
sg = sendgrid.SendGridAPIClient(api_key=SENDGRID_API_KEY)

# Initialize SQLite database
def init_db():
    conn = sqlite3.connect('license_keys.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS licenses
                 (key TEXT PRIMARY KEY, email TEXT, tier TEXT, subscription_id TEXT, status TEXT)''')
    conn.commit()
    conn.close()

init_db()

def generate_license_key(tier):
    if tier not in ["Bronze", "Silver", "Gold"]:
        print(f"Invalid tier: {tier}")
        return None
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))
    checksum = hashlib.sha256((tier + code).encode()).hexdigest()[-4:].upper()
    return f"{tier}-{code}-{checksum}"

@app.route('/')
def home():
    landing_page_html = """
    <html>
    <head><title>SwiftSale</title></head>
    <body>
        <h1>Welcome to SwiftSale</h1>
        <p>Powering Whatnot Auctions with Precision</p>
        <p><a href="https://checkoutpage.co/s/your-username/swiftsale-plans">Purchase a Subscription</a></p>
    </body>
    </html>
    """
    return render_template_string(landing_page_html)

@app.route('/webhook', methods=['POST'])
def handle_webhook():
    data = request.json
    print(f"Received webhook: {data}")
    
    conn = sqlite3.connect('license_keys.db')
    c = conn.cursor()

    event = data.get('event')
    
    if event == 'payment.succeeded':
        tier = data['product']['name']
        email = data['customer']['email']
        subscription_id = data.get('subscription', {}).get('id')
        
        if "Bronze" in tier:
            tier_name = "Bronze"
        elif "Silver" in tier:
            tier_name = "Silver"
        elif "Gold" in tier:
            tier_name = "Gold"
        else:
            print(f"Unknown tier: {tier}")
            return "Invalid tier", 400

        license_key = generate_license_key(tier_name)
        if not license_key:
            print(f"Failed to generate license key for tier: {tier_name}")
            return "Invalid tier", 400

        c.execute("INSERT INTO licenses (key, email, tier, subscription_id, status) VALUES (?, ?, ?, ?, ?)",
                  (license_key, email, tier_name, subscription_id, "active"))
        conn.commit()

        message = Mail(
            from_email=Email(SENDER_EMAIL),
            to_emails=To(email),
            subject="Your SwiftSale License Key",
            plain_text_content=Content(
                "text/plain",
                f"Thank you for purchasing the {tier_name} plan!\n\n"
                f"Your License Key: {license_key}\n\n"
                "Open SwiftSale, go to Settings, enter your tier, paste this key, and click 'Validate Key' to activate your subscription."
            )
        )
        try:
            response = sg.send(message)
            print(f"Email sent to {email}: {license_key}, SendGrid response: {response.status_code}")
        except Exception as e:
            print(f"Email failed: {str(e)}")
            return "Email failed", 500

    elif event == 'invoice.payment_failed':
        subscription_id = data['subscription']['id']
        c.execute("SELECT email FROM licenses WHERE subscription_id = ?", (subscription_id,))
        result = c.fetchone()
        if result:
            email = result[0]
            message = Mail(
                from_email=Email(SENDER_EMAIL),
                to_emails=To(email),
                subject="SwiftSale Payment Failed",
                plain_text_content=Content(
                    "text/plain",
                    "We were unable to process your payment for your SwiftSale subscription.\n\n"
                    "Please update your payment method in the customer portal: https://checkoutpage.co/customer-portal\n\n"
                    "Your access will be restricted until payment is resolved."
                )
            )
            sg.send(message)
        return "Payment failure noted", 200

    elif event == 'customer.subscription.updated':
        subscription_id = data['subscription']['id']
        status = data['subscription']['status']
        new_status = "active" if status == "active" else "inactive"
        if status == "canceled":
            c.execute("UPDATE licenses SET status = 'inactive' WHERE subscription_id = ?", (subscription_id,))
            conn.commit()
            print(f"Marked license key as inactive for subscription: {subscription_id}")
        else:
            c.execute("UPDATE licenses SET status = ? WHERE subscription_id = ?", (new_status, subscription_id))
            conn.commit()
            print(f"Updated license key status to {new_status} for subscription: {subscription_id}")
        return "Success", 200

    conn.close()
    print(f"Event not handled: {event}")
    return "Event not handled", 200

@app.route('/check_license', methods=['POST'])
def check_license():
    data = request.json
    license_key = data.get('key')
    if not license_key:
        return {"status": "invalid", "message": "License key required"}, 400
    
    conn = sqlite3.connect('license_keys.db')
    c = conn.cursor()
    c.execute("SELECT status, tier FROM licenses WHERE key = ?", (license_key,))
    result = c.fetchone()
    conn.close()

    if result:
        status, tier = result
        return {"status": status, "tier": tier}, 200
    return {"status": "invalid", "message": "License key not found"}, 404

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=5001)