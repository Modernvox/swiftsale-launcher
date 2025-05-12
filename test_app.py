import sqlite3
import threading
import requests
import webbrowser
import tkinter as tk
from tkinter import messagebox, ttk, filedialog
from PIL import Image, ImageTk
import os
import sys
import datetime
import csv
import logging
from logging.handlers import RotatingFileHandler
import pyperclip
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO
import stripe
from dotenv import load_dotenv
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from tenacity import retry, stop_after_attempt, wait_exponential
import asyncio
import warnings
import socket
import time

# Suppress urllib3 warning from python-telegram-bot
warnings.filterwarnings("ignore", category=UserWarning, module="telegram")

# Check for required dependencies
try:
    from telegram import Bot
except ImportError:
    Bot = None
    logging.warning("python-telegram-bot not installed. Telegram features will be disabled.")

# Define PRIMARY_COLOR constant
PRIMARY_COLOR = "#0288D1"

# Setup logging with file rotation
log_dir = os.path.join(os.path.expanduser("~"), "Documents", "SwiftSale")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "swiftsale.log")

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
console_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))

file_handler = RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=5)
file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))

logger.addHandler(console_handler)
logger.addHandler(file_handler)

# Resource path helper for PyInstaller
def get_resource_path(relative_path):
    base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)

# Load environment variables securely
load_dotenv()

# Configure Stripe securely
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

if not STRIPE_SECRET_KEY or not WEBHOOK_SECRET:
    logging.error("Stripe API keys are missing from the environment. Check your .env file.")
    raise ValueError("Stripe API keys not configured.")

stripe.api_key = STRIPE_SECRET_KEY

PRICE_MAP = {
    "Trial":  "price_1RLcG9J7WrcpTNl63DovHUbe",
    "Bronze": "price_1RLcKcJ7WrcpTNl63TEVWY7F",
    "Silver": "price_1RLcKcJ7WrcpTNl6jT7sLvmU",
    "Gold":   "price_1RLcP4J7WrcpTNl6a8aHdSgv",
}

class SCDWhatnotGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("SwiftSale(TM) - Powering Whatnot Auctions with Precision")
        self.root.geometry("1000x700")
        self.root.configure(bg="#2E2E2E")

        try:
            # Initialize SQLite database
            self.db_path = os.path.join(log_dir, "swiftsale.db")
            self.init_db()

            # Get user email (simulated login)
            self.user_email = os.getenv("USER_EMAIL")
            if not self.user_email:
                self.user_email = self.prompt_login()
                if not self.user_email:
                    raise ValueError("User email required.")

            # Initialize Telegram bot
            bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
            self.telegram_error_shown = False
            if Bot and bot_token:
                try:
                    self.bot = Bot(token=bot_token)
                    logging.info("Telegram bot initialized successfully.")
                except Exception as e:
                    logging.error(f"Failed to initialize Telegram bot: {e}", exc_info=True)
                    self.telegram_error_shown = True
                    messagebox.showwarning("Telegram Warning", f"Failed to initialize Telegram bot: {e}")
            else:
                logging.warning("Telegram bot token not found or python-telegram-bot not installed.")

            # Initialize asyncio loop
            self.loop = None
            def run_asyncio_loop():
                self.loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self.loop)
                self.loop.run_forever()

            self.loop_thread = threading.Thread(target=run_asyncio_loop, daemon=True)
            self.loop_thread.start()
            time.sleep(0.1)  # Ensure loop is initialized

            # Data initialization
            self.bidders = {}
            self.next_bin = 1
            self.next_giveaway_num = 1
            self.giveaway_count = 0
            self.last_bidder = None
            self.show_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            self.show_start_time = datetime.datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
            self.bidder_csv_path = os.path.join(log_dir, f"bidder_history_{self.show_id}.csv")
            self.sort_order = 'desc'
            self.latest_bin_assignment = "Waiting for bidder..."

            # Tier system configuration
            self.tier_limits = {
                "Trial": {"bins": 20},
                "Bronze": {"bins": 50},
                "Silver": {"bins": 150},
                "Gold": {"bins": 300}
            }
            self.valid_tiers = list(self.tier_limits.keys())

            # Load subscription from database
            self.load_subscription()
            self.tier_var = tk.StringVar(value=self.tier)
            self.max_bins = self.tier_limits.get(self.tier, {'bins': 50})['bins']

            # Load Telegram chat ID from database
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT chat_id FROM settings WHERE user_email = ?", (self.user_email,))
            result = cursor.fetchone()
            self.chat_id = result[0] if result else ""
            conn.close()

            # GUI settings (loaded from database or defaults)
            self.top_buyer_text = "Great job, {username}! You've snagged {count} items!"
            self.giveaway_announcement_text = (
                "Giveaway #{number} Alert! Must be following us & share the stream to enter! "
                "Winner announced in a few minutes!"
            )
            self.flash_sale_announcement_text = (
                "Flash Sale Alert! Grab these deals before the timer runs out!"
            )

            # Secure API token
            self.api_token = os.urandom(16).hex()

            # Initialize Flask and SocketIO
            self.flask_app = Flask(__name__)
            self.flask_app.config['SECRET_KEY'] = os.urandom(24).hex()
            self.limiter = Limiter(get_remote_address, app=self.flask_app)
            self.socketio = SocketIO(self.flask_app, cors_allowed_origins="*", async_mode="threading")

            @self.flask_app.route('/')
            def index():
                return render_template('index.html')

            @self.flask_app.route('/get_latest')
            def get_latest():
                return jsonify({'data': self.latest_bin_assignment})

            @self.flask_app.route('/success')
            def success():
                return render_template('success.html')

            @self.flask_app.route('/cancel')
            def cancel():
                return render_template('cancel.html')

            @self.flask_app.route('/shutdown', methods=['GET'])
            def shutdown():
                func = request.environ.get('werkzeug.server.shutdown')
                if func is None:
                    logging.warning("Shutdown not supported in this environment")
                    return 'Server shutting down...', 200
                func()
                logging.info("Flask server shut down")
                return 'Server shut down', 200

            @self.socketio.on('connect')
            def handle_connect():
                self.socketio.emit('update', {'data': self.latest_bin_assignment})
                logging.info("Flask client connected")

            @self.flask_app.route('/create-checkout-session', methods=['POST'])
            @self.limiter.limit("10 per minute")
            @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=10))
            def create_checkout_session():
                try:
                    token = request.headers.get('Authorization')
                    if not token or token != f'Bearer {self.api_token}':
                        logging.warning("Unauthorized access to create-checkout-session")
                        return jsonify({'error': 'Unauthorized'}), 401
                    data = request.get_json(force=True)
                    tier = data.get('tier')
                    price_id = PRICE_MAP.get(tier)

                    if not price_id:
                        logging.error(f"Invalid tier: {tier}")
                        return jsonify({'error': 'Invalid tier'}), 400

                    session = stripe.checkout.Session.create(
                        payment_method_types=['card'],
                        line_items=[{'price': price_id, 'quantity': 1}],
                        mode='subscription',
                        success_url=request.url_root + 'success?subscription_id={CHECKOUT_SESSION_ID}',
                        cancel_url=request.url_root + 'cancel',
                        customer_email=self.user_email
                    )
                    logging.info(f"Created Stripe checkout session for tier: {tier}")
                    return jsonify({'url': session.url})
                except stripe.error.StripeError as e:
                    logging.error(f"Stripe error in checkout session: {e}", exc_info=True)
                    return jsonify({'error': str(e)}), 500
                except Exception as e:
                    logging.error(f"Failed to create checkout session: {e}", exc_info=True)
                    return jsonify({'error': 'Internal server error'}), 500

            @self.flask_app.route('/stripe-webhook', methods=['POST'])
            @self.flask_app.route('/webhook',        methods=['POST'])
            def stripe_webhook():
                logging.info("Stripe webhook received a request")
                try:
                    payload = request.get_data(as_text=True)
                    sig = request.headers.get('Stripe-Signature')
                    event = stripe.Webhook.construct_event(payload, sig, WEBHOOK_SECRET)

                    if event['type'] == 'checkout.session.completed':
                        session = event['data']['object']
                        sub_id = session.get('subscription')
                        if sub_id:
                            subscription = stripe.Subscription.retrieve(sub_id, expand=['items.data.price'])
                            price_id = subscription['items']['data'][0]['price']['id']
                            new_tier = REVERSE_PRICE_MAP.get(price_id, "Trial")

                            if new_tier:
                                logging.info(f"Updating subscription to {new_tier}")
                                self.update_subscription(new_tier, sub_id)
                    elif event['type'] == 'customer.subscription.updated':
                        subscription = event['data']['object']
                        price_id = subscription['items']['data'][0]['price']['id']
                        new_tier = REVERSE_PRICE_MAP.get(price_id, "Trial")

                        logging.info(f"Subscription updated to {new_tier}")
                        self.update_subscription(new_tier, subscription['id'])
                    elif event['type'] == 'customer.subscription.deleted':
                        logging.info("Subscription canceled. Reverting to Trial.")
                        self.update_subscription("Trial", "")
                    elif event['type'] == 'invoice.payment_failed':
                        logging.warning("Payment failed for subscription.")
                        self.root.after(0, lambda: messagebox.showwarning(
                            "Payment Failed", "Your subscription payment failed. Please update your payment method."
                        ))
                except stripe.error.SignatureVerificationError as e:
                    logging.error(f"Webhook signature verification failed: {e}", exc_info=True)
                    return '', 400
                except Exception as e:
                    logging.error(f"Exception in stripe_webhook: {e}", exc_info=True)
                    return '', 500
                return '', 200

            def run_flask():
                try:
                    port = int(os.getenv('PORT', 5000))
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                        try:
                            s.bind(('127.0.0.1', port))
                        except OSError:
                            logging.error(f"Port {port} is already in use")
                            self.root.after(0, lambda: messagebox.showerror(
                                "Flask Error", f"Port {port} is in use. Please free the port or change PORT in .env"
                            ))
                            return
                    self.socketio.run(
                        self.flask_app,
                        host='0.0.0.0',
                        port=port,
                        debug=False
                    )
                    logging.info(f"Flask server started on port {port}")
                except Exception as e:
                    logging.error(f"Flask server failed: {e}", exc_info=True)
                    self.root.after(0, lambda: messagebox.showerror("Flask Error", f"Flask server failed: {e}"))

            self.flask_thread = threading.Thread(target=run_flask, daemon=True)
            self.flask_thread.start()

            # Define style_button function
            def style_button(button):
                button.config(
                    bg="#0288D1",
                    fg="#FFFFFF",
                    font=("Helvetica", 8),
                    activebackground="#0277BD",
                    activeforeground="#FFFFFF",
                    relief="flat",
                    padx=10,
                    pady=5
                )

            # Field info for help buttons
            self.field_info = {
                "search": "Search for bidder by username or partial matches for transactions.",
                "username": "Enter Whatnot username to assign bin number. Click field to auto-paste copied username.",
                "quantity": "Number of items won (default: 1).",
                "weight_class": "Select weight class or 'PU Only' (optional).",
                "giveaway": "Mark as giveaway to assign unique number.",
                "avg_sell_rate": "Calculate average time between item sales and estimated totals. Click to copy.",
                "start_giveaway": "Copies a giveaway announcement to clipboard. Text is customizable in Settings tab.",
                "start_flash_sale": "Copies a flash sale announcement to clipboard. Text is customizable in Settings tab.",
                "top_buyers": "Shows top 3 buyers. Click a name to copy a shoutout message.",
                "add_bidder": "Assigns a bin number to the entered username.",
                "export_bidders": "Exports bidder data to CSV.",
                "import_csv": "Imports bidder data from a CSV to restore a show.",
                "clear_data": "Clears all data and starts a new show.",
                "sort_order": "Sort transactions by timestamp: 'Asc' (oldest first), 'Desc' (newest first).",
                "upgrade": "Select a tier and upgrade via Stripe in the Settings tab."
            }

            # Configure styles
            try:
                style = ttk.Style()
                style.theme_use('clam')
                style.configure(
                    "Treeview",
                    font=("Helvetica", 8),
                    rowheight=20,
                    background="#F5F5F5"
                )
                style.configure(
                    "Treeview.Heading",
                    font=("Helvetica", 8, "bold"),
                    background="#0288D1",
                    foreground="#FFFFFF"
                )
                style.map("Treeview", background=[('selected', '#BBDEFB')])
                logging.info("Treeview styles successfully configured.")
            except Exception as e:
                logging.error(f"Style configuration failed: {e}", exc_info=True)
                messagebox.showerror("Style Error", f"Failed to configure styles: {e}")

            # Header Frame
            header = tk.Frame(root, bg="#212121")
            header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=5, pady=5)

            logo_path = get_resource_path("swiftsale_logo.png")
            try:
                logo_img = Image.open(logo_path).resize((75, 50), Image.Resampling.LANCZOS)
                self.logo = ImageTk.PhotoImage(logo_img)
                tk.Label(header, image=self.logo, bg="#212121").pack(side="left", padx=10)
                logging.info("Logo successfully loaded.")
            except Exception as e:
                logging.warning(f"Failed to load logo: {e}")
                tk.Label(
                    header,
                    text="SwiftSale(TM)",
                    font=("Helvetica", 12, "bold"),
                    fg="#FFFFFF",
                    bg="#212121"
                ).pack(side="left", padx=10)

            self.show_label = tk.Label(
                header,
                text=f"Show ID: {self.show_id} | Tier: {self.tier} (Max {self.max_bins} bins)",
                font=("Helvetica", 8),
                fg="#B0BEC5",
                bg="#212121"
            )
            self.show_label.pack(side="right", padx=10)

            # Tabbed Layout
            self.notebook = ttk.Notebook(root)
            self.notebook.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=10, pady=10)

            root.grid_rowconfigure(1, weight=1)
            root.grid_columnconfigure(0, weight=1)
            root.grid_columnconfigure(1, weight=1)

            # Bidders tab
            self.bidders_tab = tk.Frame(self.notebook, bg="#FFFFFF")
            self.notebook.add(self.bidders_tab, text="Bidders")

            # Add Bidders tab content
            bidders_frame = tk.Frame(self.bidders_tab, bg="#FFFFFF")
            bidders_frame.pack(fill="both", expand=True, padx=5, pady=5)

            tk.Label(bidders_frame, text="Assigned Bins:", font=("Helvetica", 10, "bold"), bg="#FFFFFF", fg="#0288D1").pack(anchor="w", padx=5)
            self.bidders_bin_display = tk.Text(bidders_frame, height=10, width=60, font=("Helvetica", 10), bg="#F5F5F5", state="disabled")
            self.bidders_bin_display.pack(fill="x", padx=5, pady=5)

            def create_context_menu(widget):
                menu = tk.Menu(widget, tearoff=0)
                menu.add_command(label="Copy", command=lambda: widget.event_generate("<<Copy>>"))
                def show_menu(event):
                    menu.post(event.x_root, event.y_root)
                widget.bind("<Button-3>", show_menu)

            create_context_menu(self.bidders_bin_display)

            tk.Label(bidders_frame, text="Assigned Bins (Large Display for Label Matching):", font=("Helvetica", 12, "bold"), bg="#FFFFFF", fg="#0288D1").pack(anchor="w", padx=5, pady=(10, 0))
            self.bidders_bin_display_large = tk.Text(bidders_frame, height=10, width=60, font=("Helvetica", 16), bg="#F5F5F5", state="disabled")
            self.bidders_bin_display_large.pack(fill="x", padx=5, pady=5)
            create_context_menu(self.bidders_bin_display_large)
            self.update_bin_display()

            # Transactions tab
            self.transactions_tab = tk.Frame(self.notebook, bg="#FFFFFF")
            self.notebook.add(self.transactions_tab, text="Transactions")

            # Search controls
            search_frame = tk.Frame(self.transactions_tab, bg="#FFFFFF")
            search_frame.pack(fill="x", padx=5, pady=5)
            search_frame.grid_columnconfigure(1, weight=1)

            tk.Label(search_frame, text="Search:", bg="#FFFFFF", font=("Helvetica", 8)).grid(row=0, column=0, padx=5, sticky="e")
            self.search_entry = tk.Entry(search_frame, width=20, font=("Helvetica", 8), bg="#F5F5F5")
            self.search_entry.grid(row=0, column=1, padx=5, sticky="ew")

            tk.Button(
                search_frame,
                text="?",
                command=lambda: self.show_field_info("search"),
                width=2,
                font=("Helvetica", 8),
                bg="#B0BEC5",
                fg="#FFFFFF"
            ).grid(row=0, column=2)

            find_bidder_button = tk.Button(
                search_frame,
                text="Find Bidder",
                command=self.search_bidders
            )
            style_button(find_bidder_button)
            find_bidder_button.grid(row=0, column=3, padx=5)

            self.search_result = tk.Label(
                search_frame,
                text="",
                wraplength=500,
                bg="#FFFFFF",
                fg=PRIMARY_COLOR,
                font=("Helvetica", 8)
            )
            self.search_result.grid(row=1, column=0, columnspan=4, pady=5, sticky="w")

            # Input controls
            input_frame = tk.Frame(self.transactions_tab, bg="#FFFFFF")
            input_frame.pack(fill="x", padx=5, pady=5)
            input_frame.grid_columnconfigure(1, weight=1)

            self.announcement_text = tk.Text(
                input_frame, height=2, width=50, font=("Helvetica", 8), bg="#F5F5F5", state="disabled"
            )
            self.announcement_text.grid(row=0, column=0, columnspan=6, padx=5, pady=5)

            self.current_bidder_label = tk.Label(
                input_frame, text="", font=("Helvetica", 10, "bold"), bg="#FFFFFF", fg="#0288D1"
            )
            self.current_bidder_label.grid(row=0, column=6, padx=5, sticky="w")

            tk.Label(input_frame, text="Username:", bg="#FFFFFF", fg=PRIMARY_COLOR, font=("Helvetica", 10, "bold")).grid(row=1, column=0, padx=5, sticky="e")
            self.username_entry = tk.Entry(input_frame, width=20, font=("Helvetica", 12, "bold"), bg="#F5F5F5")
            self.username_entry.grid(row=1, column=1, padx=5, pady=10, sticky="ew")
            self.username_entry.bind('<Button-1>', lambda event: self.paste_to_entry(self.username_entry))

            add_bidder_button = tk.Button(input_frame, text="Add Bidder", command=self.add_bidder)
            style_button(add_bidder_button)
            add_bidder_button.grid(row=1, column=3, padx=5, pady=10)

            tk.Label(input_frame, text="Quantity:", bg="#FFFFFF", fg=PRIMARY_COLOR, font=("Helvetica", 8)).grid(row=2, column=0, padx=5, sticky="e")
            self.qty_entry = tk.Entry(input_frame, width=15, font=("Helvetica", 8), bg="#F5F5F5")
            self.qty_entry.insert(0, "1")
            self.qty_entry.grid(row=2, column=1, padx=5)

            tk.Label(input_frame, text="Weight:", bg="#FFFFFF", fg=PRIMARY_COLOR, font=("Helvetica", 8)).grid(row=2, column=3, padx=5, sticky="e")
            self.weight_entry = ttk.Combobox(
                input_frame,
                values=["A", "B", "C", "D", "E", "F", "G", "H", "PU Only"],
                width=10,
                state="readonly",
                font=("Helvetica", 8)
            )
            self.weight_entry.grid(row=2, column=4, padx=5)

            self.giveaway_var = tk.BooleanVar()
            tk.Checkbutton(
                input_frame,
                text="Giveaway",
                variable=self.giveaway_var,
                bg="#FFFFFF",
                fg=PRIMARY_COLOR,
                font=("Helvetica", 8)
            ).grid(row=3, column=1, padx=5, sticky="w")

            avg_sell_rate_button = tk.Button(input_frame, text="Avg Sell Rate", command=lambda: self.show_avg_sell_rate(show_message=True))
            style_button(avg_sell_rate_button)
            avg_sell_rate_button.grid(row=4, column=0, padx=5, pady=5)

            start_giveaway_button = tk.Button(input_frame, text="Start Giveaway", command=self.copy_giveaway_announcement)
            style_button(start_giveaway_button)
            start_giveaway_button.grid(row=4, column=2, padx=5, pady=5)

            start_flash_sale_button = tk.Button(input_frame, text="Start Flash Sale", command=self.copy_flash_sale_announcement)
            style_button(start_flash_sale_button)
            start_flash_sale_button.grid(row=4, column=4, padx=5, pady=5)

            sort_asc_button = tk.Button(input_frame, text="Sort Asc", command=lambda: self.set_sort_order('asc'))
            style_button(sort_asc_button)
            sort_asc_button.grid(row=5, column=0, padx=5, pady=5)

            sort_desc_button = tk.Button(input_frame, text="Sort Desc", command=lambda: self.set_sort_order('desc'))
            style_button(sort_desc_button)
            sort_desc_button.grid(row=5, column=1, padx=5, pady=5)

            export_bidders_button = tk.Button(input_frame, text="Export Bidders", command=self.print_bidders)
            style_button(export_bidders_button)
            export_bidders_button.grid(row=6, column=0, padx=5, pady=5)

            import_csv_button = tk.Button(input_frame, text="Import CSV", command=self.import_csv)
            style_button(import_csv_button)
            import_csv_button.grid(row=6, column=2, padx=5, pady=5)

            clear_data_button = tk.Button(input_frame, text="Clear Data", command=self.clear_data)
            style_button(clear_data_button)
            clear_data_button.grid(row=6, column=4, padx=5, pady=5)

            self.tree = ttk.Treeview(
                self.transactions_tab,
                columns=("Username", "Bin", "Qty", "Weight", "Giveaway", "GiveawayNum", "Timestamp"),
                show="headings",
                height=10
            )
            for col in ("Username", "Bin", "Qty", "Weight", "Giveaway", "GiveawayNum", "Timestamp"):
                self.tree.heading(col, text=col)
                self.tree.column(col, width=120 if col != "Timestamp" else 160, anchor="w")
            self.tree.pack(fill="both", expand=True, padx=5, pady=5)

            scrollbar = ttk.Scrollbar(self.transactions_tab, orient="vertical", command=self.tree.yview)
            self.tree.configure(yscrollcommand=scrollbar.set)
            scrollbar.pack(side="right", fill="y")

            tk.Label(self.transactions_tab, text="Assigned Bins:", font=("Helvetica", 10, "bold"), bg="#FFFFFF", fg="#0288D1").pack(anchor="w", padx=5)
            self.bin_display = tk.Text(self.transactions_tab, height=4, width=60, font=("Helvetica", 10), bg="#F5F5F5", state="disabled")
            self.bin_display.pack(fill="x", padx=5, pady=5)
            self.update_bin_display()

            # Settings tab
            self.settings_tab = tk.Frame(self.notebook, bg="#FFFFFF")
            self.notebook.add(self.settings_tab, text="Settings")

            settings_frame = tk.Frame(self.settings_tab, bg="#FFFFFF")
            settings_frame.pack(pady=10)

            tk.Label(settings_frame, text="Telegram Chat ID:", bg="#FFFFFF", font=("Helvetica", 8)).pack(pady=5)
            self.chat_id_entry = tk.Entry(settings_frame, width=40, font=("Helvetica", 8), bg="#F5F5F5")
            self.chat_id_entry.insert(0, self.chat_id)
            self.chat_id_entry.pack(pady=5)

            tk.Label(settings_frame, text="Top Buyer Text:", bg="#FFFFFF", font=("Helvetica", 8)).pack(pady=5)
            self.top_buyer_entry = tk.Entry(settings_frame, width=40, font=("Helvetica", 8), bg="#F5F5F5")
            self.top_buyer_entry.insert(0, self.top_buyer_text)
            self.top_buyer_entry.pack(pady=5)

            tk.Label(settings_frame, text="Giveaway Announcement Text:", bg="#FFFFFF", font=("Helvetica", 8)).pack(pady=5)
            self.giveaway_entry = tk.Entry(settings_frame, width=40, font=("Helvetica", 8), bg="#F5F5F5")
            self.giveaway_entry.insert(0, self.giveaway_announcement_text)
            self.giveaway_entry.pack(pady=5)

            tk.Label(settings_frame, text="Flash Sale Announcement Text:", bg="#FFFFFF", font=("Helvetica", 8)).pack(pady=5)
            self.flash_sale_entry = tk.Entry(settings_frame, width=40, font=("Helvetica", 8), bg="#F5F5F5")
            self.flash_sale_entry.insert(0, self.flash_sale_announcement_text)
            self.flash_sale_entry.pack(pady=5)

            tk.Button(
                settings_frame,
                text="Save Settings",
                command=self.save_settings,
                bg="#0288D1",
                fg="#FFFFFF",
                font=("Helvetica", 8)
            ).pack(pady=10)

            subscription_frame = tk.Frame(self.settings_tab, bg="#FFFFFF")
            subscription_frame.pack(pady=10)
            self.subscription_frame = subscription_frame

            self.subscription_label = tk.Label(
                subscription_frame,
                text=f"Current Tier: {self.tier} (Max {self.max_bins} bins)",
                bg="#FFFFFF",
                font=("Helvetica", 8)
            )
            self.subscription_label.pack(pady=5)

            self.subscription_status_label = tk.Label(
                subscription_frame,
                text="Status: N/A\nNext Billing: N/A",
                bg="#FFFFFF",
                font=("Helvetica", 8)
            )
            self.subscription_status_label.pack(pady=5)

            self.tier_combobox = ttk.Combobox(
                subscription_frame,
                textvariable=self.tier_var,
                values=["Trial", "Bronze", "Silver", "Gold"],
                state="readonly",
                width=10,
                font=("Helvetica", 8)
            )
            self.tier_combobox.set(self.tier)
            self.tier_combobox.pack(pady=5)

            tk.Button(
                subscription_frame,
                text="Upgrade",
                command=self.on_upgrade,
                bg="#43A047",
                fg="#FFFFFF",
                font=("Helvetica", 8)
            ).pack(side="left", padx=5, pady=10)

            tk.Button(
                subscription_frame,
                text="Downgrade",
                command=self.on_downgrade,
                bg="#FF9800",
                fg="#FFFFFF",
                font=("Helvetica", 8)
            ).pack(side="left", padx=5, pady=10)

            tk.Button(
                subscription_frame,
                text="Cancel",
                command=self.on_cancel,
                bg="#F44336",
                fg="#FFFFFF",
                font=("Helvetica", 8)
            ).pack(side="left", padx=5, pady=10)

            # Analytics tab
            self.analytics_tab = tk.Frame(self.notebook, bg="#FFFFFF")
            self.notebook.add(self.analytics_tab, text="Analytics")

            top_buyers_header = tk.Frame(self.analytics_tab, bg="#FFFFFF")
            top_buyers_header.pack(fill="x")
            tk.Label(
                top_buyers_header,
                text="Top Buyers",
                font=("Helvetica", 10, "bold"),
                bg="#FFFFFF",
                fg="#0288D1"
            ).pack(side="left", padx=5, pady=5)
            tk.Button(
                top_buyers_header,
                text="?",
                command=lambda: self.show_field_info("top_buyers"),
                width=2,
                font=("Helvetica", 8),
                bg="#B0BEC5",
                fg="#FFFFFF"
            ).pack(side="right", padx=5)

            self.top_buyers_labels = []
            for i in range(3):
                label = tk.Label(
                    self.analytics_tab,
                    text="",
                    font=("Helvetica", 8),
                    bg="#FFFFFF",
                    wraplength=200,
                    cursor="hand2"
                )
                label.pack(anchor="w", padx=5, pady=2)
                label.bind("<Button-1>", lambda e, idx=i: self.copy_top_buyer_text(idx))
                self.top_buyers_labels.append(label)

            stats_frame = tk.Frame(self.analytics_tab, bg="#FFFFFF")
            stats_frame.pack(fill="x", padx=5, pady=5)

            self.stats_text = tk.Label(
                stats_frame,
                text="No stats yet",
                font=("Helvetica", 8),
                bg="#F5F5F5",
                anchor="w",
                justify="left"
            )
            self.stats_text.pack(fill="x", padx=5, pady=2)
            self.stats_text.bind("<Button-1>", lambda e: self.copy_stats_text())

            self.sell_rate_text = tk.Label(
                stats_frame,
                text="No sell rate yet",
                font=("Helvetica", 8),
                bg="#F5F5F5",
                anchor="w",
                justify="left"
            )
            self.sell_rate_text.pack(fill="x", padx=5, pady=2)
            self.sell_rate_text.bind("<Button-1>", lambda e: self.copy_sell_rate_text())

            # Footer
            footer = tk.Frame(root, bg="#212121")
            footer.grid(row=2, column=0, columnspan=2, sticky="ew", padx=5, pady=5)

            self.footer_label = tk.Label(
                footer,
                text=f"License ID: SS-2025-001 | SwiftSale(TM) (C) 2025 | Bins: 0/{self.max_bins}",
                font=("Helvetica", 8),
                fg="#B0BEC5",
                bg="#212121"
            )
            self.footer_label.pack(side="left", padx=10)

            self.update_footer()
            self.update_top_buyers()
            self.update_stats()
            self.show_avg_sell_rate(show_message=False)
            self.update_subscription_ui()
            logging.info("UI updates initialized successfully.")

            self.root.grid_columnconfigure(0, weight=1)
            self.root.grid_rowconfigure(1, weight=1)
            self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

            logging.info("Application initialized")
        except Exception as e:
            logging.error(f"UI initialization failed: {e}", exc_info=True)
            messagebox.showerror("Initialization Error", f"UI initialization failed: {e}")
            self.root.destroy()

    def init_db(self):
        """Initialize SQLite database."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    email TEXT PRIMARY KEY,
                    subscription_id TEXT,
                    tier TEXT,
                    license_key TEXT,
                    last_verified TIMESTAMP
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    user_email TEXT PRIMARY KEY,
                    chat_id TEXT,
                    top_buyer_text TEXT,
                    giveaway_announcement_text TEXT,
                    flash_sale_announcement_text TEXT
                )
            """)
            conn.commit()
        except sqlite3.Error as e:
            logging.error(f"Failed to initialize database: {e}", exc_info=True)
            messagebox.showerror("Database Error", f"Failed to initialize database: {e}")
        finally:
            if 'conn' in locals():
                conn.close()

    def prompt_login(self):
        """Simulate login prompt for user email."""
        login_window = tk.Toplevel(self.root)
        login_window.title("Login")
        login_window.geometry("300x150")
        tk.Label(login_window, text="Enter your email:").pack(pady=10)
        email_entry = tk.Entry(login_window, width=30)
        email_entry.pack(pady=5)
        email = [None]
        def submit():
            email[0] = email_entry.get().strip()
            if email[0]:
                login_window.destroy()
        tk.Button(login_window, text="Submit", command=submit).pack(pady=10)
        login_window.grab_set()
        self.root.wait_window(login_window)
        return email[0]

    def load_subscription(self):
        """Load subscription from database and verify with Stripe."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT tier, license_key FROM users WHERE email = ?", (self.user_email,))
            result = cursor.fetchone()
            if result:
                self.tier, self.license_key = result
            else:
                self.tier, self.license_key = "Trial", ""
            if self.tier not in self.tier_limits:
                self.tier = "Trial"
            self.max_bins = self.tier_limits[self.tier]['bins']
            self.verify_subscription_with_stripe()
            self.update_header_and_footer()
            logging.info(f"Subscription loaded: {self.tier} (Max {self.max_bins} bins)")
        except sqlite3.Error as e:
            logging.error(f"Failed to load subscription: {e}", exc_info=True)
            self.tier = "Trial"
            self.max_bins = self.tier_limits['Trial']['bins']
            self.update_header_and_footer()
        finally:
            if 'conn' in locals():
                conn.close()

    def verify_subscription_with_stripe(self):
        """Verify subscription status with Stripe."""
        if self.tier == "Trial" or not self.license_key:
            return
        try:
            subscription = stripe.Subscription.retrieve(self.license_key)
            if subscription.status != "active":
                logging.warning(f"Subscription {self.license_key} is not active. Reverting to Trial.")
                self.tier = "Trial"
                self.max_bins = self.tier_limits["Trial"]["bins"]
                self.license_key = ""
                self.update_subscription(self.tier, self.license_key)
            else:
                price_id = subscription["items"]["data"][0]["price"]["id"]
                new_tier = REVERSE_PRICE_MAP.get(price_id, "Trial")

                if new_tier != self.tier:
                    logging.info(f"Updating tier from {self.tier} to {new_tier}")
                    self.update_subscription(new_tier, self.license_key)
        except stripe.error.StripeError as e:
            logging.error(f"Failed to verify subscription: {e}", exc_info=True)
            self.tier = "Trial"
            self.max_bins = self.tier_limits["Trial"]["bins"]
            self.update_subscription(self.tier, "")

    def update_subscription(self, tier, license_key):
        """Update subscription in database."""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            if tier == "Trial":
                cursor.execute(
                    "DELETE FROM users WHERE email = ?",
                    (self.user_email,)
                )
            else:
                cursor.execute(
                    "INSERT OR REPLACE INTO users (email, tier, license_key, last_verified) VALUES (?, ?, ?, ?)",
                    (self.user_email, tier, license_key, datetime.datetime.now().isoformat())
                )
            conn.commit()
            self.tier = tier
            self.max_bins = self.tier_limits[tier]['bins']
            self.license_key = license_key
            def update_ui():
                if hasattr(self, 'subscription_label'):
                    self.subscription_label.config(text=f"Current Tier: {self.tier} (Max {self.max_bins} bins)")
                self.update_subscription_ui()
                self.update_header_and_footer()
            self.root.after(0, update_ui)
            logging.info(f"Subscription updated: Tier={tier}, License Key={license_key}")
        except sqlite3.Error as e:
            logging.error(f"Failed to update subscription: {e}", exc_info=True)
            self.root.after(0, lambda: messagebox.showerror("Error", f"Failed to update subscription: {e}"))
        finally:
            if 'conn' in locals():
                conn.close()

    def update_subscription_ui(self):
        """Update subscription status UI."""
        def update():
            try:
                status = "N/A"
                next_billing = "N/A"
                if self.tier != "Trial" and self.license_key:
                    subscription = stripe.Subscription.retrieve(self.license_key)
                    status = subscription["status"].capitalize()
                    next_billing = datetime.datetime.fromtimestamp(
                        subscription["current_period_end"]
                    ).strftime("%Y-%m-%d")
                status_text = f"Status: {status}\nNext Billing: {next_billing}"
                if hasattr(self, 'subscription_status_label'):
                    self.subscription_status_label.config(text=status_text)
            except stripe.error.StripeError as e:
                logging.error(f"Failed to fetch subscription status: {e}", exc_info=True)
                if hasattr(self, 'subscription_status_label'):
                    self.subscription_status_label.config(text="Status: Error\nNext Billing: N/A")
        self.root.after(0, update)

    def update_header_and_footer(self):
        """Update header and footer with subscription details."""
        def update():
            header_text = f"SwiftSale(TM) - {self.tier} Subscription (Max {self.max_bins} Bins)"
            self.root.title(header_text)
            assigned_bins = len(self.bidders)
            footer_text = f"License ID: SS-2025-001 | SwiftSale(TM) (C) 2025 | Bins: {assigned_bins}/{self.max_bins}"
            if hasattr(self, 'footer_label'):
                self.footer_label.config(text=footer_text)
            if hasattr(self, 'show_label'):
                self.show_label.config(text=f"Show ID: {self.show_id} | Tier: {self.tier} (Max {self.max_bins} bins)")
        self.root.after(0, update)

    def on_closing(self):
        """Handle application closure."""
        logging.info("Closing application")
        if self.loop is not None:
            try:
                self.loop.call_soon_threadsafe(self.loop.stop)
                tasks = asyncio.all_tasks(self.loop)
                for task in tasks:
                    task.cancel()
                self.loop.run_until_complete(self.loop.shutdown_asyncgens())
                self.loop.close()
            except Exception as e:
                logging.error(f"Failed to close asyncio loop: {e}", exc_info=True)
        try:
            requests.get(f"http://localhost:{os.getenv('PORT', 5000)}/shutdown", timeout=1)
        except requests.RequestException:
            logging.warning("Failed to shut down Flask server")
        self.root.destroy()

    def paste_to_entry(self, entry):
        """Paste clipboard content into entry field."""
        try:
            clipboard = self.root.clipboard_get()
            cleaned_clipboard = clipboard.strip().strip('()')
            current_content = entry.get().strip()
            if cleaned_clipboard == current_content:
                logging.info(f"Skipped pasting: Clipboard matches current content")
                return
            entry.delete(0, tk.END)
            entry.insert(0, cleaned_clipboard)
            logging.info(f"Pasted clipboard content: {cleaned_clipboard}")
        except tk.TclError:
            logging.warning("Paste failed: Clipboard empty or inaccessible")

    def update_footer(self):
        """Update footer with bin count."""
        self.update_header_and_footer()

    def save_settings(self):
        """Save settings to database."""
        try:
            self.chat_id = self.chat_id_entry.get().strip()
            self.top_buyer_text = self.top_buyer_entry.get().strip()
            self.giveaway_announcement_text = self.giveaway_entry.get().strip()
            self.flash_sale_announcement_text = self.flash_sale_entry.get().strip()

            if not self.chat_id:
                messagebox.showerror("Error", "Chat ID required!")
                logging.warning("Save settings: Empty chat ID")
                return
            if not (self.chat_id.startswith('@') or self.chat_id.isdigit()):
                messagebox.showerror("Error", "Invalid Telegram Chat ID! Must start with '@' or be a numeric ID.")
                logging.warning("Save settings: Invalid chat ID format")
                return

            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO settings
                (user_email, chat_id, top_buyer_text, giveaway_announcement_text, flash_sale_announcement_text)
                VALUES (?, ?, ?, ?, ?)
                """,
                (self.user_email, self.chat_id, self.top_buyer_text, self.giveaway_announcement_text,
                 self.flash_sale_announcement_text)
            )
            conn.commit()
            conn.close()

            self.update_footer()
            self.update_top_buyers()
            self.update_stats()
            self.update_bin_display()
            self.show_avg_sell_rate(show_message=False)

            messagebox.showinfo("Success", "Settings saved!")
            logging.info("Settings saved successfully.")
        except sqlite3.Error as e:
            logging.error(f"Failed to save settings: {e}", exc_info=True)
            messagebox.showerror("Settings Error", f"Failed to save settings: {e}")

    def update_bin_display(self):
        """Update bin displays with latest assignments in both Transactions and Bidders tabs."""
        def update():
            bin_text = "No bins assigned yet."
            if self.bidders:
                bin_text = "\n".join(
                    f"Bin {data['bin']}: {data.get('original_username', username)}"
                    for username, data in sorted(self.bidders.items(), key=lambda x: x[1]['bin'])
                )
            
            if hasattr(self, 'bin_display'):
                self.bin_display.config(state="normal")
                self.bin_display.delete(1.0, tk.END)
                self.bin_display.insert(tk.END, bin_text)
                self.bin_display.config(state="disabled")

            if hasattr(self, 'bidders_bin_display'):
                self.bidders_bin_display.config(state="normal")
                self.bidders_bin_display.delete(1.0, tk.END)
                self.bidders_bin_display.insert(tk.END, bin_text)
                self.bidders_bin_display.config(state="disabled")

            if hasattr(self, 'bidders_bin_display_large'):
                self.bidders_bin_display_large.config(state="normal")
                self.bidders_bin_display_large.delete(1.0, tk.END)
                self.bidders_bin_display_large.insert(tk.END, bin_text)
                self.bidders_bin_display_large.config(state="disabled")

            logging.info("Updated bin displays")
        self.root.after(0, update)

    def copy_sell_rate_text(self):
        """Copy sell rate text to clipboard."""
        text = self.sell_rate_text.cget("text").strip()
        pyperclip.copy(text)
        messagebox.showinfo("Copied", f"Copied: {text}")
        logging.info(f"Copied sell rate text: {text}")

    def show_avg_sell_rate(self, show_message=True):
        """Calculate and display average sell rate."""
        try:
            if not self.bidders:
                sell_rate_text = "No transactions to analyze."
            else:
                timestamps = [
                    datetime.datetime.strptime(t['timestamp'], "%Y-%m-%d %I:%M:%S %p")
                    for data in self.bidders.values() for t in data['transactions']
                ]
                if len(timestamps) < 2:
                    sell_rate_text = "Need at least two transactions."
                else:
                    timestamps.sort()
                    avg_seconds = sum(
                        (timestamps[i + 1] - timestamps[i]).total_seconds()
                        for i in range(len(timestamps) - 1)
                    ) / (len(timestamps) - 1)
                    minutes, seconds = divmod(int(avg_seconds), 60)
                    sell_rate_text = f"Avg Sell Rate: {minutes} min {seconds} sec"

            def update():
                if hasattr(self, 'sell_rate_text'):
                    self.sell_rate_text.config(text=sell_rate_text)
                if show_message:
                    messagebox.showinfo("Average Sell Rate", sell_rate_text)
            self.root.after(0, update)
            logging.info(f"Avg sell rate: {sell_rate_text}")
        except Exception as e:
            logging.error(f"Failed to calculate sell rate: {e}", exc_info=True)
            self.root.after(0, lambda: messagebox.showerror("Error", f"Failed to calculate sell rate: {e}"))

    def copy_top_buyer_text(self, index):
        """Copy top buyer shoutout to clipboard."""
        try:
            if index < len(self.top_buyers):
                username, count = self.top_buyers[index]
                text = self.top_buyer_text.format(username=username, count=count)
                pyperclip.copy(text)
                messagebox.showinfo("Copied", f"Copied: {text}")
                logging.info(f"Copied top buyer text: {text}")
        except Exception as e:
            logging.error(f"Failed to copy top buyer text: {e}", exc_info=True)
            messagebox.showerror("Error", f"Failed to copy text: {e}")

    def update_top_buyers(self):
        """Update top buyers display."""
        try:
            self.top_buyers = sorted(
                [(data.get('original_username', username), data['total_items'])
                 for username, data in self.bidders.items()],
                key=lambda x: x[1], reverse=True
            )[:3]
            def update():
                for i, label in enumerate(self.top_buyers_labels):
                    if i < len(self.top_buyers):
                        username, count = self.top_buyers[i]
                        text = self.top_buyer_text.format(username=username, count=count)
                        label.config(text=f"{username}: {count} items\n{text}")
                    else:
                        label.config(text="")
            self.root.after(0, update)
            logging.info("Updated top buyers display")
        except Exception as e:
            logging.error(f"Failed to update top buyers: {e}", exc_info=True)

    def copy_stats_text(self):
        """Copy stats text to clipboard."""
        text = self.stats_text.cget("text").strip()
        pyperclip.copy(text)
        messagebox.showinfo("Copied", f"Copied: {text}")
        logging.info(f"Copied stats text: {text}")

    def update_stats(self):
        """Update stats display."""
        try:
            total_items = sum(data['total_items'] for data in self.bidders.values())
            unique_buyers = len(self.bidders)
            avg_items = round(total_items / unique_buyers, 1) if unique_buyers > 0 else 0
            stats_text = f"Sold {total_items} items to {unique_buyers} buyers, avg {avg_items} items each"
            def update():
                if hasattr(self, 'stats_text'):
                    self.stats_text.config(text=stats_text)
            self.root.after(0, update)
            logging.info("Updated stats display")
        except Exception as e:
            logging.error(f"Failed to update stats: {e}", exc_info=True)

    def copy_giveaway_announcement(self):
        """Copy giveaway announcement to clipboard."""
        try:
            text = self.giveaway_announcement_text.format(number=self.giveaway_count + 1)
            pyperclip.copy(text)
            def update():
                if hasattr(self, 'announcement_text'):
                    self.announcement_text.config(state="normal")
                    self.announcement_text.delete("1.0", tk.END)
                    self.announcement_text.insert(tk.END, text)
                    self.announcement_text.config(state="disabled")
                messagebox.showinfo("Copied", f"Copied: {text}")
            self.root.after(0, update)
            logging.info(f"Copied giveaway announcement: {text}")
        except Exception as e:
            logging.error(f"Failed to copy giveaway announcement: {e}", exc_info=True)
            self.root.after(0, lambda: messagebox.showerror("Error", f"Failed to copy announcement: {e}"))

    def copy_flash_sale_announcement(self):
        """Copy flash sale announcement to clipboard."""
        try:
            text = self.flash_sale_announcement_text
            pyperclip.copy(text)
            def update():
                if hasattr(self, 'announcement_text'):
                    self.announcement_text.config(state="normal")
                    self.announcement_text.delete("1.0", tk.END)
                    self.announcement_text.insert(tk.End, text)
                    self.announcement_text.config(state="disabled")
                messagebox.showinfo("Copied", f"Copied: {text}")
            self.root.after(0, update)
            logging.info(f"Copied flash sale announcement: {text}")
        except Exception as e:
            logging.error(f"Failed to copy flash sale announcement: {e}", exc_info=True)
            self.root.after(0, lambda: messagebox.showerror("Error", f"Failed to copy announcement: {e}"))

    def add_transaction(self, username, original_username, qty, weight, is_giveaway):
        """Add or update a transaction for a bidder."""
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
        if username in self.bidders:
            data = self.bidders[username]
            bin_num = data['bin']
            giveaway_num = self.next_giveaway_num if is_giveaway else 0
            txn = {
                'bin': bin_num,
                'qty': qty,
                'weight': weight,
                'giveaway': is_giveaway,
                'giveaway_num': giveaway_num,
                'timestamp': timestamp
            }
            data['transactions'].append(txn)
            data['total_items'] += qty
            if is_giveaway:
                self.next_giveaway_num += 1
                self.giveaway_count += 1
        else:
            if self.next_bin > self.max_bins:
                raise ValueError(f"Bin limit reached for {self.tier} tier ({self.max_bins} bins).")
            bin_num = self.next_bin
            giveaway_num = self.next_giveaway_num if is_giveaway else 0
            txn = {
                'bin': bin_num,
                'qty': qty,
                'weight': weight,
                'giveaway': is_giveaway,
                'giveaway_num': giveaway_num,
                'timestamp': timestamp
            }
            self.bidders[username] = {
                'bin': bin_num,
                'transactions': [txn],
                'total_items': qty,
                'original_username': original_username
            }
            self.next_bin += 1
            if is_giveaway:
                self.next_giveaway_num += 1
                self.giveaway_count += 1
        return bin_num, giveaway_num if is_giveaway else None

    def add_bidder(self):
        """Handle bidder addition with UI updates."""
        try:
            if not hasattr(self, 'bidders'):
                self.bidders = {}
                logging.info("Initialized self.bidders")
            if not hasattr(self, 'sort_order'):
                self.sort_order = 'desc'
                logging.info("Initialized self.sort_order to 'desc'")
            if not hasattr(self, 'tree'):
                logging.error("Treeview widget not initialized")
                messagebox.showerror("Error", "Treeview not initialized. Please restart the application.")
                return

            username = self.username_entry.get().strip().lower()
            original_username = self.username_entry.get().strip()
            qty_str = self.qty_entry.get().strip()
            weight = self.weight_entry.get() or ""
            is_giveaway = self.giveaway_var.get()

            if not username:
                messagebox.showerror("Error", "Username required!")
                self.current_bidder_label.config(text="Error: Username required")
                logging.warning("Add bidder: Username required")
                return

            qty = int(qty_str) if qty_str else 1
            if qty <= 0:
                raise ValueError("Quantity must be positive")
            bin_num, giveaway_num = self.add_transaction(username, original_username, qty, weight, is_giveaway)
            self.last_bidder = username
            self.current_bidder_label.config(text=f"Username: {original_username} | Bin: {bin_num}")
            self.latest_bin_assignment = f"Username: {original_username} | Bin: {bin_num}"
            self.socketio.emit('update', {'data': self.latest_bin_assignment})
            if hasattr(self, 'bot') and self.chat_id:
                asyncio.run_coroutine_threadsafe(
                    self.send_bin_number(original_username, bin_num),
                    self.loop
                )
            self.username_entry.delete(0, tk.END)
            self.qty_entry.delete(0, tk.END)
            self.qty_entry.insert(0, "1")
            self.weight_entry.set("")
            self.giveaway_var.set(False)
            self.update_treeview()
            self.update_bin_display()
            self.update_top_buyers()
            self.update_stats()
            self.show_avg_sell_rate(show_message=False)
            self.update_footer()
            logging.info(f"Added/updated bidder: {original_username}, Bin: {bin_num}")
        except ValueError as e:
            messagebox.showerror("Error", str(e))
            self.current_bidder_label.config(text=f"Error: {e}")
            logging.warning(f"Add bidder failed: {e}")
        except Exception as e:
            logging.error(f"Unexpected error in add_bidder: {e}", exc_info=True)
            messagebox.showerror("Error", f"An unexpected error occurred: {e}")

    def search_bidders(self):
        """Search for bidders by username or display name."""
        try:
            q = self.search_entry.get().strip().lower()
            if not q:
                self.search_result.config(text="Enter a search term!")
                logging.warning("Search bidders: Empty query")
                return
            res = []
            for username, data in self.bidders.items():
                display_username = data.get('original_username', username)
                if q in username.lower() or q in display_username.lower():
                    res.append(f"{display_username} → Bin {data['bin']}")
            self.search_result.config(text='\n'.join(res) if res else "No bidders found!")
            logging.info(f"Search bidders: {'Found matches' if res else 'No matches'}")
        except Exception as e:
            logging.error(f"Failed to search bidders: {e}", exc_info=True)
            messagebox.showerror("Error", f"Search failed: {e}")

    def set_sort_order(self, order):
        """Set sort order for treeview."""
        try:
            self.sort_order = order
            self.update_treeview()
            logging.info(f"Sort order set to {order}")
        except Exception as e:
            logging.error(f"Failed to set sort order: {e}", exc_info=True)
            messagebox.showerror("Error", f"Failed to set sort order: {e}")

    def update_treeview(self):
        """Update treeview with sorted transactions."""
        try:
            if not hasattr(self, 'tree'):
                logging.error("Treeview widget not initialized")
                return
            if not hasattr(self, 'bidders') or not self.bidders:
                logging.info("No bidders to display in Treeview")
                self.tree.delete(*self.tree.get_children())
                return
            if not hasattr(self, 'sort_order'):
                self.sort_order = 'desc'
                logging.info("Initialized self.sort_order to 'desc'")

            self.tree.delete(*self.tree.get_children())
            transactions = [
                {'username': username, 'display_username': data.get('original_username', username), 'bin': data['bin'], 'txn': t}
                for username, data in self.bidders.items() for t in data['transactions']
            ]
            if not transactions:
                logging.info("No transactions to display in Treeview")
                return
            sorted_transactions = sorted(
                transactions,
                key=lambda x: datetime.datetime.strptime(x['txn']['timestamp'], "%Y-%m-%d %I:%M:%S %p"),
                reverse=(self.sort_order == 'desc')
            )
            user_parents = {}
            for t in sorted_transactions:
                username = t['username']
                data = t['txn']
                display_username = t['display_username']
                if username not in user_parents:
                    parent = self.tree.insert('', 'end', text='+', open=False, values=(
                        display_username, t['bin'], data['qty'], data['weight'],
                        'Yes' if data['giveaway'] else 'No', data['giveaway_num'], data['timestamp']
                    ))
                    user_parents[username] = parent
                else:
                    self.tree.insert(user_parents[username], 'end', values=(
                        '', t['bin'], data['qty'], data['weight'],
                        'Yes' if data['giveaway'] else 'No', data['giveaway_num'], data['timestamp']
                    ))
            def on_click(e):
                region = self.tree.identify_region(e.x, e.y)
                if region == 'tree':
                    iid = self.tree.identify_row(e.y)
                    if iid:
                        self.tree.item(iid, open=not self.tree.item(iid, 'open'))
            self.tree.bind('<ButtonRelease-1>', on_click)
            logging.info(f"Treeview updated with sort order: {self.sort_order}")
        except Exception as e:
            logging.error(f"Treeview update failed: {e}", exc_info=True)
            messagebox.showerror("Error", f"Failed to update treeview: {e}")

    def print_bidders(self):
        """Export bidder data to CSV."""
        try:
            if not self.bidders:
                messagebox.showinfo("Info", "No bidders to export.")
                logging.info("Export bidders: No bidders")
                return
            csv_columns = ["username", "bin", "qty", "weight", "giveaway", "giveaway_num", "timestamp"]
            os.makedirs(os.path.dirname(self.bidder_csv_path), exist_ok=True)
            file_exists = os.path.exists(self.bidder_csv_path) and os.path.getsize(self.bidder_csv_path) > 0
            with open(self.bidder_csv_path, mode="a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=csv_columns)
                if not file_exists:
                    writer.writeheader()
                for username, data in self.bidders.items():
                    display_username = data.get('original_username', username)
                    for t in data["transactions"]:
                        writer.writerow({
                            "username": display_username,
                            "bin": data["bin"],
                            "qty": t["qty"],
                            "weight": t["weight"],
                            "giveaway": "Yes" if t["giveaway"] else "No",
                            "giveaway_num": t["giveaway_num"] if t["giveaway"] else "",
                            "timestamp": t["timestamp"]
                        })
            messagebox.showinfo("Success", f"Data appended to {self.bidder_csv_path}")
            logging.info(f"Bidders exported to {self.bidder_csv_path}")
        except (OSError, PermissionError) as e:
            logging.error(f"Bidders export failed: {e}", exc_info=True)
            messagebox.showerror("Error", f"Export failed: {e}")
        except Exception as e:
            logging.error(f"Unexpected error in print_bidders: {e}", exc_info=True)
            messagebox.showerror("Error", f"Export failed: {e}")

    def import_csv(self):
        """Import bidder data from CSV."""
        file_path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv")])
        if not file_path:
            logging.info("Import CSV: No file selected")
            return
        try:
            self.bidders.clear()
            self.next_bin = 1
            self.next_giveaway_num = 1
            self.giveaway_count = 0
            self.last_bidder = None
            with open(file_path, mode="r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                expected_columns = ["username", "bin", "qty", "weight", "giveaway", "giveaway_num", "timestamp"]
                if not all(col in reader.fieldnames for col in expected_columns):
                    raise ValueError("Invalid CSV format. Expected columns: " + ", ".join(expected_columns))
                for row in reader:
                    username = row["username"].strip().lower()
                    original_username = row["username"].strip()
                    try:
                        bin_num = int(row["bin"])
                        qty = int(row["qty"])
                        giveaway_num = int(row["giveaway_num"]) if row["giveaway_num"] else 0
                        is_giveaway = row["giveaway"].lower() in ("yes", "true", "1")
                        datetime.datetime.strptime(row["timestamp"], "%Y-%m-%d %I:%M:%S %p")
                    except (ValueError, TypeError) as e:
                        raise ValueError(f"Invalid data in CSV row: {row}")
                    if bin_num >= self.next_bin:
                        self.next_bin = bin_num + 1
                    if is_giveaway and giveaway_num >= self.next_giveaway_num:
                        self.next_giveaway_num = giveaway_num + 1
                        self.giveaway_count += 1
                    txn = {
                        'bin': bin_num,
                        'qty': qty,
                        'weight': row["weight"],
                        'giveaway': is_giveaway,
                        'giveaway_num': giveaway_num,
                        'timestamp': row["timestamp"]
                    }
                    if username in self.bidders:
                        self.bidders[username]['transactions'].append(txn)
                        self.bidders[username]['total_items'] += qty
                    else:
                        if bin_num > self.max_bins:
                            raise ValueError(f"Bin {bin_num} exceeds limit for {self.tier} tier ({self.max_bins} bins).")
                        self.bidders[username] = {
                            'bin': bin_num,
                            'transactions': [txn],
                            'total_items': qty,
                            'original_username': original_username
                        }
            self.update_treeview()
            self.update_bin_display()
            self.update_top_buyers()
            self.update_stats()
            self.show_avg_sell_rate(show_message=False)
            self.update_footer()
            messagebox.showinfo("Success", f"Imported {len(self.bidders)} bidders from {file_path}")
            logging.info(f"Imported {len(self.bidders)} bidders from {file_path}")
        except ValueError as e:
            logging.error(f"Import CSV failed: {e}")
            messagebox.showerror("Error", f"Import failed: {e}")
            self.bidders.clear()
            self.next_bin = 1
            self.next_giveaway_num = 1
            self.giveaway_count = 0
            self.update_treeview()
            self.update_bin_display()
            self.update_top_buyers()
            self.update_stats()
            self.show_avg_sell_rate(show_message=False)
            self.update_footer()
        except Exception as e:
            logging.error(f"Unexpected error in import_csv: {e}", exc_info=True)
            messagebox.showerror("Error", f"Import failed: {e}")

    def clear_data(self):
        """Clear all bidder data and start a new show."""
        try:
            if messagebox.askyesno("Confirm", "Start new show?"):
                self.save_auction_history()
                self.bidders.clear()
                self.last_bidder = None
                self.next_bin = 1
                self.next_giveaway_num = 1
                self.giveaway_count = 0
                self.tree.delete(*self.tree.get_children())
                self.search_result.config(text="")
                self.current_bidder_label.config(text="")
                self.latest_bin_assignment = "Waiting for bidder..."
                self.socketio.emit('update', {'data': self.latest_bin_assignment})
                self.show_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                self.bidder_csv_path = os.path.join(log_dir, f"bidder_history_{self.show_id}.csv")
                self.update_bin_display()
                self.update_top_buyers()
                self.update_stats()
                self.show_avg_sell_rate(show_message=False)
                self.announcement_text.config(state="normal")
                self.announcement_text.delete("1.0", tk.END)
                self.announcement_text.config(state="disabled")
                if hasattr(self, 'bot') and self.chat_id:
                    asyncio.run_coroutine_threadsafe(
                        self.bot.send_message(self.chat_id, f"New show: {self.show_id}"),
                        self.loop
                    )
                messagebox.showinfo("Info", f"New show: {self.show_id}")
                self.update_footer()
                logging.info(f"Started new show: {self.show_id}")
        except Exception as e:
            logging.error(f"Failed to clear data: {e}", exc_info=True)
            messagebox.showerror("Error", f"Failed to start new show: {e}")

    def save_auction_history(self):
        """Save auction history to CSV."""
        if not self.bidders:
            logging.info("Save auction history: No bidders")
            return
        csv_path = os.path.join(log_dir, f"auction_history_{self.show_id}.csv")
        try:
            os.makedirs(os.path.dirname(csv_path), exist_ok=True)
            with open(csv_path, mode="w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["show_id", "start_time", "end_time", "total_bidders", "total_transactions"])
                writer.writeheader()
                writer.writerow({
                    "show_id": self.show_id,
                    "start_time": self.show_start_time,
                    "end_time": datetime.datetime.now().strftime("%Y-%m-%d %I:%M:%S %p"),
                    "total_bidders": len(self.bidders),
                    "total_transactions": sum(len(data["transactions"]) for data in self.bidders.values())
                })
            logging.info(f"Auction history saved to {csv_path}")
        except Exception as e:
            logging.error(f"Auction history save failed: {e}", exc_info=True)

    def on_upgrade(self):
        """Initiate Stripe checkout for upgrade."""
        tier = self.tier_var.get()
        if tier == self.tier:
            messagebox.showinfo("Info", f"You’re already on {tier}.")
            logging.info(f"Upgrade attempt: Already on tier {tier}")
            return
        try:
            headers = {'Authorization': f'Bearer {self.api_token}'}
            resp = requests.post(
                f"http://localhost:{os.getenv('PORT', 5000)}/create-checkout-session",
                json={"tier": tier},
                headers=headers,
                timeout=5
            )
            resp.raise_for_status()
            webbrowser.open(resp.json()['url'])
            logging.info(f"Opened Stripe checkout for tier: {tier}")
        except requests.RequestException as e:
            logging.error(f"Checkout request failed: {e}", exc_info=True)
            messagebox.showerror("Checkout Error", f"Failed to initiate checkout: {e}")
        except Exception as e:
            logging.error(f"Unexpected error in on_upgrade: {e}", exc_info=True)
            messagebox.showerror("Error", f"An unexpected error occurred: {e}")

    def on_downgrade(self):
        """Downgrade to a lower tier."""
        current_tier = self.tier
        new_tier = self.tier_var.get()
        if new_tier == current_tier:
            messagebox.showinfo("Info", f"You’re already on {new_tier}.")
            logging.info(f"Downgrade attempt: Already on tier {new_tier}")
            return
        if new_tier == "Trial":
            self.on_cancel()
            return
        try:
            subscription = stripe.Subscription.retrieve(self.license_key)
           price_id = PRICE_MAP.get(new_tier)

            if not price_id:
                raise ValueError("Invalid tier")
            stripe.Subscription.modify(
                self.license_key,
                items=[{"id": subscription["items"]["data"][0]["id"], "price": price_id}]
            )
            self.update_subscription(new_tier, self.license_key)
            messagebox.showinfo("Success", f"Downgraded to {new_tier}.")
            logging.info(f"Downgraded to {new_tier}")
        except stripe.error.StripeError as e:
            logging.error(f"Downgrade failed: {e}", exc_info=True)
            messagebox.showerror("Error", f"Downgrade failed: {e}")
        except Exception as e:
            logging.error(f"Unexpected error in on_downgrade: {e}", exc_info=True)
            messagebox.showerror("Error", f"An unexpected error occurred: {e}")

    def on_cancel(self):
        """Cancel subscription."""
        if self.tier == "Trial":
            messagebox.showinfo("Info", "No active subscription to cancel.")
            logging.info("Cancel attempt: No active subscription")
            return
        try:
            stripe.Subscription.delete(self.license_key)
            self.update_subscription("Trial", "")
            messagebox.showinfo("Success", "Subscription canceled. Reverted to Trial.")
            logging.info("Subscription canceled")
        except stripe.error.StripeError as e:
            logging.error(f"Cancellation failed: {e}", exc_info=True)
            messagebox.showerror("Error", f"Cancellation failed: {e}")
        except Exception as e:
            logging.error(f"Unexpected error in on_cancel: {e}", exc_info=True)
            messagebox.showerror("Error", f"An unexpected error occurred: {e}")

    def show_field_info(self, field):
        """Show help information for a field."""
        messagebox.showinfo("Help", self.field_info.get(field, "No information available."))

    async def send_bin_number(self, username, bin_number):
        """Send bin number to Telegram."""
        if not hasattr(self, 'bot') or not self.chat_id:
            logging.warning("Send bin number: Bot or chat ID missing")
            return
        try:
            await self.bot.send_message(chat_id=self.chat_id, text=f"Username: {username} | Bin: {bin_number}")
            logging.info(f"Sent Telegram message: Username: {username} | Bin: {bin_number}")
        except Exception as e:
            logging.error(f"Telegram send failed: {e}", exc_info=True)
            if not self.telegram_error_shown:
                self.root.after(
                    0,
                    lambda: messagebox.showwarning("Telegram Warning", f"Failed to send message: {e}")
                )
                self.telegram_error_shown = True

if __name__ == '__main__':
    root = tk.Tk()
    app = SCDWhatnotGUI(root)
    root.mainloop()