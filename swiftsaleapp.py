import tkinter as tk
from tkinter import messagebox, ttk, filedialog
from PIL import Image, ImageTk
import os
import sys
import datetime
import csv
from telegram import Bot
import asyncio
import configparser
import warnings
import logging
import pyperclip
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO
import stripe
import threading
import webbrowser
import requests
from dotenv import load_dotenv

# Suppress urllib3 warning from python-telegram-bot
warnings.filterwarnings("ignore", category=UserWarning, module="telegram")

# Setup logging
log_dir = os.path.join(os.path.expanduser("~"), "Documents", "SwiftSale")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "swiftsale.log")
logging.basicConfig(
    filename=log_file,
    level=logging.DEBUG,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger()
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.DEBUG)
console_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
logger.addHandler(console_handler)

# Resource path helper for PyInstaller
def get_resource_path(relative_path):
    """Get absolute path to resource, works for dev and PyInstaller."""
    if hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(__file__)
    return os.path.join(base_path, relative_path)

# Load environment variables
load_dotenv()

# Configure Stripe
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
PRICE_MAP = {
    'price_1RMISXJ7WrcpTNl6dFGmS7v2': 'Bronze',
    'price_1RMIT8J7WrcpTNl6JFldsRLo': 'Silver',
    'price_1RMIXyJ7WrcpTNl6qjTjkG3o': 'Gold',
    'price_1RMIj1J7WrcpTNl6q2jxLwMI': 'Trial'
}

# Asyncio loop for Telegram
loop = None
loop_thread = None

def run_asyncio_loop():
    """Run an asyncio event loop in a separate thread."""
    global loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_forever()

loop_thread = threading.Thread(target=run_asyncio_loop, daemon=True)
loop_thread.start()

