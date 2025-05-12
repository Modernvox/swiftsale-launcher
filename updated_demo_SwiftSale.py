import tkinter as tk
from tkinter import messagebox, ttk, filedialog
from PIL import Image, ImageTk
import os, sys, datetime, csv, hashlib
import pdfplumber, fitz, subprocess, pytesseract, re
from telegram import Bot
import asyncio, configparser
import warnings
import logging
import pyperclip
from flask import Flask, render_template, jsonify
from flask_socketio import SocketIO
import threading
import platform
import shutil
from dotenv import load_dotenv  # Added for .env support

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

# Tesseract path setup
if hasattr(sys, '_MEIPASS'):
    pytesseract.pytesseract.tesseract_cmd = get_resource_path('tesseract/tesseract.exe')
else:
    default_tesseract_path = r'C:\Program Files\Tesseract-OCR\tesseract.exe' if platform.system() == 'Windows' else shutil.which('tesseract') or '/usr/bin/tesseract'
    if not os.path.exists(default_tesseract_path) and not shutil.which(default_tesseract_path):
        logging.error(f"Tesseract not found at {default_tesseract_path}")
        tk.Tk().withdraw()
        messagebox.showerror("Tesseract Error", f"Tesseract not found at {default_tesseract_path}. Please install Tesseract-OCR.")
        sys.exit(1)
    pytesseract.pytesseract.tesseract_cmd = default_tesseract_path

# Load environment variables
load_dotenv()

# Asyncio event loop
asyncio.set_event_loop(asyncio.new_event_loop())

class SCDWhatnotGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("SwiftSale™– Developed with Whatnot sellers in mind ")
        self.root.geometry("1000x700")
        self.root.configure(bg="#E6F0FA")

        self.bidders = {}
        self.next_bin = 1
        self.next_giveaway_num = 1
        self.giveaway_count = 0
        self.label_data = []
        self.last_bidder = None
        self.show_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.show_start_time = datetime.datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
        self.bidder_csv_path = os.path.join(log_dir, f"bidder_history_{self.show_id}.csv")

        # Tier system configuration
        self.tier_limits = {
            "Trial": {"bins": 25, "labels": 25},
            "Bronze": {"bins": 50, "labels": float('inf')},
            "Silver": {"bins": 150, "labels": float('inf')},
            "Gold": {"bins": 300, "labels": float('inf')}
        }
        self.valid_tiers = list(self.tier_limits.keys())

        # Load configuration
        self.config = configparser.ConfigParser()
        self.config_path = get_resource_path("config.ini")  # Updated to use get_resource_path
        if not os.path.exists(self.config_path):
            self.config['Telegram'] = {'bot_token': '', 'chat_id': ''}
            self.config['Subscription'] = {'tier': 'Trial', 'license_key': ''}
            self.config['GUI'] = {
                'top_buyer_text': 'Great job, {username}! You’ve snagged {count} items!',
                'giveaway_announcement_text': 'Giveaway #{number} Alert! Must be following us & share the stream to enter! Winner announced in a few minutes!',
                'flash_sale_announcement_text': '🚨 Flash Sale Alert! Grab these deals before the timer runs out! 🚨'
            }
            with open(self.config_path, 'w') as configfile:
                self.config.write(configfile)
        self.config.read(self.config_path)

        # Ensure sections exist in config
        for section in ['Telegram', 'Subscription', 'GUI']:
            if section not in self.config:
                self.config[section] = {}
                if section == 'Telegram':
                    self.config[section]['bot_token'] = ''
                    self.config[section]['chat_id'] = ''
                elif section == 'Subscription':
                    self.config[section]['tier'] = 'Trial'
                    self.config[section]['license_key'] = ''
                elif section == 'GUI':
                    self.config[section]['top_buyer_text'] = 'Great job, {username}! You’ve snagged {count} items!'
                    self.config[section]['giveaway_announcement_text'] = 'Giveaway #{number} Alert! Must be following us & share the stream to enter! Winner announced in a few minutes!'
                    self.config[section]['flash_sale_announcement_text'] = '🚨 Flash Sale Alert! Grab these deals before the timer runs out! 🚨'
                with open(self.config_path, 'w') as configfile:
                    self.config.write(configfile)

        self.bot_token = self.config['Telegram'].get('bot_token', '')
        self.chat_id = self.config['Telegram'].get('chat_id', '')
        self.tier = self.config['Subscription'].get('tier', 'Trial')
        self.license_key = self.config['Subscription'].get('license_key', '')
        self.top_buyer_text = self.config['GUI'].get('top_buyer_text', 'Great job, {username}! You’ve snagged {count} items!')
        self.giveaway_announcement_text = self.config['GUI'].get('giveaway_announcement_text', 'Giveaway #{number} Alert! Must be following us & share the stream to enter! Winner announced in a few minutes!')
        self.flash_sale_announcement_text = self.config['GUI'].get('flash_sale_announcement_text', '🚨 Flash Sale Alert! Grab these deals before the timer runs out! 🚨')

        # Validate license key and tier
        if self.tier == 'Trial' and not self.license_key:
            pass
        elif not self.validate_license_key(self.license_key, self.tier):
            self.tier = 'Trial'
            self.license_key = ''
            self.config['Subscription']['tier'] = self.tier
            self.config['Subscription']['license_key'] = self.license_key
            with open(self.config_path, 'w') as configfile:
                self.config.write(configfile)
        self.max_bins = self.tier_limits[self.tier]['bins']
        self.max_labels = self.tier_limits[self.tier]['labels']
        if not self.bot_token:
            logging.warning("Telegram bot token missing in config.ini")
            messagebox.showerror("Error", "Telegram bot token missing in config.ini. Set in Settings.")
        self.bot = Bot(token=self.bot_token) if self.bot_token else None

        # Initialize Flask and SocketIO
        self.flask_app = Flask(__name__)
        self.socketio = SocketIO(self.flask_app)
        self.latest_bin_assignment = "Waiting for bidder..."

        @self.flask_app.route('/')
        def index():
            return render_template('index.html')

        @self.flask_app.route('/get_latest')
        def get_latest():
            return jsonify({'data': self.latest_bin_assignment})

        @self.socketio.on('connect')
        def handle_connect():
            self.socketio.emit('update', {'data': self.latest_bin_assignment})
            logging.info("Flask client connected")

        def run_flask():
            try:
                port = int(os.environ.get('PORT', 5000))
                self.socketio.run(self.flask_app, host='0.0.0.0', port=port, allow_unsafe_werkzeug=True)
                logging.info(f"Flask server started on port {port}")
            except Exception as e:
                logging.error(f"Flask server failed: {str(e)}")
                self.root.after(0, lambda: messagebox.showerror("Flask Error", f"Flask server failed: {str(e)}"))

        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()

        # Expanded field_info for new buttons
        self.field_info = {
            "search": "Search for bidder by username or partial matches for transactions.",
            "username": "Enter Whatnot username to assign bin number. Click field to auto-paste copied username.",
            "quantity": "Number of items won (default: 1).",
            "weight_class": "Select weight class or 'PU Only' (optional).",
            "giveaway": "Mark as giveaway to assign unique number.",
            "label_text": "Custom label text (e.g., 'Bin: {bin_number}').",
            "text_x": "X-coordinate for label text (pixels).",
            "text_y": "Y-coordinate for label text (pixels).",
            "bin_range": "Bin range for printing labels (e.g., '1-25').",
            "avg_sell_rate": "Calculate average time between item sales and estimated totals for 2-hour and 3-hour shows. Click displayed rate to copy for chat.",
            "settings": "Opens the Settings window to customize subscription tier, top buyer text, giveaway announcement text, and flash sale announcement text.",
            "start_giveaway": "Copies a giveaway announcement to the clipboard for pasting into Whatnot chat. Includes an auto-incrementing giveaway number. Text is customizable in Settings.",
            "start_flash_sale": "Copies a flash sale announcement to the clipboard for pasting into Whatnot chat to promote time-limited deals. Text is customizable in Settings.",
            "top_buyers": "Shows the top 3 buyers with the most items won. Click a buyer’s name to copy a shoutout message for Whatnot chat.",
            "add_bidder": "Assigns a bin number to the entered username and logs the transaction.",
            "print_bidders": "Exports bidder data to a CSV file for record-keeping.",
            "import_bidders": "Imports bidder data from a previously exported CSV file to restore a past show.",
            "import_labels": "Imports labels from a PDF file, matching usernames to bins.",
            "print_labels": "Prints labels for the specified bin range with custom text.",
            "test_print": "Prints a single test label to verify printer settings.",
            "preview_labels": "Displays a preview of the first label with applied text.",
            "export_labels": "Exports label data to a CSV file.",
            "clear_data": "Clears all data and starts a new show.",
            "printer_guide": "Shows instructions for setting up your printer."
        }

        # Main container with two columns
        self.main_container = tk.Frame(root, bg="#E6F0FA")
        self.main_container.grid(row=1, column=0, columnspan=2, padx=5, pady=5, sticky="nsew")
        self.main_container.grid_columnconfigure(0, weight=3)
        self.main_container.grid_columnconfigure(1, weight=1)
        self.main_container.grid_rowconfigure(0, weight=1)

        # Header Frame
        header = tk.Frame(root, bg="#1A2526")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=2, pady=2)
        logo_path = get_resource_path("SwiftSale.png")  # Updated to use get_resource_path
        try:
            logo_img = Image.open(logo_path).resize((75, 60), Image.Resampling.LANCZOS)
            self.logo = ImageTk.PhotoImage(logo_img)
            tk.Label(header, image=self.logo, bg="#1A2526").pack(side="left", padx=5)
            tk.Label(header, text="SwiftSale™", font=("Helvetica", 12, "bold"), fg="#FFFFFF", bg="#1A2526").pack(side="left")
        except Exception as e:
            logging.warning(f"Failed to load logo: {str(e)}")
            tk.Label(header, text="SwiftSale™", font=("Helvetica", 12, "bold"), fg="#FFFFFF", bg="#1A2526").pack(side="left", padx=5)
        tk.Label(header, text=f"Show ID: {self.show_id} | Tier: {self.tier} (Max {self.max_bins} bins, {self.max_labels if self.max_labels != float('inf') else 'Unlimited'} labels)", font=("Helvetica", 8), fg="#FFFFFF", bg="#1A2526", name="header_label").pack(side="right", padx=5)

        # Main Frame (Left Column)
        main = tk.Frame(self.main_container, bg="#E6F0FA")
        main.grid(row=0, column=0, padx=5, pady=5, sticky="nsew")
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(4, weight=1)

        # Input Frame
        input_frame = tk.Frame(main, bg="#E6F0FA")
        input_frame.pack(fill="x", padx=5, pady=2)
        input_frame.grid_columnconfigure(1, weight=1)

        # Announcement Text Display
        self.announcement_text = tk.Text(input_frame, height=2, width=50, font=("Helvetica", 8), bg="#FFFFFF", state="disabled")
        self.announcement_text.grid(row=0, column=0, columnspan=5, padx=2, pady=2)
        self.current_bidder_label = tk.Label(input_frame, text="", font=("Helvetica", 10, "bold"), bg="#E6F0FA")
        self.current_bidder_label.grid(row=0, column=5, padx=5, sticky="w")

        # Search and Settings
        tk.Label(input_frame, text="Search:", bg="#E6F0FA", font=("Helvetica", 8)).grid(row=1, column=0, padx=2, sticky="e")
        self.search_entry = tk.Entry(input_frame, width=15, font=("Helvetica", 8))
        self.search_entry.grid(row=1, column=1, padx=2)
        tk.Button(input_frame, text="?", command=lambda: self.show_field_info("search"), width=2, font=("Helvetica", 8)).grid(row=1, column=2)
        tk.Button(input_frame, text="Search", command=self.search_bidders, bg="#E63946", fg="#FFFFFF", font=("Helvetica", 8)).grid(row=1, column=3, padx=2)
        tk.Button(input_frame, text="Settings", command=self.open_settings, bg="#FFA500", fg="#FFFFFF", font=("Helvetica", 8)).grid(row=1, column=4, padx=2)
        tk.Button(input_frame, text="?", command=lambda: self.show_field_info("settings"), width=2, font=("Helvetica", 8)).grid(row=1, column=5)

        # Transaction Inputs
        tk.Label(input_frame, text="Username:", bg="#E6F0FA", font=("Helvetica", 10, "bold")).grid(row=2, column=0, padx=2, sticky="e")
        self.username_entry = tk.Entry(input_frame, width=20, font=("Helvetica", 12, "bold"), bg="#FFFFCC")
        self.username_entry.grid(row=2, column=1, padx=2, pady=5, sticky="ew")
        self.username_entry.bind('<Button-1>', lambda event: self.paste_to_entry(self.username_entry))
        tk.Button(input_frame, text="?", command=lambda: self.show_field_info("username"), width=2, font=("Helvetica", 8)).grid(row=2, column=2, pady=5)

        tk.Label(input_frame, text="Quantity:", bg="#E6F0FA", font=("Helvetica", 8)).grid(row=3, column=0, padx=2, sticky="e")
        self.qty_entry = tk.Entry(input_frame, width=15, font=("Helvetica", 8))
        self.qty_entry.insert(0, "1")
        self.qty_entry.grid(row=3, column=1, padx=2)
        tk.Button(input_frame, text="?", command=lambda: self.show_field_info("quantity"), width=2, font=("Helvetica", 8)).grid(row=3, column=2)

        tk.Label(input_frame, text="Weight:", bg="#E6F0FA", font=("Helvetica", 8)).grid(row=3, column=3, padx=2, sticky="e")
        self.weight_entry = ttk.Combobox(input_frame, values=["A", "B", "C", "D", "E", "F", "G", "H", "PU Only"], width=10, state="readonly", font=("Helvetica", 8))
        self.weight_entry.grid(row=3, column=4, padx=2)
        tk.Button(input_frame, text="?", command=lambda: self.show_field_info("weight_class"), width=2, font=("Helvetica", 8)).grid(row=3, column=5)

        self.giveaway_var = tk.BooleanVar()
        tk.Checkbutton(input_frame, text="Giveaway", variable=self.giveaway_var, bg="#E6F0FA", font=("Helvetica", 8)).grid(row=4, column=1, padx=2, sticky="w")
        tk.Button(input_frame, text="?", command=lambda: self.show_field_info("giveaway"), width=2, font=("Helvetica", 8)).grid(row=4, column=2)

        tk.Label(input_frame, text="Label Text:", bg="#E6F0FA", font=("Helvetica", 8)).grid(row=5, column=0, padx=2, sticky="e")
        self.label_text_entry = tk.Entry(input_frame, width=15, font=("Helvetica", 8))
        self.label_text_entry.insert(0, "Bin: {bin_number}")
        self.label_text_entry.grid(row=5, column=1, padx=2)
        tk.Button(input_frame, text="?", command=lambda: self.show_field_info("label_text"), width=2, font=("Helvetica", 8)).grid(row=5, column=2)

        tk.Label(input_frame, text="Text X:", bg="#E6F0FA", font=("Helvetica", 8)).grid(row=5, column=3, padx=2, sticky="e")
        self.text_x_entry = tk.Entry(input_frame, width=10, font=("Helvetica", 8))
        self.text_x_entry.insert(0, "50")
        self.text_x_entry.grid(row=5, column=4, padx=2)
        tk.Button(input_frame, text="?", command=lambda: self.show_field_info("text_x"), width=2, font=("Helvetica", 8)).grid(row=5, column=5)

        tk.Label(input_frame, text="Text Y:", bg="#E6F0FA", font=("Helvetica", 8)).grid(row=6, column=3, padx=2, sticky="e")
        self.text_y_entry = tk.Entry(input_frame, width=10, font=("Helvetica", 8))
        self.text_y_entry.insert(0, "50")
        self.text_y_entry.grid(row=6, column=4, padx=2)
        tk.Button(input_frame, text="?", command=lambda: self.show_field_info("text_y"), width=2, font=("Helvetica", 8)).grid(row=6, column=5)

        tk.Label(input_frame, text="Bin Range:", bg="#E6F0FA", font=("Helvetica", 8)).grid(row=6, column=0, padx=2, sticky="e")
        self.bin_range_entry = tk.Entry(input_frame, width=15, font=("Helvetica", 8))
        self.bin_range_entry.insert(0, f"1-{self.max_bins}")
        self.bin_range_entry.grid(row=6, column=1, padx=2)
        tk.Button(input_frame, text="?", command=lambda: self.show_field_info("bin_range"), width=2, font=("Helvetica", 8)).grid(row=6, column=2)

        # New Buttons (Avg Sell Rate, Start Giveaway, Start Flash Sale)
        tk.Button(input_frame, text="Avg Sell Rate", command=lambda: self.show_avg_sell_rate(show_message=True), bg="#4682B4", fg="#FFFFFF", font=("Helvetica", 8)).grid(row=4, column=3, padx=2)
        tk.Button(input_frame, text="?", command=lambda: self.show_field_info("avg_sell_rate"), width=2, font=("Helvetica", 8)).grid(row=4, column=4)
        tk.Button(input_frame, text="Start Giveaway", command=self.copy_giveaway_announcement, bg="#FFD700", fg="#FFFFFF", font=("Helvetica", 8)).grid(row=4, column=5, padx=2)
        tk.Button(input_frame, text="?", command=lambda: self.show_field_info("start_giveaway"), width=2, font=("Helvetica", 8)).grid(row=4, column=6)
        tk.Button(input_frame, text="Start Flash Sale", command=self.copy_flash_sale_announcement, bg="#FF4500", fg="#FFFFFF", font=("Helvetica", 8)).grid(row=4, column=7, padx=2)
        tk.Button(input_frame, text="?", command=lambda: self.show_field_info("start_flash_sale"), width=2, font=("Helvetica", 8)).grid(row=4, column=8)

        # Button Frame
        btn_frame = tk.Frame(main, bg="#E6F0FA")
        btn_frame.pack(fill="x", padx=5, pady=2)
        buttons = [
            ("Add Bidder", self.add_bidder, "#2ECC71", "add_bidder"),
            ("Print Bidders", self.print_bidders, "#E63946", "print_bidders"),
            ("Import Labels", self.import_labels, "#3498DB", "import_labels"),
            ("Print Labels", self.print_labels, "#9B59B6", "print_labels"),
            ("Test Print", self.test_print_label, "#FF5733", "test_print"),
            ("Preview Labels", self.preview_labels, "#00CED1", "preview_labels"),
            ("Export Labels", self.export_labels, "#228B22", "export_labels"),
            ("Clear Data", self.clear_data, "#A8D5E2", "clear_data"),
            ("Printer Guide", self.show_printer_setup, "#F1C40F", "printer_guide")
        ]
        for i, (text, cmd, bg, field) in enumerate(buttons):
            tk.Button(btn_frame, text=text, command=cmd, bg=bg, fg="#FFFFFF", width=12, font=("Helvetica", 8)).grid(row=0, column=i, padx=2, pady=(0, 2))
            tk.Button(btn_frame, text="?", command=lambda f=field: self.show_field_info(f), width=2, font=("Helvetica", 8)).grid(row=1, column=i, padx=2, pady=(0, 2))

        # Search Result
        self.search_result = tk.Label(main, text="", wraplength=500, bg="#E6F0FA", font=("Helvetica", 8))
        self.search_result.pack(pady=2)

        # Treeview
        self.tree = ttk.Treeview(main, columns=("Username", "Bin", "Qty", "Weight", "Giveaway", "GiveawayNum", "Timestamp"), show="tree headings", height=8)
        style = ttk.Style()
        style.configure("Treeview", font=("Helvetica", 8), rowheight=18)
        style.configure("Treeview.Heading", font=("Helvetica", 8, "bold"))
        self.tree.heading("#0", text="")
        self.tree.column("#0", width=20)
        for col in ("Username", "Bin", "Qty", "Weight", "Giveaway", "GiveawayNum", "Timestamp"):
            self.tree.heading(col, text=col)
            self.tree.column(col, width=80 if col != "Timestamp" else 120)
        self.tree.pack(fill="both", expand=True, padx=5, pady=2)
        scrollbar = ttk.Scrollbar(main, orient="vertical", command=self.tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scrollbar.set)

        # Bin Display
        tk.Label(main, text="Assigned Bins:", font=("Helvetica", 10, "bold"), bg="#E6F0FA").pack(anchor="w", padx=5)
        self.bin_display = tk.Text(main, height=4, width=60, font=("Helvetica", 10), bg="#FFFFFF")
        self.bin_display.pack(fill="x", padx=5, pady=2)
        self.bin_display.config(state="disabled")

        # Right Column (Top Buyers and Stats)
        top_buyers_frame = tk.Frame(self.main_container, bg="#E6F0FA", bd=2, relief="groove")
        top_buyers_frame.grid(row=0, column=1, padx=5, pady=5, sticky="nsew")
        top_buyers_header = tk.Frame(top_buyers_frame, bg="#E6F0FA")
        top_buyers_header.pack(fill="x")
        tk.Label(top_buyers_header, text="Top Buyers", font=("Helvetica", 10, "bold"), bg="#E6F0FA").pack(side="left", padx=5, pady=2)
        tk.Button(top_buyers_header, text="?", command=lambda: self.show_field_info("top_buyers"), width=2, font=("Helvetica", 8)).pack(side="right", padx=5)
        self.top_buyers_labels = []
        for i in range(3):
            label = tk.Label(top_buyers_frame, text="", font=("Helvetica", 8), bg="#E6F0FA", wraplength=200, cursor="hand2")
            label.pack(anchor="w", padx=5, pady=1)
            label.bind("<Button-1>", lambda e, idx=i: self.copy_top_buyer_text(idx))
            self.top_buyers_labels.append(label)

        stats_frame = tk.Frame(top_buyers_frame, bg="#E6F0FA")
        stats_frame.pack(fill="x", padx=5, pady=5)
        self.stats_text = tk.Text(stats_frame, height=1, width=40, font=("Helvetica", 8), bg="#FFFFFF", cursor="hand2")
        self.stats_text.pack()
        self.stats_text.insert(tk.END, "No stats yet")
        self.stats_text.config(state="disabled")
        self.stats_text.bind("<Button-1>", lambda e: self.copy_stats_text())
        self.sell_rate_text = tk.Text(stats_frame, height=3, width=40, font=("Helvetica", 8), bg="#FFFFFF", cursor="hand2")
        self.sell_rate_text.pack(pady=2)
        self.sell_rate_text.insert(tk.END, "No sell rate yet")
        self.sell_rate_text.config(state="disabled")
        self.sell_rate_text.bind("<Button-1>", lambda e: self.copy_sell_rate_text())

        # Footer
        footer = tk.Frame(root, bg="#1A2526")
        footer.grid(row=2, column=0, columnspan=2, sticky="ew", padx=2, pady=2)
        tk.Label(footer, text=f"License ID: SS-2025-001 | SwiftSale™ © 2025 | Bins: {len(self.bidders)}/{self.max_bins} | Labels: {len(self.label_data)}/{self.max_labels if self.max_labels != float('inf') else 'Unlimited'}", font=("Helvetica", 8), fg="#FFFFFF", bg="#1A2526", name="footer_label").pack(side="left", padx=5)

        # Initialize UI updates
        self.update_footer()
        self.update_top_buyers()
        self.update_stats()
        self.show_avg_sell_rate(show_message=False)

        # Grid weights
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(1, weight=1)
        root.protocol("WM_DELETE_WINDOW", self.on_closing)

        logging.info("Application initialized")

    def on_closing(self):
        logging.info("Closing application")
        self.root.destroy()

    def paste_to_entry(self, entry):
        try:
            clipboard = self.root.clipboard_get()
            cleaned_clipboard = clipboard.strip().strip('()')
            current_content = entry.get().strip()
            if cleaned_clipboard == current_content:
                logging.info(f"Skipped pasting: Clipboard content '{cleaned_clipboard}' matches current field content")
                return
            entry.delete(0, tk.END)
            entry.insert(0, cleaned_clipboard)
            logging.info(f"Pasted clipboard content into username field: raw='{clipboard}', cleaned='{cleaned_clipboard}'")
        except tk.TclError:
            logging.warning("Paste failed: Clipboard empty or inaccessible")

    def validate_license_key(self, key, tier):
        if not key or tier not in self.valid_tiers or tier == 'Trial':
            return False
        try:
            parts = key.split('-')
            if len(parts) != 3 or parts[0].lower() != tier.lower():
                return False
            tier_part, code1, code2 = parts
            checksum = hashlib.sha256((tier_part + code1).encode()).hexdigest()[-4:].upper()
            return checksum == code2
        except:
            return False

    def show_field_info(self, field):
        messagebox.showinfo(f"{field.replace('_', ' ').title()} Info", self.field_info[field])
        logging.info(f"Displayed info for {field}")

    def update_footer(self):
        footer_text = f"License ID: SS-2025-001 | SwiftSale™ © 2025 | Bins: {len(self.bidders)}/{self.max_bins} | Labels: {len(self.label_data)}/{self.max_labels if self.max_labels != float('inf') else 'Unlimited'}"
        try:
            footer_label = self.root.nametowidget(".!frame3.footer_label")
            footer_label.config(text=footer_text)
        except KeyError:
            logging.warning("Footer label not found")
        self.root.update()

    def open_settings(self):
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.geometry("400x300")
        win.configure(bg="#E6F0FA")

        tk.Label(win, text="Telegram Chat ID:", bg="#E6F0FA", font=("Helvetica", 8)).pack(pady=5)
        chat_id_entry = tk.Entry(win, width=40, font=("Helvetica", 8))
        chat_id_entry.insert(0, self.chat_id)
        chat_id_entry.pack(pady=5)

        tk.Label(win, text="Subscription Tier:", bg="#E6F0FA", font=("Helvetica", 8)).pack(pady=5)
        tk.Label(win, text=f"Current: {self.tier} (Max {self.max_bins} bins, {self.max_labels if self.max_labels != float('inf') else 'Unlimited'} labels)", bg="#E6F0FA", font=("Helvetica", 8)).pack(pady=5)
        tier_entry = tk.Entry(win, width=40, font=("Helvetica", 8))
        tier_entry.insert(0, self.tier)
        tier_entry.pack(pady=5)

        tk.Label(win, text="License Key:", bg="#E6F0FA", font=("Helvetica", 8)).pack(pady=5)
        license_entry = tk.Entry(win, width=40, font=("Helvetica", 8))
        license_entry.pack(pady=5)

        tk.Label(win, text="Top Buyer Text:", bg="#E6F0FA", font=("Helvetica", 8)).pack(pady=5)
        top_buyer_entry = tk.Entry(win, width=40, font=("Helvetica", 8))
        top_buyer_entry.insert(0, self.top_buyer_text)
        top_buyer_entry.pack(pady=5)
        tk.Label(win, text="Use {username} and {count} for placeholders", bg="#Eenni6F0FA", font=("Helvetica", 8), fg="#555").pack()

        tk.Label(win, text="Giveaway Announcement Text:", bg="#E6F0FA", font=("Helvetica", 8)).pack(pady=5)
        giveaway_entry = tk.Entry(win, width=40, font=("Helvetica", 8))
        giveaway_entry.insert(0, self.giveaway_announcement_text)
        giveaway_entry.pack(pady=5)
        tk.Label(win, text="Use {number} for giveaway number", bg="#E6F0FA", font=("Helvetica", 8), fg="#555").pack()

        tk.Label(win, text="Flash Sale Announcement Text:", bg="#E6F0FA", font=("Helvetica", 8)).pack(pady=5)
        flash_sale_entry = tk.Entry(win, width=40, font=("Helvetica", 8))
        flash_sale_entry.insert(0, self.flash_sale_announcement_text)
        flash_sale_entry.pack(pady=5)

        def validate_key():
            key = license_entry.get().strip()
            tier = tier_entry.get().strip()
            if not key:
                messagebox.showerror("Error", "License key required!")
                logging.warning("Validate key: Empty license key")
                return
            if tier not in self.valid_tiers:
                messagebox.showerror("Error", f"Invalid tier! Choose from: {', '.join(self.valid_tiers)}")
                logging.warning(f"Invalid tier entered: {tier}")
                return
            if self.validate_license_key(key, tier):
                self.tier = tier
                self.max_bins = self.tier_limits[tier]['bins']
                self.max_labels = self.tier_limits[tier]['labels']
                self.license_key = key
                self.config['Subscription']['tier'] = tier
                self.config['Subscription']['license_key'] = key
                with open(self.config_path, 'w') as f:
                    self.config.write(f)
                self.root.nametowidget(".!frame.!label3").config(text=f"Show ID: {self.show_id} | Tier: {self.tier} (Max {self.max_bins} bins, {self.max_labels if self.max_labels != float('inf') else 'Unlimited'} labels)")
                self.update_footer()
                self.bin_range_entry.delete(0, tk.END)
                self.bin_range_entry.insert(0, f"1-{self.max_bins}")
                messagebox.showinfo("Success", f"License key validated! Tier set to {tier}.")
                logging.info(f"License key validated: Tier set to {tier}")
            else:
                messagebox.showerror("Error", "Invalid license key!")
                logging.warning("Validate key: Invalid license key")

        def save():
            self.chat_id = chat_id_entry.get().strip()
            self.top_buyer_text = top_buyer_entry.get().strip()
            self.giveaway_announcement_text = giveaway_entry.get().strip()
            self.flash_sale_announcement_text = flash_sale_entry.get().strip()
            if not self.chat_id:
                messagebox.showerror("Error", "Chat ID required!")
                logging.warning("Save settings: Empty chat ID")
                return
            self.config['Telegram']['chat_id'] = self.chat_id
            self.config['GUI']['top_buyer_text'] = self.top_buyer_text
            self.config['GUI']['giveaway_announcement_text'] = self.giveaway_announcement_text
            self.config['GUI']['flash_sale_announcement_text'] = self.flash_sale_announcement_text
            with open(self.config_path, 'w') as f:
                self.config.write(f)
            messagebox.showinfo("Success", "Settings saved!")
            logging.info("Settings saved")
            self.update_top_buyers()
            win.destroy()

        tk.Button(win, text="Validate Key", command=validate_key, bg="#3498DB", fg="#FFFFFF", font=("Helvetica", 8)).pack(pady=5)
        tk.Button(win, text="Save Settings", command=save, bg="#2ECC71", fg="#FFFFFF", font=("Helvetica", 8)).pack(pady=5)

    def update_bin_display(self):
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
        self.bin_display.tag_configure("latest", font=("Helvetica", 10, "bold"))
        self.bin_display.see("1.0")
        logging.info("Updated bin display")

    async def send_bin_number(self, username, bin_number):
        if not self.bot or not self.chat_id:
            logging.warning("Send bin number: Bot or chat ID missing")
            return
        try:
            await self.bot.send_message(chat_id=self.chat_id, text=f"Username: {username} | Bin: {bin_number}")
            logging.info(f"Sent Telegram message: Username: {username} | Bin: {bin_number}")
        except Exception as e:
            logging.error(f"Telegram send failed: {str(e)}")
            messagebox.showerror("Telegram Error", f"Failed to send: {str(e)}")

    def copy_sell_rate_text(self):
        text = self.sell_rate_text.get("1.0", tk.END).strip()
        pyperclip.copy(text)
        messagebox.showinfo("Copied", f"Copied: {text}")
        logging.info(f"Copied sell rate text: {text}")

    def show_avg_sell_rate(self, show_message=True):
        if not self.bidders:
            sell_rate_text = "No transactions to analyze."
            self.sell_rate_text.config(state="normal")
            self.sell_rate_text.delete("1.0", tk.END)
            self.sell_rate_text.insert(tk.END, sell_rate_text)
            self.sell_rate_text.config(state="disabled")
            if show_message:
                messagebox.showinfo("Info", sell_rate_text)
            logging.info("Avg sell rate: No transactions")
            return
        
        timestamps = []
        for user, data in self.bidders.items():
            for txn in data['transactions']:
                try:
                    ts = datetime.datetime.strptime(txn['timestamp'], "%Y-%m-%d %I:%M:%S %p")
                    timestamps.append(ts)
                except ValueError:
                    continue
        
        if len(timestamps) < 2:
            sell_rate_text = "Need at least two transactions."
            self.sell_rate_text.config(state="normal")
            self.sell_rate_text.delete("1.0", tk.END)
            self.sell_rate_text.insert(tk.END, sell_rate_text)
            self.sell_rate_text.config(state="disabled")
            if show_message:
                messagebox.showinfo("Info", sell_rate_text)
            logging.info("Avg sell rate: Insufficient transactions")
            return
        
        timestamps.sort()
        time_diffs = [(timestamps[i + 1] - timestamps[i]).total_seconds() for i in range(len(timestamps) - 1)]
        avg_seconds = sum(time_diffs) / len(time_diffs)
        minutes, seconds = divmod(int(avg_seconds), 60)
        time_str = f"{minutes} min {seconds} sec" if minutes > 0 else f"{seconds} sec"
        
        two_hour_seconds = 2 * 3600
        three_hour_seconds = 3 * 3600
        est_two_hour = int(two_hour_seconds / avg_seconds) if avg_seconds > 0 else 0
        est_three_hour = int(three_hour_seconds / avg_seconds) if avg_seconds > 0 else 0
        
        sell_rate_text = (f"SwiftSale™ Your sellers current time per sale is {time_str}. "
                          f"At this sell rate your expected to sell a total of {est_two_hour} items "
                          f"for a 2 hour show or {est_three_hour} items for a 3 hour show.")
        self.sell_rate_text.config(state="normal")
        self.sell_rate_text.delete("1.0", tk.END)
        self.sell_rate_text.insert(tk.END, sell_rate_text)
        self.sell_rate_text.config(state="disabled")
        if show_message:
            messagebox.showinfo("Average Sell Rate", sell_rate_text)
        logging.info(f"Avg sell rate calculated: {sell_rate_text}")

    def copy_top_buyer_text(self, index):
        if index < len(self.top_buyers):
            username, count = self.top_buyers[index]
            text = self.top_buyer_text.format(username=username, count=count)
            pyperclip.copy(text)
            messagebox.showinfo("Copied", f"Copied: {text}")
            logging.info(f"Copied top buyer text: {text}")

    def update_top_buyers(self):
        self.top_buyers = sorted(
            [(data.get('original_username', username), data['total_items']) for username, data in self.bidders.items()],
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

    def copy_stats_text(self):
        text = self.stats_text.get("1.0", tk.END).strip()
        pyperclip.copy(text)
        messagebox.showinfo("Copied", f"Copied: {text}")
        logging.info(f"Copied stats text: {text}")

    def update_stats(self):
        total_items = sum(data['total_items'] for data in self.bidders.values())
        unique_buyers = len(self.bidders)
        avg_items = round(total_items / unique_buyers, 1) if unique_buyers > 0 else 0
        stats_text = f"Sold {total_items} items to {unique_buyers} buyers, avg {avg_items} items each"
        self.stats_text.config(state="normal")
        self.stats_text.delete("1.0", tk.END)
        self.stats_text.insert(tk.END, stats_text)
        self.stats_text.config(state="disabled")
        logging.info("Updated stats display")

    def copy_giveaway_announcement(self):
        text = self.giveaway_announcement_text.format(number=self.giveaway_count + 1)
        pyperclip.copy(text)
        self.announcement_text.config(state="normal")
        self.announcement_text.delete("1.0", tk.END)
        self.announcement_text.insert(tk.END, text)
        self.announcement_text.config(state="disabled")
        messagebox.showinfo("Copied", f"Copied: {text}")
        logging.info(f"Copied giveaway announcement: {text}")

    def copy_flash_sale_announcement(self):
        text = self.flash_sale_announcement_text
        pyperclip.copy(text)
        self.announcement_text.config(state="normal")
        self.announcement_text.delete("1.0", tk.END)
        self.announcement_text.insert(tk.END, text)
        self.announcement_text.config(state="disabled")
        messagebox.showinfo("Copied", f"Copied: {text}")
        logging.info(f"Copied flash sale announcement: {text}")

    def add_bidder(self):
        username = self.username_entry.get().strip().lower()
        original_username = self.username_entry.get().strip()
        qty_str = self.qty_entry.get().strip()
        weight = self.weight_entry.get() or ""
        is_giveaway = self.giveaway_var.get()
        
        logging.info(f"Attempting to add bidder: username='{username}', qty='{qty_str}', weight='{weight}', giveaway={is_giveaway}")
        
        if not username:
            messagebox.showerror("Error", "Username required!")
            logging.warning("Add bidder: Username required")
            self.current_bidder_label.config(text="Error: Username required")
            return
        
        try:
            qty = int(qty_str) if qty_str else 1
            if qty <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Error", "Invalid quantity!")
            logging.warning(f"Add bidder: Invalid quantity '{qty_str}'")
            self.current_bidder_label.config(text="Error: Invalid quantity")
            return
        
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
        
        if username in self.bidders:
            logging.info(f"Updating existing bidder: {username}")
            txn = {
                'bin': self.bidders[username]['bin'],
                'qty': qty,
                'weight': weight,
                'giveaway': is_giveaway,
                'giveaway_num': self.next_giveaway_num if is_giveaway else 0,
                'timestamp': timestamp
            }
            self.bidders[username]['transactions'].append(txn)
            self.bidders[username]['total_items'] += qty
            if is_giveaway:
                self.next_giveaway_num += 1
                self.giveaway_count += 1
        else:
            if self.next_bin > self.max_bins:
                messagebox.showerror("Error", f"Bin limit reached for {self.tier} tier ({self.max_bins} bins). Upgrade your subscription.")
                logging.warning(f"Add bidder: Bin limit reached ({self.max_bins})")
                self.current_bidder_label.config(text=f"Error: Bin Mentioned limit ({self.max_bins}) reached")
                return
            logging.info(f"Adding new bidder: {username}, bin {self.next_bin}")
            txn = {
                'bin': self.next_bin,
                'qty': qty,
                'weight': weight,
                'giveaway': is_giveaway,
                'giveaway_num': self.next_giveaway_num if is_giveaway else 0,
                'timestamp': timestamp
            }
            self.bidders[username] = {
                'bin': self.next_bin,
                'transactions': [txn],
                'total_items': qty,
                'original_username': original_username
            }
            self.next_bin += 1
            if is_giveaway:
                self.next_giveaway_num += 1
                self.giveaway_count += 1
        
        self.last_bidder = username
        self.current_bidder_label.config(text=f"Username: {original_username} | Bin: {txn['bin']}")
        self.latest_bin_assignment = f"Username: {original_username} | Bin: {txn['bin']}"
        self.socketio.emit('update', {'data': self.latest_bin_assignment})
        asyncio.run_coroutine_threadsafe(self.send_bin_number(original_username, txn['bin']), asyncio.get_event_loop())
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
        logging.info(f"Added/updated bidder: {original_username}, Bin: {txn['bin']}, Total bins: {len(self.bidders)}")

    def import_labels(self):
        file_path = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf")])
        if not file_path:
            logging.info("Import labels: No file selected")
            return
        progress_window = tk.Toplevel(self.root)
        progress_window.title("Importing Labels")
        progress_window.geometry("300x100")
        progress_window.configure(bg="#E6F0FA")
        progress_window.transient(self.root)
        progress_window.grab_set()
        tk.Label(progress_window, text="Processing PDF...", bg="#E6F0FA", font=("Helvetica", 10)).pack(pady=5)
        progress_bar = ttk.Progressbar(progress_window, orient="horizontal", length=250, mode="determinate")
        progress_bar.pack(pady=5)
        try:
            self.label_data = []
            with pdfplumber.open(file_path) as pdf:
                total_pages = len(pdf.pages)
                progress_bar["maximum"] = total_pages
                for page_num, page in enumerate(pdf.pages):
                    text = page.extract_text()
                    if not text:
                        try:
                            text = pytesseract.image_to_string(page.to_image().original)
                        except Exception as e:
                            logging.warning(f"OCR failed on page {page_num}: {str(e)}")
                    matches = [m.strip().lower() for m in re.findall(r'\((.*?)\)', text or "")]
                    for username in self.bidders.keys():
                        if username in matches:
                            if len(self.label_data) >= self.max_labels:
                                progress_window.destroy()
                                messagebox.showerror("Error", f"Label limit reached for {self.tier} tier ({self.max_labels} labels). Upgrade your subscription.")
                                logging.warning(f"Import labels: Label limit reached ({self.max_labels})")
                                return
                            self.label_data.append({
                                "bin_number": self.bidders[username]["bin"],
                                "username": username,
                                "pdf_page": page_num,
                                "pdf_path": file_path
                            })
                    progress_bar["value"] = page_num + 1
                    self.root.update()
            progress_window.destroy()
            if self.label_data:
                missing = set(self.bidders.keys()) - {l["username"] for l in self.label_data}
                if missing:
                    missing_display = [self.bidders[username].get('original_username', username) for username in missing]
                    messagebox.showwarning("Warning", f"Missing labels for: {', '.join(missing_display)}")
                    logging.info(f"Imported {len(self.label_data)} labels, missing: {missing_display}")
                else:
                    messagebox.showinfo("Success", f"Imported {len(self.label_data)} labels.")
                    logging.info(f"Imported {len(self.label_data)} labels")
            else:
                messagebox.showwarning("Warning", "No usernames matched in PDF.")
                logging.warning("Import labels: No usernames matched in PDF")
            self.update_footer()
        except Exception as e:
            progress_window.destroy()
            logging.error(f"Import labels failed: {str(e)}")
            messagebox.showerror("Error", f"Import failed: {str(e)}")

    def clear_data(self):
        if messagebox.askyesno("Confirm", "Start new show?"):
            self.save_auction_history()
            self.bidders.clear()
            self.label_data.clear()
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
            if self.bot and self.chat_id:
                asyncio.run_coroutine_threadsafe(
                    self.bot.send_message(self.chat_id, f"New show: {self.show_id}"),
                    asyncio.get_event_loop()
                )
            messagebox.showinfo("Info", f"New show: {self.show_id}")
            self.update_footer()
            logging.info(f"Started new show: {self.show_id}")

    def search_bidders(self):
        q = self.search_entry.get().strip().lower()
        if not q:
            self.search_result.config(text="Enter query!")
            logging.warning("Search bidders: Empty query")
            return
        if q in self.bidders:
            data = self.bidders[q]
            display_username = data.get('original_username', q)
            self.search_result.config(text=f"Username: {display_username} | Bin: {data['bin']} | Items: {data['total_items']}")
            logging.info(f"Search bidders: Found {display_username}")
            return
        res = []
        for username, data in self.bidders.items():
            display_username = data.get('original_username', username)
            for t in data['transactions']:
                if q in username or q in str(t['qty']) or q in t['weight'].lower():
                    res.append(f"{display_username} | Bin {data['bin']} | {t['qty']}x | {t['weight']} | # {t['giveaway_num']} | {t['timestamp']}")
        self.search_result.config(text='\n'.join(res) if res else 'No matches!')
        logging.info(f"Search bidders: {'Found matches' if res else 'No matches'}")

    def update_treeview(self):
        self.tree.delete(*self.tree.get_children())
        for username, data in self.bidders.items():
            if not data['transactions']:
                continue
            last = data['transactions'][-1]
            display_username = data.get('original_username', username)
            parent = self.tree.insert('', 'end', text='+', open=False, values=(
                display_username, data['bin'], last['qty'], last['weight'], 'Yes' if last['giveaway'] else 'No', last['giveaway_num'], last['timestamp']))
            for t in data['transactions'][:-1]:
                self.tree.insert(parent, 'end', values=(
                    '', data['bin'], t['qty'], t['weight'], 'Yes' if t['giveaway'] else 'No', t['giveaway_num'], t['timestamp']))
        def on_click(e):
            region = self.tree.identify_region(e.x, e.y)
            if region != 'tree':
                return
            iid = self.tree.identify_row(e.y)
            if iid:
                self.tree.item(iid, open=not self.tree.item(iid, 'open'))
        self.tree.bind('<ButtonRelease-1>', on_click)
        logging.info("Updated treeview")

    def print_bidders(self):
        if not self.bidders:
            messagebox.showinfo("Info", "No bidders to export.")
            logging.info("Print bidders: No bidders")
            return
        csv_columns = ["username", "bin", "qty", "weight", "giveaway", "giveaway_num", "timestamp"]
        try:
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
            logging.error(f"Bidders export failed: {str(e)}")
            messagebox.showerror("Error", f"Export failed: {str(e)}")

    def save_auction_history(self):
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
            logging.error(f"Auction history save failed: {str(e)}")
            messagebox.showerror("Error", f"Save failed: {str(e)}")

    def print_labels(self):
        if not self.label_data:
            messagebox.showwarning("Warning", "No labels imported.")
            logging.warning("Print labels: No labels")
            return
        try:
            bin_range = self.bin_range_entry.get().strip() or f"1-{self.max_bins}"
            start_bin, end_bin = map(int, bin_range.split("-"))
            if end_bin > self.max_bins or start_bin < 1:
                messagebox.showerror("Error", f"Bin range must be between 1 and {self.max_bins}.")
                logging.warning(f"Print labels: Invalid bin range {bin_range}")
                return
            labels = [l for l in sorted(self.label_data, key=lambda x: x["bin_number"]) if start_bin <= l["bin_number"] <= end_bin]
            if not labels:
                messagebox.showwarning("Warning", f"No labels in range {start_bin}-{end_bin}.")
                logging.warning(f"Print labels: No labels in range {start_bin}-{end_bin}")
                return
            try:
                text_x = float(self.text_x_entry.get() or 50)
                text_y = float(self.text_y_entry.get() or 50)
            except ValueError:
                messagebox.showerror("Error", "Text X and Text Y must be numeric.")
                logging.error("Print labels: Invalid text coordinates")
                return
            printed_count = 0
            for label in labels:
                try:
                    doc = fitz.open(label["pdf_path"])
                    page = doc[label["pdf_page"]]
                    label_text = self.label_text_entry.get().format(bin_number=label["bin_number"])
                    page.insert_text((text_x, text_y), label_text, fontsize=12, fontname="helv", color=(0, 0, 0))
                    temp_doc = fitz.open()
                    temp_doc.insert_pdf(doc, from_page=label["pdf_page"], to_page=label["pdf_page"])
                    temp_pdf = os.path.join(log_dir, f"temp_label_bin_{label['bin_number']}.pdf")
                    os.makedirs(os.path.dirname(temp_pdf), exist_ok=True)
                    temp_doc.save(temp_pdf)
                    temp_doc.close()
                    doc.close()
                    try:
                        if os.name == "nt":
                            os.startfile(temp_pdf, "print")
                        else:
                            subprocess.run(["lp", temp_pdf], check=True)
                        printed_count += 1
                    except Exception as e:
                        logging.error(f"Failed to print label for bin {label['bin_number']}: {str(e)}")
                    finally:
                        try:
                            os.remove(temp_pdf)
                        except:
                            pass
                except Exception as e:
                    logging.error(f"Failed to process label for bin {label['bin_number']}: {str(e)}")
                    continue
            messagebox.showinfo("Success", f"Printed {printed_count} labels for bins {start_bin}-{end_bin}.")
            logging.info(f"Printed {printed_count} labels for bins {start_bin}-{end_bin}")
        except ValueError:
            messagebox.showerror("Error", "Invalid bin range format. Use 'start-end'.")
            logging.error("Print labels: Invalid bin range format")
        except Exception as e:
            logging.error(f"Print labels failed: {str(e)}")
            messagebox.showerror("Error", f"Print failed: {str(e)}")

    def test_print_label(self):
        if not self.label_data:
            messagebox.showwarning("Warning", "No labels imported.")
            logging.warning("Test print: No labels")
            return
        try:
            label = sorted(self.label_data, key=lambda x: x["bin_number"])[0]
            doc = fitz.open(label["pdf_path"])
            page = doc[label["pdf_page"]]
            try:
                text_x = float(self.text_x_entry.get() or 50)
                text_y = float(self.text_y_entry.get() or 50)
            except ValueError:
                messagebox.showerror("Error", "Text X and Text Y must be numeric.")
                logging.error("Test print: Invalid text coordinates")
                return
            label_text = self.label_text_entry.get().format(bin_number=label["bin_number"])
            page.insert_text((text_x, text_y), label_text, fontsize=12, fontname="helv", color=(0, 0, 0))
            temp_doc = fitz.open()
            temp_doc.insert_pdf(doc, from_page=label["pdf_page"], to_page=label["pdf_page"])
            temp_pdf = os.path.join(log_dir, "test_label.pdf")
            os.makedirs(os.path.dirname(temp_pdf), exist_ok=True)
            temp_doc.save(temp_pdf)
            temp_doc.close()
            doc.close()
            try:
                if os.name == "nt":
                    os.startfile(temp_pdf, "print")
                else:
                    subprocess.run(["lp", temp_pdf], check=True)
                messagebox.showinfo("Success", f"Test label printed for bin {label['bin_number']}.")
                logging.info(f"Test label printed for bin {label['bin_number']}")
            finally:
                os.remove(temp_pdf)
        except Exception as e:
            logging.error(f"Test print failed: {str(e)}")
            messagebox.showerror("Error", f"Test print failed: {str(e)}")

    def preview_labels(self):
        if not self.label_data:
            messagebox.showwarning("Warning", "No labels to preview.")
            logging.warning("Preview labels: No labels")
            return
        try:
            label = sorted(self.label_data, key=lambda x: x["bin_number"])[0]
            doc = fitz.open(label["pdf_path"])
            page = doc[label["pdf_page"]]
            try:
                text_x = float(self.text_x_entry.get() or 50)
                text_y = float(self.text_y_entry.get() or 50)
            except ValueError:
                messagebox.showerror("Error", "Text X and Text Y must be numeric.")
                logging.error("Preview labels: Invalid text coordinates")
                return
            label_text = self.label_text_entry.get().format(bin_number=label["bin_number"])
            page.insert_text((text_x, text_y), label_text, fontsize=12, fontname="helv", color=(0, 0, 0))
            pix = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5))
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            photo = ImageTk.PhotoImage(img)
            win = tk.Toplevel(self.root)
            win.title("Label Preview")
            win.geometry("300x400")
            canvas = tk.Canvas(win, width=300, height=400, bg="white")
            canvas.pack()
            canvas.create_image(0, 0, image=photo, anchor="nw")
            canvas.image = photo
            doc.close()
            logging.info(f"Previewed label for bin {label['bin_number']}")
        except Exception as e:
            logging.error(f"Label preview failed: {str(e)}")
            messagebox.showerror("Error", f"Preview failed: {str(e)}")

    def export_labels(self):
        if not self.label_data:
            messagebox.showwarning("Warning", "No labels to export.")
            logging.warning("Export labels: No labels")
            return
        try:
            csv_path = os.path.join(log_dir, f"labels_{self.show_id}.csv")
            os.makedirs(os.path.dirname(csv_path), exist_ok=True)
            with open(csv_path, mode="w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["username", "bin_number", "pdf_page"])
                writer.writeheader()
                for label in sorted(self.label_data, key=lambda x: x["bin_number"]):
                    username = label["username"]
                    display_username = self.bidders.get(username, {}).get('original_username', username)
                    writer.writerow({
                        "username": display_username,
                        "bin_number": label["bin_number"],
                        "pdf_page": label["pdf_page"]
                    })
            messagebox.showinfo("Success", f"Exported to {csv_path}")
            logging.info(f"Labels exported to {csv_path}")
        except Exception as e:
            logging.error(f"Label export failed: {str(e)}")
            messagebox.showerror("Error", f"Export failed: {str(e)}")

    def show_printer_setup(self):
        messagebox.showinfo("Printer Setup", """
        Printer Setup Guide:
        1. Use Chrome for Whatnot labels.
        2. Enable pop-ups in Chrome.
        3. Thermal printers (4x6): Set 4x6 paper, portrait.
        4. Standard printers (8.5x11): Set Letter, enable 'Fit to Page'.
        5. Check orientation if labels print sideways.
        6. Test print before bulk printing.""")
        logging.info("Displayed printer setup guide")

if __name__ == '__main__':
    root = tk.Tk()
    app = SCDWhatnotGUI(root)
    root.mainloop()