class SCDWhatnotGUI:
    def __init__(self, root):
        """Initialize the SwiftSale GUI for Whatnot auctions with a tabbed layout."""
        self.root = root
        self.root.title("SwiftSale(TM) - Powering Whatnot Auctions with Precision")
        self.root.geometry("1000x700")
        self.root.configure(bg="#2E2E2E")  # Dark gray background

        # Initialize Telegram bot
        try:
            bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
            if bot_token:
                self.bot = Bot(token=bot_token)
                self.telegram_error_shown = False
            else:
                logging.warning("Telegram bot token not found in .env")
                self.telegram_error_shown = True
        except Exception as e:
            logging.error(f"Error initializing Telegram bot: {e}", exc_info=True)
            self.telegram_error_shown = True

        # Data initialization
        self.bidders = {}
        self.next_bin = 1
        self.next_giveaway_num = 1
        self.giveaway_count = 0
        self.last_bidder = None
        self.show_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.show_start_time = datetime.datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
        self.bidder_csv_path = os.path.join(log_dir, f"bidder_history_{self.show_id}.csv")
        self.sort_order = 'desc'  # Default to descending (newest first)

        # Tier system configuration
        self.tier_limits = {
            "Trial": {"bins": 150},
            "Bronze": {"bins": 50},
            "Silver": {"bins": 150},
            "Gold": {"bins": 300}
        }
        self.valid_tiers = list(self.tier_limits.keys())

        # Load configuration
        self.config = configparser.ConfigParser()
        self.config_path = get_resource_path("config.ini")
        if not os.path.exists(self.config_path):
            self.config['DEFAULT'] = {}
            self.config['Telegram'] = {'bot_token': '', 'chat_id': ''}
            self.config['Subscription'] = {'tier': 'Trial', 'license_key': ''}
            self.config['GUI'] = {
                'top_buyer_text': "Great job, {username}! You've snagged {count} items!",
                'giveaway_announcement_text': 'Giveaway #{number} Alert! Must be following us & share the stream to enter! Winner announced in a few minutes!',
                'flash_sale_announcement_text': 'Flash Sale Alert! Grab these deals before the timer runs out!'
            }
            self.write_config()
        self.config.read(self.config_path)

        # Ensure sections exist in config
        for section in ['Telegram', 'Subscription', 'GUI']:
            if section not in self.config:
                self.config[section] = {}
            if section == 'Telegram':
                self.config[section].setdefault('bot_token', '')
                self.config[section].setdefault('chat_id', '')
            elif section == 'Subscription':
                self.config[section].setdefault('tier', 'Trial')
                self.config[section].setdefault('license_key', '')
            elif section == 'GUI':
                self.config[section].setdefault(
                    'top_buyer_text', "Great job, {username}! You've snagged {count} items!"
                )
                self.config[section].setdefault(
                    'giveaway_announcement_text',
                    'Giveaway #{number} Alert! Must be following us & share the stream to enter! Winner announced in a few minutes!'
                )
                self.config[section].setdefault(
                    'flash_sale_announcement_text',
                    'Flash Sale Alert! Grab these deals before the timer runs out!'
                )
            self.write_config()

        # Initialize subscription attributes
        self.tier = self.config['Subscription'].get('tier', 'Trial')
        self.max_bins = self.tier_limits[self.tier]['bins']
        self.tier_var = tk.StringVar(value=self.tier)
        self.chat_id = self.config['Telegram'].get('chat_id', '')
        self.top_buyer_text = self.config['GUI'].get('top_buyer_text')
        self.giveaway_announcement_text = self.config['GUI'].get('giveaway_announcement_text')
        self.flash_sale_announcement_text = self.config['GUI'].get('flash_sale_announcement_text')

        # Secure API token for localhost calls
        self.api_token = os.urandom(16).hex()

        # Initialize Flask and SocketIO
        self.flask_app = Flask(__name__)
        self.socketio = SocketIO(self.flask_app, cors_allowed_origins="*", async_mode="threading")
        self.latest_bin_assignment = "Waiting for bidder..."

        # Configure styles
        style = ttk.Style()
        try:
            style.theme_use('clam')
            style.configure("Treeview", font=("Helvetica", 8), rowheight=20, background="#F5F5F5")
            style.configure("Treeview.Heading", font=("Helvetica", 8, "bold"), background="#0288D1", foreground="#FFFFFF")
            style.map("Treeview", background=[('selected', '#BBDEFB')])
        except Exception as e:
            logging.error(f"Style configuration failed: {e}", exc_info=True)
            messagebox.showerror("Style Error", f"Failed to configure styles: {e}")

        # Header Frame
        header = tk.Frame(root, bg="#212121")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        logo_path = get_resource_path("swiftapplogo.png")
        try:
            logo_img = Image.open(logo_path).resize((160, 50), Image.Resampling.LANCZOS)
            self.logo = ImageTk.PhotoImage(logo_img)
            tk.Label(header, image=self.logo, bg="#212121").pack(side="left", padx=10)
            tk.Label(
                header,
                text="- Powering Whatnot Auctions with Precision",
                font=("Helvetica", 12, "bold"),
                fg="#FFFFFF",
                bg="#212121"
            ).pack(side="left")
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

        # Tabbed Layout with ttk.Notebook
        self.notebook = ttk.Notebook(root)
        self.notebook.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=10, pady=10)

        # Transactions Tab
        self.transactions_tab = tk.Frame(self.notebook, bg="#FFFFFF")
        self.notebook.add(self.transactions_tab, text="Transactions")

        # Input Frame
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

        tk.Label(input_frame, text="Search:", bg="#FFFFFF", font=("Helvetica", 8)).grid(row=1, column=0, padx=5, sticky="e")
        self.search_entry = tk.Entry(input_frame, width=15, font=("Helvetica", 8), bg="#F5F5F5")
        self.search_entry.grid(row=1, column=1, padx=5)
        tk.Button(
            input_frame,
            text="?",
            command=lambda: self.show_field_info("search"),
            width=2,
            font=("Helvetica", 8),
            bg="#B0BEC5",
            fg="#FFFFFF"
        ).grid(row=1, column=2)
        tk.Button(
            input_frame,
            text="Search",
            command=self.search_bidders,
            bg="#0288D1",
            fg="#FFFFFF",
            font=("Helvetica", 8),
            width=12
        ).grid(row=1, column=3, padx=5)

        tk.Label(input_frame, text="Username:", bg="#FFFFFF", font=("Helvetica", 10, "bold")).grid(row=2, column=0, padx=5, sticky="e")
        self.username_entry = tk.Entry(input_frame, width=20, font=("Helvetica", 12, "bold"), bg="#F5F5F5")
        self.username_entry.grid(row=2, column=1, padx=5, pady=10, sticky="ew")
        self.username_entry.bind('<Button-1>', lambda event: self.paste_to_entry(self.username_entry))
        tk.Button(
            input_frame,
            text="?",
            command=lambda: self.show_field_info("username"),
            width=2,
            font=("Helvetica", 8),
            bg="#B0BEC5",
            fg="#FFFFFF"
        ).grid(row=2, column=2, pady=10)
        tk.Button(
            input_frame,
            text="Add Bidder",
            command=self.add_bidder,
            bg="#0288D1",
            fg="#FFFFFF",
            font=("Helvetica", 8),
            width=12
        ).grid(row=2, column=3, padx=5, pady=10)
        tk.Button(
            input_frame,
            text="?",
            command=lambda: self.show_field_info("add_bidder"),
            width=2,
            font=("Helvetica", 8),
            bg="#B0BEC5",
            fg="#FFFFFF"
        ).grid(row=2, column=4, pady=10)

        tk.Label(input_frame, text="Quantity:", bg="#FFFFFF", font=("Helvetica", 8)).grid(row=3, column=0, padx=5, sticky="e")
        self.qty_entry = tk.Entry(input_frame, width=15, font=("Helvetica", 8), bg="#F5F5F5")
        self.qty_entry.insert(0, "1")
        self.qty_entry.grid(row=3, column=1, padx=5)
        tk.Button(
            input_frame,
            text="?",
            command=lambda: self.show_field_info("quantity"),
            width=2,
            font=("Helvetica", 8),
            bg="#B0BEC5",
            fg="#FFFFFF"
        ).grid(row=3, column=2)

        tk.Label(input_frame, text="Weight:", bg="#FFFFFF", font=("Helvetica", 8)).grid(row=3, column=3, padx=5, sticky="e")
        self.weight_entry = ttk.Combobox(
            input_frame,
            values=["A", "B", "C", "D", "E", "F", "G", "H", "PU Only"],
            width=10,
            state="readonly",
            font=("Helvetica", 8)
        )
        self.weight_entry.grid(row=3, column=4, padx=5)
        tk.Button(
            input_frame,
            text="?",
            command=lambda: self.show_field_info("weight_class"),
            width=2,
            font=("Helvetica", 8),
            bg="#B0BEC5",
            fg="#FFFFFF"
        ).grid(row=3, column=5)

        self.giveaway_var = tk.BooleanVar()
        tk.Checkbutton(
            input_frame,
            text="Giveaway",
            variable=self.giveaway_var,
            bg="#FFFFFF",
            font=("Helvetica", 8)
        ).grid(row=4, column=1, padx=5, sticky="w")
        tk.Button(
            input_frame,
            text="?",
            command=lambda: self.show_field_info("giveaway"),
            width=2,
            font=("Helvetica", 8),
            bg="#B0BEC5",
            fg="#FFFFFF"
        ).grid(row=4, column=2)

        tk.Button(
            input_frame,
            text="Avg Sell Rate",
            command=lambda: self.show_avg_sell_rate(show_message=True),
            bg="#0288D1",
            fg="#FFFFFF",
            font=("Helvetica", 8),
            width=12
        ).grid(row=5, column=0, padx=5, pady=5)
        tk.Button(
            input_frame,
            text="?",
            command=lambda: self.show_field_info("avg_sell_rate"),
            width=2,
            font=("Helvetica", 8),
            bg="#B0BEC5",
            fg="#FFFFFF"
        ).grid(row=5, column=1, pady=5)
        tk.Button(
            input_frame,
            text="Start Giveaway",
            command=self.copy_giveaway_announcement,
            bg="#0288D1",
            fg="#FFFFFF",
            font=("Helvetica", 8),
            width=12
        ).grid(row=5, column=2, padx=5, pady=5)
        tk.Button(
            input_frame,
            text="?",
            command=lambda: self.show_field_info("start_giveaway"),
            width=2,
            font=("Helvetica", 8),
            bg="#B0BEC5",
            fg="#FFFFFF"
        ).grid(row=5, column=3, pady=5)
        tk.Button(
            input_frame,
            text="Start Flash Sale",
            command=self.copy_flash_sale_announcement,
            bg="#0288D1",
            fg="#FFFFFF",
            font=("Helvetica", 8),
            width=12
        ).grid(row=5, column=4, padx=5, pady=5)
        tk.Button(
            input_frame,
            text="?",
            command=lambda: self.show_field_info("start_flash_sale"),
            width=2,
            font=("Helvetica", 8),
            bg="#B0BEC5",
            fg="#FFFFFF"
        ).grid(row=5, column=5, pady=5)

        tk.Button(
            input_frame,
            text="Sort Asc",
            command=lambda: self.set_sort_order('asc'),
            bg="#0288D1",
            fg="#FFFFFF",
            font=("Helvetica", 8),
            width=12
        ).grid(row=6, column=0, padx=5, pady=5)
        tk.Button(
            input_frame,
            text="Sort Desc",
            command=lambda: self.set_sort_order('desc'),
            bg="#0288D1",
            fg="#FFFFFF",
            font=("Helvetica", 8),
            width=12
        ).grid(row=6, column=1, padx=5, pady=5)
        tk.Button(
            input_frame,
            text="?",
            command=lambda: self.show_field_info("sort_order"),
            width=2,
            font=("Helvetica", 8),
            bg="#B0BEC5",
            fg="#FFFFFF"
        ).grid(row=6, column=2, pady=5)

        tk.Button(
            input_frame,
            text="Export Bidders",
            command=self.print_bidders,
            bg="#0288D1",
            fg="#FFFFFF",
            font=("Helvetica", 8),
            width=12
        ).grid(row=7, column=0, padx=5, pady=5)
        tk.Button(
            input_frame,
            text="?",
            command=lambda: self.show_field_info("export_bidders"),
            width=2,
            font=("Helvetica", 8),
            bg="#B0BEC5",
            fg="#FFFFFF"
        ).grid(row=7, column=1, pady=5)
        tk.Button(
            input_frame,
            text="Import CSV",
            command=self.import_csv,
            bg="#0288D1",
            fg="#FFFFFF",
            font=("Helvetica", 8),
            width=12
        ).grid(row=7, column=2, padx=5, pady=5)
        tk.Button(
            input_frame,
            text="?",
            command=lambda: self.show_field_info("import_csv"),
            width=2,
            font=("Helvetica", 8),
            bg="#B0BEC5",
            fg="#FFFFFF"
        ).grid(row=7, column=3, pady=5)
        tk.Button(
            input_frame,
            text="Clear Data",
            command=self.clear_data,
            bg="#0288D1",
            fg="#FFFFFF",
            font=("Helvetica", 8),
            width=12
        ).grid(row=7, column=4, padx=5, pady=5)
        tk.Button(
            input_frame,
            text="?",
            command=lambda: self.show_field_info("clear_data"),
            width=2,
            font=("Helvetica", 8),
            bg="#B0BEC5",
            fg="#FFFFFF"
        ).grid(row=7, column=5, pady=5)

        self.search_result = tk.Label(
            self.transactions_tab, text="", wraplength=500, bg="#FFFFFF", font=("Helvetica", 8)
        )
        self.search_result.pack(pady=5)

        self.tree = ttk.Treeview(
            self.transactions_tab,
            columns=("Username", "Bin", "Qty", "Weight", "Giveaway", "GiveawayNum", "Timestamp"),
            show="tree headings",
            height=8
        )
        self.tree.heading("#0", text="")
        self.tree.column("#0", width=20)
        for col in ("Username", "Bin", "Qty", "Weight", "Giveaway", "GiveawayNum", "Timestamp"):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=80 if col != "Timestamp" else 120)
        self.tree.pack(fill="both", expand=True, padx=5, pady=5)
        scrollbar = ttk.Scrollbar(self.transactions_tab, orient="vertical", command=self.tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scrollbar.set)

        tk.Label(self.transactions_tab, text="Assigned Bins:", font=("Helvetica", 10, "bold"), bg="#FFFFFF", fg="#0288D1").pack(anchor="w", padx=5)
        self.bin_display = tk.Text(self.transactions_tab, height=4, width=60, font=("Helvetica", 10), bg="#F5F5F5")
        self.bin_display.pack(fill="x", padx=5, pady=5)
        self.bin_display.config(state="disabled")

        # Settings Tab
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
        tk.Label(settings_frame, text="Use {username} and {count}", bg="#FFFFFF", font=("Helvetica", 8), fg="#555").pack()

        tk.Label(settings_frame, text="Giveaway Announcement Text:", bg="#FFFFFF", font=("Helvetica", 8)).pack(pady=5)
        self.giveaway_entry = tk.Entry(settings_frame, width=40, font=("Helvetica", 8), bg="#F5F5F5")
        self.giveaway_entry.insert(0, self.giveaway_announcement_text)
        self.giveaway_entry.pack(pady=5)
        tk.Label(settings_frame, text="Use {number}", bg="#FFFFFF", font=("Helvetica", 8), fg="#555").pack()

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

        tk.Label(subscription_frame, text="Current Subscription Tier:", bg="#FFFFFF", font=("Helvetica", 8)).pack(pady=5)
        tk.Label(subscription_frame, text=f"{self.tier} (Max {self.max_bins} bins)", bg="#FFFFFF", font=("Helvetica", 8)).pack(pady=5)
        tk.Label(subscription_frame, text="Upgrade to:", bg="#FFFFFF", font=("Helvetica", 8)).pack(pady=5)
        self.tier_combobox = ttk.Combobox(
            subscription_frame,
            textvariable=self.tier_var,
            values=["Bronze", "Silver", "Gold"],
            state="readonly",
            width=10,
            font=("Helvetica", 8)
        )
        self.tier_combobox.pack(pady=5)
        tk.Button(
            subscription_frame,
            text="Upgrade",
            command=self.on_upgrade,
            bg="#43A047",
            fg="#FFFFFF",
            font=("Helvetica", 8)
        ).pack(pady=10)
        tk.Button(
            subscription_frame,
            text="?",
            command=lambda: self.show_field_info("upgrade"),
            width=2,
            font=("Helvetica", 8),
            bg="#B0BEC5",
            fg="#FFFFFF"
        ).pack(pady=5)

        # Analytics Tab
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
        self.stats_text = tk.Text(
            stats_frame, height=1, width=40, font=("Helvetica", 8), bg="#F5F5F5", cursor="hand2"
        )
        self.stats_text.pack(pady=2)
        self.stats_text.insert(tk.END, "No stats yet")
        self.stats_text.config(state="disabled")
        self.stats_text.bind("<Button-1>", lambda e: self.copy_stats_text())
        self.sell_rate_text = tk.Text(
            stats_frame, height=3, width=40, font=("Helvetica", 8), bg="#F5F5F5", cursor="hand2"
        )
        self.sell_rate_text.pack(pady=2)
        self.sell_rate_text.insert(tk.END, "No sell rate yet")
        self.sell_rate_text.config(state="disabled")
        self.sell_rate_text.bind("<Button-1>", lambda e: self.copy_sell_rate_text())

        # Footer
        footer = tk.Frame(root, bg="#212121")
        footer.grid(row=2, column=0, columnspan=2, sticky="ew", padx=5, pady=5)
        self.footer_label = tk.Label(
            footer,
            text=f"License ID: SS-2025-001 | SwiftSale(TM) (C) 2025 | Bins: {len(self.bidders)}/{self.max_bins}",
            font=("Helvetica", 8),
            fg="#B0BEC5",
            bg="#212121"
        )
        self.footer_label.pack(side="left", padx=10)

        # Initialize UI updates
        self.update_footer()
        self.update_top_buyers()
        self.update_stats()
        self.show_avg_sell_rate(show_message=False)

        # Grid weights
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(1, weight=1)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Start Flask server
        flask_thread = threading.Thread(target=self.run_flask, daemon=True)
        flask_thread.start()

        logging.info("Application initialized")

    # Flask routes and methods
    def run_flask(self):
        """Run the Flask server in a separate thread."""
        try:
            port = int(os.getenv('PORT', 5000))
            self.socketio.run(
                self.flask_app,
                host='0.0.0.0',
                port=port,
                debug=False
            )
            logging.info(f"Flask server started on port {port}")
        except Exception as e:
            logging.error(f"Flask server failed: {e}", exc_info=True)
            self.root.after(
                0, lambda: messagebox.showerror("Flask Error", f"Flask server failed: {e}")
            )

    @property
    def flask_routes(self):
        """Define Flask routes."""
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

        @self.socketio.on('connect')
        def handle_connect():
            self.socketio.emit('update', {'data': self.latest_bin_assignment})
            logging.info("Flask client connected")

        @self.flask_app.route('/create-checkout-session', methods=['POST'])
        def create_checkout_session():
            """Create a Stripe checkout session with token authentication."""
            try:
                token = request.headers.get('Authorization')
                if not token or token != f'Bearer {self.api_token}':
                    logging.warning("Unauthorized access to create-checkout-session")
                    return jsonify({'error': 'Unauthorized'}), 401
                data = request.get_json(force=True)
                tier = data.get('tier')
                price_id = next((pid for pid, t in PRICE_MAP.items() if t == tier), None)
                if not price_id:
                    logging.error(f"Invalid tier: {tier}")
                    return jsonify({'error': 'Invalid tier'}), 400
                session = stripe.checkout.Session.create(
                    payment_method_types=['card'],
                    line_items=[{'price': price_id, 'quantity': 1}],
                    mode='subscription',
                    success_url=request.url_root + 'success?subscription_id={CHECKOUT_SESSION_ID}',
                    cancel_url=request.url_root + 'cancel'
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
        def stripe_webhook():
            """Handle Stripe webhook events for subscription updates."""
            logging.info("Stripe webhook received a request")
            try:
                payload = request.data
                sig = request.headers.get('Stripe-Signature')
                event = stripe.Webhook.construct_event(payload, sig, WEBHOOK_SECRET)
                if event['type'] == 'checkout.session.completed':
                    session = event['data']['object']
                    sub_id = session.get('subscription')
                    if not sub_id:
                        logging.error("No subscription ID on session")
                    else:
                        subscription = stripe.Subscription.retrieve(
                            sub_id, expand=['items.data.price']
                        )
                        price_id = subscription['items']['data'][0]['price']['id']
                        new_tier = PRICE_MAP.get(price_id)
                        if new_tier:
                            logging.info(f"Updating subscription to {new_tier}")
                            self.update_subscription(new_tier, sub_id)
            except stripe.error.SignatureVerificationError as e:
                logging.error(f"Webhook signature verification failed: {e}", exc_info=True)
            except Exception as e:
                logging.error(f"Exception in stripe_webhook: {e}", exc_info=True)
            return '', 200

    def show_field_info(self, field_name):
        """Display information about a specific field in a message box."""
        field_info = {
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
        info_text = field_info.get(field_name, "No information available for this field.")
        messagebox.showinfo("Field Information", info_text)

    def write_config(self):
        """Write configuration to file."""
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                self.config.write(f)
            logging.info("config.ini successfully updated")
        except Exception as e:
            logging.error(f"Failed to write config.ini: {e}", exc_info=True)
            messagebox.showerror("Config Error", f"Failed to save configuration: {e}")

    def on_closing(self):
        """Handle application closure."""
        logging.info("Closing application")
        global loop
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
        self.root.destroy()

    def paste_to_entry(self, entry):
        """Paste clipboard content into the specified entry field."""
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

    def validate_license_key(self, key, tier):
        """Validate license key based on tier."""
        if tier == 'Trial':
            return True
        return bool(key and key.startswith('sub_'))

    def update_subscription(self, tier, license_key):
        """Update subscription tier and persist to config."""
        self.tier = tier
        self.max_bins = self.tier_limits[tier]['bins']
        self.config['Subscription']['tier'] = tier
        self.config['Subscription']['license_key'] = license_key
        self.write_config()
        self.show_label.config(text=f"Show ID: {self.show_id} | Tier: {self.tier} (Max {self.max_bins} bins)")
        self.update_footer()
        logging.info(f"Subscription updated: Tier={tier}, License Key={license_key}")

    def update_footer(self):
        """Update footer with current bin usage."""
        self.footer_label.config(
            text=f"License ID: SS-2025-001 | SwiftSale(TM) (C) 2025 | Bins: {len(self.bidders)}/{self.max_bins}"
        )

    def save_settings(self):
        """Save settings from the settings tab."""
        try:
            self.chat_id = self.chat_id_entry.get().strip()
            self.top_buyer_text = self.top_buyer_entry.get().strip()
            self.giveaway_announcement_text = self.giveaway_entry.get().strip()
            self.flash_sale_announcement_text = self.flash_sale_entry.get().strip()
            if not self.chat_id:
                messagebox.showerror("Error", "Chat ID required!")
                logging.warning("Save settings: Empty chat ID")
                return
            self.config['Telegram']['chat_id'] = self.chat_id
            self.config['GUI']['top_buyer_text'] = self.top_buyer_text
            self.config['GUI']['giveaway_announcement_text'] = self.giveaway_announcement_text
            self.config['GUI']['flash_sale_announcement_text'] = self.flash_sale_announcement_text
            self.write_config()
            messagebox.showinfo("Success", "Settings saved!")
            logging.info("Settings saved")
            self.update_top_buyers()
        except Exception as e:
            logging.error(f"Failed to save settings: {e}", exc_info=True)
            messagebox.showerror("Settings Error", f"Failed to save settings: {e}")

    def update_bin_display(self):
        """Update bin display with latest assignments."""
        self.bin_display.config(state="normal")
        self.bin_display.delete(1.0, tk.END)
        if not self.bidders:
            self.bin_display.insert(tk.END, "No bidders.")
        else:
            if self.last_bidder and self.last_bidder in self.bidders:
                data = self.bidders[self.last_bidder]
                display_username = data.get('original_username', self.last_bidder)
                self.bin_display.insert(tk.END, f"Bin {data['bin']}: {display_username}\n")
            remaining_bidders = [(username, data) for username, data in self.bidders.items() if username != self.last_bidder]
            sorted_bidders = sorted(remaining_bidders, key=lambda x: x[1]['bin'], reverse=True)
            for username, data in sorted_bidders:
                display_username = data.get('original_username', username)
                self.bin_display.insert(tk.END, f"Bin {data['bin']}: {display_username}\n")
        self.bin_display.config(state="disabled")
        logging.info("Updated bin display")

    async def send_bin_number(self, username, bin_number):
        """Send bin number to Telegram with error handling."""
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

    def copy_sell_rate_text(self):
        """Copy sell rate text to clipboard."""
        text = self.sell_rate_text.get("1.0", tk.END).strip()
        pyperclip.copy(text)
        messagebox.showinfo("Copied", f"Copied: {text}")
        logging.info(f"Copied sell rate text: {text}")

    def show_avg_sell_rate(self, show_message=True):
        """Calculate and display average sell rate."""
        try:
            if not self.bidders:
                sell_rate_text = "No transactions to analyze."
            else:
                timestamps = []
                for user, data in self.bidders.items():
                    for txn in data['transactions']:
                        ts = datetime.datetime.strptime(txn['timestamp'], "%Y-%m-%d %I:%M:%S %p")
                        timestamps.append(ts)
                if len(timestamps) < 2:
                    sell_rate_text = "Need at least two transactions."
                else:
                    timestamps.sort()
                    time_diffs = [(timestamps[i + 1] - timestamps[i]).total_seconds() for i in range(len(timestamps) - 1)]
                    avg_seconds = sum(time_diffs) / len(time_diffs)
                    minutes, seconds = divmod(int(avg_seconds), 60)
                    time_str = f"{minutes} min {seconds} sec" if minutes > 0 else f"{seconds} sec"
                    est_two_hour = int((2 * 3600) / avg_seconds) if avg_seconds > 0 else 0
                    est_three_hour = int((3 * 3600) / avg_seconds) if avg_seconds > 0 else 0
                    sell_rate_text = (
                        f"SwiftSale(TM) Your sellers' current time per sale is {time_str}. "
                        f"At this sell rate you're expected to sell a total of {est_two_hour} items "
                        f"for a 2-hour show or {est_three_hour} items for a 3-hour show."
                    )
            self.sell_rate_text.config(state="normal")
            self.sell_rate_text.delete("1.0", tk.END)
            self.sell_rate_text.insert(tk.END, sell_rate_text)
            self.sell_rate_text.config(state="disabled")
            if show_message:
                messagebox.showinfo("Average Sell Rate", sell_rate_text)
            logging.info(f"Avg sell rate: {sell_rate_text}")
        except Exception as e:
            logging.error(f"Failed to calculate sell rate: {e}", exc_info=True)
            messagebox.showerror("Error", f"Failed to calculate sell rate: {e}")

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
            for i, label in enumerate(self.top_buyers_labels):
                if i < len(self.top_buyers):
                    username, count = self.top_buyers[i]
                    text = self.top_buyer_text.format(username=username, count=count)
                    label.config(text=f"{username}: {count} items\n{text}")
                else:
                    label.config(text="")
            logging.info("Updated top buyers display")
        except Exception as e:
            logging.error(f"Failed to update top buyers: {e}", exc_info=True)

    def copy_stats_text(self):
        """Copy stats text to clipboard."""
        text = self.stats_text.get("1.0", tk.END).strip()
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
            self.stats_text.config(state="normal")
            self.stats_text.delete("1.0", tk.END)
            self.stats_text.insert(tk.END, stats_text)
            self.stats_text.config(state="disabled")
            logging.info("Updated stats display")
        except Exception as e:
            logging.error(f"Failed to update stats: {e}", exc_info=True)

    def copy_giveaway_announcement(self):
        """Copy giveaway announcement to clipboard."""
        try:
            text = self.giveaway_announcement_text.format(number=self.giveaway_count + 1)
            pyperclip.copy(text)
            self.announcement_text.config(state="normal")
            self.announcement_text.delete("1.0", tk.END)
            self.announcement_text.insert(tk.END, text)
            self.announcement_text.config(state="disabled")
            messagebox.showinfo("Copied", f"Copied: {text}")
            logging.info(f"Copied giveaway announcement: {text}")
        except Exception as e:
            logging.error(f"Failed to copy giveaway announcement: {e}", exc_info=True)
            messagebox.showerror("Error", f"Failed to copy announcement: {e}")

    def copy_flash_sale_announcement(self):
        """Copy flash sale announcement to clipboard."""
        try:
            text = self.flash_sale_announcement_text
            pyperclip.copy(text)
            self.announcement_text.config(state="normal")
            self.announcement_text.delete("1.0", tk.END)
            self.announcement_text.insert(tk.END, text)
            self.announcement_text.config(state="disabled")
            messagebox.showinfo("Copied", f"Copied: {text}")
            logging.info(f"Copied flash sale announcement: {text}")
        except Exception as e:
            logging.error(f"Failed to copy flash sale announcement: {e}", exc_info=True)
            messagebox.showerror("Error", f"Failed to copy announcement: {e}")

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

        try:
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
                    loop
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

    def import_csv(self):
        """Import bidder data from CSV with optimized parsing."""
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
                    bin_num = int(row["bin"])
                    qty = int(row["qty"])
                    giveaway_num = int(row["giveaway_num"]) if row["giveaway_num"] else 0
                    is_giveaway = row["giveaway"].lower() in ("yes", "true", "1")
                    datetime.datetime.strptime(row["timestamp"], "%Y-%m-%d %I:%M:%S %p")  # Validate timestamp
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
                        loop
                    )
                messagebox.showinfo("Info", f"New show: {self.show_id}")
                self.update_footer()
                logging.info(f"Started new show: {self.show_id}")
        except Exception as e:
            logging.error(f"Failed to clear data: {e}", exc_info=True)
            messagebox.showerror("Error", f"Failed to start new show: {e}")

    def search_bidders(self):
        """Search for bidders by username or transaction details."""
        try:
            q = self.search_entry.get().strip().lower()
            if not q:
                self.search_result.config(text="Enter query!")
                logging.warning("Search bidders: Empty query")
                return
            if q in self.bidders:
                data = self.bidders[q]
                display_username = data.get('original_username', q)
                self.search_result.config(
                    text=f"Username: {display_username} | Bin: {data['bin']} | Items: {data['total_items']}"
                )
                logging.info(f"Search bidders: Found {display_username}")
                return
            res = []
            for username, data in self.bidders.items():
                display_username = data.get('original_username', username)
                for t in data['transactions']:
                    if q in username or q in str(t['qty']) or q in t['weight'].lower():
                        res.append(
                            f"{display_username} | Bin {data['bin']} | {t['qty']}x | {t['weight']} | # {t['giveaway_num']} | {t['timestamp']}"
                        )
            self.search_result.config(text='\n'.join(res) if res else 'No matches!')
            logging.info(f"Search bidders: {'Found matches' if res else 'No matches'}")
        except Exception as e:
            logging.error(f"Failed to search bidders: {e}", exc_info=True)
            messagebox.showerror("Error", f"Search failed: {e}")

    def set_sort_order(self, order):
        """Set sort order for treeview."""
        self.sort_order = order
        self.update_treeview()
        logging.info(f"Sort order set to {order}")

    def update_treeview(self):
        """Update treeview with sorted transactions."""
        try:
            self.tree.delete(*self.tree.get_children())
            transactions = [
                {
                'username': username,
                'display_username': data.get('original_username', username),
                'bin': data['bin'],
                'txn': t
                }
                for username, data in self.bidders.items()
                for t in data['transactions']
            ]
            if not transactions:
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
                        parent = self.tree.insert(
                              '', 'end', text='+', open=False,
                              values=(
                             display_username,
                             t['bin'],
                             data['qty'],
                             data['weight'],
                             data.get('minimum', ''),                        
                             'Yes' if data['giveaway'] else 'No',
                             data['giveaway_num'],
                             data['timestamp']
                       )
                    )
                    user_parents[username] = parent
             else:
                    self.tree.insert(
                    user_parents[username], 'end',
                    values=(
                        '',  # blank for the username column
                        t['bin'],
                        data['qty'],
                        data['weight'],
                        data.get('minimum', ''),                        
                        'Yes' if data['giveaway'] else 'No',
                        data['giveaway_num'],
                        data['timestamp']
                    )
                )

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
            with open(self.bidder_csv_path, mode="a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=csv_columns)
                if os.path.getsize(self.bidder_csv_path) == 0:
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
        except Exception as e:
            logging.error(f"Bidders export failed: {e}", exc_info=True)
            messagebox.showerror("Error", f"Export failed: {e}")

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
        """Initiate Stripe checkout with secure API call."""
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
            if resp.status_code == 200:
                webbrowser.open(resp.json()['url'])
                logging.info(f"Opened Stripe checkout for tier: {tier}")
            else:
                logging.error(f"Failed to create checkout session: {resp.status_code} {resp.text}")
                messagebox.showerror("Checkout Error", f"Failed to initiate checkout: {resp.status_code}")
        except requests.RequestException as e:
            logging.error(f"Checkout request failed: {e}", exc_info=True)
            messagebox.showerror("Checkout Error", f"Failed to initiate checkout: {e}")
        except Exception as e:
            logging.error(f"Unexpected error in on_upgrade: {e}", exc_info=True)
            messagebox.showerror("Error", f"An unexpected error occurred: {e}")

if __name__ == '__main__':
    root = tk.Tk()
    app = SCDWhatnotGUI(root)
    root.mainloop()