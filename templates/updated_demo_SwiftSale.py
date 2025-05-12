import tkinter as tk
from tkinter import messagebox, ttk, filedialog
from PIL import Image, ImageTk
import os, sys, datetime, csv, hashlib
import pdfplumber, fitz, subprocess, pytesseract, re
from telegram import Bot
import asyncio, configparser
import warnings
import platform
import shutil
import threading
from flask import Flask, render_template
from flask_socketio import SocketIO

# Social media import
try:
    import tweepy
except ImportError:
    tweepy = None
    print("Warning: tweepy not installed. X posting will be disabled.")

# Suppress urllib3 warning from python-telegram-bot
warnings.filterwarnings("ignore", category=UserWarning, module="telegram")

# Tesseract path setup (Fixed for cross-platform compatibility)
def setup_tesseract(config):
    """
    Configure Tesseract-OCR path for bundled executable or local environment.
    Uses config.ini path or defaults to Windows standard path.
    """
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller bundled executable
        tesseract_path = os.path.join(sys._MEIPASS, 'tesseract', 'tesseract.exe')
    else:
        # Read from config.ini or set platform-specific default
        tesseract_path = config['Tesseract'].get('path', '')
        if not tesseract_path:
            if platform.system() == 'Windows':
                tesseract_path = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
            else:
                tesseract_path = shutil.which('tesseract') or '/usr/bin/tesseract'

    # Verify Tesseract path
    if not os.path.exists(tesseract_path) and not shutil.which(tesseract_path):
        raise RuntimeError(
            f"Tesseract not found at {tesseract_path}. "
            "Please install Tesseract-OCR and set the correct path in Settings."
        )
    pytesseract.pytesseract.tesseract_cmd = tesseract_path

# Load config for Tesseract path
config = configparser.ConfigParser()
config_path = os.path.join(os.path.expanduser("~"), ".swiftsale", "config.ini")
os.makedirs(os.path.dirname(config_path), exist_ok=True)
if not os.path.exists(config_path):
    config['Tesseract'] = {'path': ''}
    config['Telegram'] = {'bot_token': '', 'chat_id': ''}
    config['Subscription'] = {'tier': 'Gold', 'license_key': ''}
    config['SocialMedia'] = {
        'x_api_key': '',
        'x_api_secret': '',
        'x_access_token': '',
        'x_access_token_secret': ''
    }
    with open(config_path, 'w') as configfile:
        config.write(configfile)
config.read(config_path)

# Ensure all sections exist
for section in ['Tesseract', 'Telegram', 'Subscription', 'SocialMedia']:
    if section not in config:
        config[section] = {}
        if section == 'Tesseract':
            config[section]['path'] = ''
        elif section == 'Telegram':
            config[section]['bot_token'] = ''
            config[section]['chat_id'] = ''
        elif section == 'Subscription':
            config[section]['tier'] = 'Gold'
            config[section]['license_key'] = ''
        elif section == 'SocialMedia':
            config[section]['x_api_key'] = ''
            config[section]['x_api_secret'] = ''
            config[section]['x_access_token'] = ''
            config[section]['x_access_token_secret'] = ''
        with open(config_path, 'w') as configfile:
            config.write(configfile)

try:
    setup_tesseract(config)
except RuntimeError as e:
    tk.Tk().withdraw()  # Hide main window
    messagebox.showerror("Tesseract Error", str(e))
    sys.exit(1)

class SCDWhatnotGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("SwiftSale™– Demo Mode")
        self.root.geometry("800x600")
        self.root.configure(bg="#E6F0FA")

        # Initialize asyncio event loop (Fixed for Tkinter integration)
        self.loop = asyncio.new_event_loop()
        self.running = True
        self.schedule_asyncio()

        self.bidders = {}
        self.next_bin = 1
        self.next_giveaway_num = 1
        self.label_data = []
        self.last_bidder = None
        self.show_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.show_start_time = datetime.datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
        self.bidder_csv_path = os.path.join(os.path.expanduser("~"), "Documents", "SwiftSale", f"bidder_history_{self.show_id}.csv")
        os.makedirs(os.path.dirname(self.bidder_csv_path), exist_ok=True)

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
        self.config_path = config_path
        self.config.read(self.config_path)

        self.bot_token = self.config['Telegram'].get('bot_token', '')
        self.chat_id = self.config['Telegram'].get('chat_id', '')
        self.tier = self.config['Subscription'].get('tier', 'Gold')  # Use config tier
        self.max_bins = self.tier_limits[self.tier]['bins']
        self.max_labels = self.tier_limits[self.tier]['labels']
        self.license_key = self.config['Subscription'].get('license_key', '')

        # Initialize social media (X) credentials
        self.x_api_key = self.config['SocialMedia'].get('x_api_key', '')
        self.x_api_secret = self.config['SocialMedia'].get('x_api_secret', '')
        self.x_access_token = self.config['SocialMedia'].get('x_access_token', '')
        self.x_access_token_secret = self.config['SocialMedia'].get('x_access_token_secret', '')
        try:
            self.x_client = tweepy.Client(
                consumer_key=self.x_api_key,
                consumer_secret=self.x_api_secret,
                access_token=self.x_access_token,
                access_token_secret=self.x_access_token_secret
            ) if tweepy and all([self.x_api_key, self.x_api_secret, self.x_access_token, self.x_access_token_secret]) else None
        except Exception as e:
            print(f"Warning: X client initialization failed: {e}")
            self.x_client = None

        if not self.bot_token:
            messagebox.showwarning("Warning", "Telegram bot token missing in config.ini. Set in Settings to enable messaging.")
        self.bot = Bot(token=self.bot_token) if self.bot_token else None

        # Initialize Flask app for secondary display
        self.flask_app = Flask(__name__)
        self.socketio = SocketIO(self.flask_app, async_mode='eventlet')
        self.latest_bin_assignment = "Waiting for bidder..."

        # Define Flask routes
        @self.flask_app.route('/')
        def index():
            return render_template('index.html')

        @self.socketio.on('connect')
        def handle_connect():
            self.socketio.emit('update', {'data': self.latest_bin_assignment})

        # Start Flask server in a separate thread
        def run_flask():
            self.socketio.run(self.flask_app, host='0.0.0.0', port=5000)

        flask_thread = threading.Thread(target=run_flask, daemon=True)
        flask_thread.start()

        self.field_info = {
            "search": "Search for bidder by username or partial matches for transactions.",
            "username": "Enter Whatnot username to assign bin number.",
            "quantity": "Number of items won (default: 1).",
            "weight_class": "Select weight class or 'PU Only' (optional).",
            "giveaway": "Mark as giveaway to assign unique number.",
            "label_text": "Custom label text (e.g., 'Bin: {bin_number}').",
            "text_x": "X-coordinate for label text (pixels).",
            "text_y": "Y-coordinate for label text (pixels).",
            "bin_range": "Bin range for printing labels (e.g., '1-25').",
            "tesseract_path": "Path to Tesseract-OCR executable (e.g., C:\\Program Files\\Tesseract-OCR\\tesseract.exe).",
            "avg_sell_rate": "Calculate and display the average time between item sales based on transaction timestamps.",
            "promote": "Enter text to promote your Whatnot livestream on X (e.g., date, time, URL)."
        }

        # Header Frame
        header = tk.Frame(root, bg="#1A2526")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=2, pady=2)
        logo_path = os.path.join(os.path.dirname(__file__), "SwiftSale.png")
        try:
            logo_img = Image.open(logo_path).resize((75, 60), Image.LANCZOS)
            self.logo = ImageTk.PhotoImage(logo_img)
            tk.Label(header, image=self.logo, bg="#1A2526").pack(side="left", padx=5)
            tk.Label(header, text="SwiftSale™", font=("Helvetica", 12, "bold"), fg="#FFFFFF", bg="#1A2526").pack(side="left")
        except Exception as e:
            print(f"Warning: Failed to load logo: {e}")
            tk.Label(header, text="SwiftSale™", font=("Helvetica", 12, "bold"), fg="#FFFFFF", bg="#1A2526").pack(side="left", padx=5)
        tk.Label(header, text=f"Show ID: {self.show_id} | Tier: {self.tier} (Bins: {self.max_bins}, Labels: {'Unlimited' if self.max_labels == float('inf') else self.max_labels})", font=("Helvetica", 8), fg="#FFFFFF", bg="#1A2526", name="header_label").pack(side="right", padx=5)

        # Main Frame
        main = tk.Frame(root, bg="#E6F0FA")
        main.grid(row=1, column=0, columnspan=2, padx=5, pady=5, sticky="nsew")

        # Input Frame
        input_frame = tk.Frame(main, bg="#E6F0FA")
        input_frame.pack(fill="x", padx=5, pady=2)

        # Search and Settings
        tk.Label(input_frame, text="Search:", bg="#E6F0FA", font=("Helvetica", 8)).grid(row=0, column=0, padx=2, sticky="e")
        self.search_entry = tk.Entry(input_frame, width=15, font=("Helvetica", 8))
        self.search_entry.grid(row=0, column=1, padx=2)
        tk.Button(input_frame, text="?", command=lambda: self.show_field_info("search"), width=2, font=("Helvetica", 8)).grid(row=0, column=2)
        tk.Button(input_frame, text="Search", command=self.search_bidders, bg="#E63946", fg="#FFFFFF", font=("Helvetica", 8)).grid(row=0, column=3, padx=2)
        tk.Button(input_frame, text="Settings", command=self.open_settings, bg="#FFA500", fg="#FFFFFF", font=("Helvetica", 8)).grid(row=0, column=4, padx=2)

        # Transaction Inputs
        tk.Label(input_frame, text="Username:", bg="#E6F0FA", font=("Helvetica", 8)).grid(row=1, column=0, padx=2, sticky="e")
        self.username_entry = tk.Entry(input_frame, width=15, font=("Helvetica", 8))
        self.username_entry.grid(row=1, column=1, padx=2)
        tk.Button(input_frame, text="?", command=lambda: self.show_field_info("username"), width=2, font=("Helvetica", 8)).grid(row=1, column=2)
        self.current_bidder_label = tk.Label(input_frame, text="", font=("Helvetica", 10, "bold"), bg="#E6F0FA")
        self.current_bidder_label.grid(row=1, column=3, columnspan=2, padx=2, sticky="w")

        tk.Label(input_frame, text="Quantity:", bg="#E6F0FA", font=("Helvetica", 8)).grid(row=2, column=0, padx=2, sticky="e")
        self.qty_entry = tk.Entry(input_frame, width=15, font=("Helvetica", 8))
        self.qty_entry.insert(0, "1")
        self.qty_entry.grid(row=2, column=1, padx=2)
        tk.Button(input_frame, text="?", command=lambda: self.show_field_info("quantity"), width=2, font=("Helvetica", 8)).grid(row=2, column=2)

        tk.Label(input_frame, text="Weight:", bg="#E6F0FA", font=("Helvetica", 8)).grid(row=2, column=3, padx=2, sticky="e")
        self.weight_entry = ttk.Combobox(input_frame, values=["A", "B", "C", "D", "E", "F", "G", "H", "PU Only"], width=10, state="readonly", font=("Helvetica", 8))
        self.weight_entry.grid(row=2, column=4, padx=2)
        tk.Button(input_frame, text="?", command=lambda: self.show_field_info("weight_class"), width=2, font=("Helvetica", 8)).grid(row=2, column=5)
        tk.Button(input_frame, text="Avg Sell Rate", command=self.show_avg_sell_rate, bg="#4682B4", fg="#FFFFFF", font=("Helvetica", 8)).grid(row=2, column=6, padx=2)

        self.giveaway_var = tk.BooleanVar()
        tk.Checkbutton(input_frame, text="Giveaway", variable=self.giveaway_var, bg="#E6F0FA", font=("Helvetica", 8)).grid(row=3, column=1, padx=2, sticky="w")
        tk.Button(input_frame, text="?", command=lambda: self.show_field_info("giveaway"), width=2, font=("Helvetica", 8)).grid(row=3, column=2)

        tk.Label(input_frame, text="Label Text:", bg="#E6F0FA", font=("Helvetica", 8)).grid(row=4, column=0, padx=2, sticky="e")
        self.label_text_entry = tk.Entry(input_frame, width=15, font=("Helvetica", 8))
        self.label_text_entry.insert(0, "Bin: {bin_number}")
        self.label_text_entry.grid(row=4, column=1, padx=2)
        tk.Button(input_frame, text="?", command=lambda: self.show_field_info("label_text"), width=2, font=("Helvetica", 8)).grid(row=4, column=2)

        tk.Label(input_frame, text="Text X:", bg="#E6F0FA", font=("Helvetica", 8)).grid(row=4, column=3, padx=2, sticky="e")
        self.text_x_entry = tk.Entry(input_frame, width=10, font=("Helvetica", 8))
        self.text_x_entry.insert(0, "50")
        self.text_x_entry.grid(row=4, column=4, padx=2)
        tk.Button(input_frame, text="?", command=lambda: self.show_field_info("text_x"), width=2, font=("Helvetica", 8)).grid(row=4, column=5)

        tk.Label(input_frame, text="Text Y:", bg="#E6F0FA", font=("Helvetica", 8)).grid(row=5, column=3, padx=2, sticky="e")
        self.text_y_entry = tk.Entry(input_frame, width=10, font=("Helvetica", 8))
        self.text_y_entry.insert(0, "50")
        self.text_y_entry.grid(row=5, column=4, padx=2)
        tk.Button(input_frame, text="?", command=lambda: self.show_field_info("text_y"), width=2, font=("Helvetica", 8)).grid(row=5, column=5)

        tk.Label(input_frame, text="Bin Range:", bg="#E6F0FA", font=("Helvetica", 8)).grid(row=5, column=0, padx=2, sticky="e")
        self.bin_range_entry = tk.Entry(input_frame, width=15, font=("Helvetica", 8))
        self.bin_range_entry.insert(0, f"1-{self.max_bins}")
        self.bin_range_entry.grid(row=5, column=1, padx=2)
        tk.Button(input_frame, text="?", command=lambda: self.show_field_info("bin_range"), width=2, font=("Helvetica", 8)).grid(row=5, column=2)

        # Social Media Promotion
        tk.Label(input_frame, text="Promote Show:", bg="#E6F0FA", font=("Helvetica", 8)).grid(row=6, column=0, padx=2, sticky="e")
        self.promote_entry = tk.Text(input_frame, height=3, width=30, font=("Helvetica", 8))
        self.promote_entry.grid(row=6, column=1, columnspan=3, padx=2)
        self.promote_entry.insert("1.0", f"Join my Whatnot livestream at {datetime.datetime.now().strftime('%I:%M %p')} EST! [Your URL] #Whatnot")
        tk.Button(input_frame, text="?", command=lambda: self.show_field_info("promote"), width=2, font=("Helvetica", 8)).grid(row=6, column=4)
        # Disable for Trial tier
        if self.tier == 'Trial':
            self.promote_entry.config(state='disabled')
            tk.Button(input_frame, text="Post to X", command=self.post_promote, bg="#1DA1F2", fg="#FFFFFF", font=("Helvetica", 8), state='disabled').grid(row=6, column=5, padx=2)
        else:
            tk.Button(input_frame, text="Post to X", command=self.post_promote, bg="#1DA1F2", fg="#FFFFFF", font=("Helvetica", 8)).grid(row=6, column=5, padx=2)

        # Button Frame
        btn_frame = tk.Frame(main, bg="#E6F0FA")
        btn_frame.pack(fill="x", padx=5, pady=2)
        buttons = [
            ("Add Bidder", self.add_bidder, "#2ECC71"),
            ("Print Bidders", self.print_bidders, "#E63946"),
            ("Import Labels", self.import_labels, "#3498DB"),
            ("Print Labels", self.print_labels, "#9B59B6"),
            ("Test Print", self.test_print_label, "#FF5733"),
            ("Preview Labels", self.preview_labels, "#00CED1"),
            ("Export Labels", self.export_labels, "#228B22"),
            ("Clear Data", self.clear_data, "#A8D5E2"),
            ("Printer Guide", self.show_printer_setup, "#F1C40F")
        ]
        for i, (text, cmd, bg) in enumerate(buttons):
            tk.Button(btn_frame, text=text, command=cmd, bg=bg, fg="#FFFFFF", width=12, font=("Helvetica", 8)).grid(row=0, column=i, padx=2)

        # Search Result
        self.search_result = tk.Label(main, text="", wraplength=700, bg="#E6F0FA", font=("Helvetica", 8))
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
        self.bin_display = tk.Text(main, height=4, width=80, font=("Helvetica", 10), bg="#FFFFFF")
        self.bin_display.pack(fill="x", padx=5, pady=2)
        self.bin_display.config(state="disabled")

        # Footer
        footer = tk.Frame(root, bg="#1A2526")
        footer.grid(row=2, column=0, columnspan=2, sticky="ew", padx=2, pady=2)
        tk.Label(footer, text=f"License ID: SS-2025-001 | SwiftSale™ © 2025 | Bins: {len(self.bidders)}/{self.max_bins} | Labels: {len(self.label_data)}/{'Unlimited' if self.max_labels == float('inf') else self.max_labels}", font=("Helvetica", 8), fg="#FFFFFF", bg="#1A2526", name="footer_label").pack(side="left", padx=5)

        # Initialize footer update
        self.update_footer()

        # Grid weights
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(1, weight=1)

        # Bind window close to clean up asyncio loop
        root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def on_closing(self):
        """Clean up asyncio loop on window close."""
        self.running = False
        self.loop.call_soon_threadsafe(self.loop.stop)
        try:
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
        finally:
            self.loop.close()
        self.root.destroy()

    def schedule_asyncio(self):
        """Run asyncio tasks in Tkinter's main loop."""
        if not self.running:
            return
        try:
            self.loop.run_until_complete(asyncio.sleep(0))
        except RuntimeError:
            pass  # Ignore if loop is stopped
        self.root.after(10, self.schedule_asyncio)

    async def run_coroutine(self, coro):
        """Schedule and run an asyncio coroutine."""
        try:
            return await coro
        except Exception as e:
            messagebox.showerror("Async Error", f"Async operation failed: {str(e)}")

    def validate_license_key(self, key, tier):
        """Validate license key (demo mode returns True for Gold)."""
        return True if tier == 'Gold' else False

    def show_field_info(self, field):
        """Show information about a field via messagebox."""
        messagebox.showinfo(f"{field.capitalize()} Info", self.field_info[field])

    def update_footer(self):
        """Update footer with current bin and label counts."""
        footer_text = f"License ID: SS-2025-001 | SwiftSale™ © 2025 | Bins: {len(self.bidders)}/{self.max_bins} | Labels: {len(self.label_data)}/{'Unlimited' if self.max_labels == float('inf') else self.max_labels}"
        try:
            footer_label = self.root.nametowidget(".!frame3.footer_label")
            footer_label.config(text=footer_text)
        except KeyError:
            print("Warning: Footer label not found. Skipping footer update.")
        self.root.update()

    def open_settings(self):
        """Open settings window to configure Telegram, Tesseract, and X credentials."""
        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.geometry("400x400")
        win.configure(bg="#E6F0FA")

        tk.Label(win, text="Telegram Chat ID:", bg="#E6F0FA", font=("Helvetica", 8)).pack(pady=5)
        chat_id_entry = tk.Entry(win, width=40, font=("Helvetica", 8))
        chat_id_entry.insert(0, self.chat_id)
        chat_id_entry.pack(pady=5)

        tk.Label(win, text="Tesseract Path:", bg="#E6F0FA", font=("Helvetica", 8)).pack(pady=5)
        tesseract_entry = tk.Entry(win, width=40, font=("Helvetica", 8))
        tesseract_entry.insert(0, self.config['Tesseract'].get('path', pytesseract.pytesseract.tesseract_cmd))
        tesseract_entry.pack(pady=5)
        tk.Button(win, text="?", command=lambda: self.show_field_info("tesseract_path"), width=2, font=("Helvetica", 8)).pack()

        tk.Label(win, text="Subscription Tier:", bg="#E6F0FA", font=("Helvetica", 8)).pack(pady=5)
        tier_entry = tk.Entry(win, width=40, font=("Helvetica", 8))
        tier_entry.insert(0, self.tier)
        tier_entry.pack(pady=5)

        tk.Label(win, text="X API Key:", bg="#E6F0FA", font=("Helvetica", 8)).pack(pady=5)
        x_api_key_entry = tk.Entry(win, width=40, font=("Helvetica", 8))
        x_api_key_entry.insert(0, self.x_api_key)
        x_api_key_entry.pack(pady=5)

        tk.Label(win, text="X API Secret:", bg="#E6F0FA", font=("Helvetica", 8)).pack(pady=5)
        x_api_secret_entry = tk.Entry(win, width=40, font=("Helvetica", 8))
        x_api_secret_entry.insert(0, self.x_api_secret)
        x_api_secret_entry.pack(pady=5)

        tk.Label(win, text="X Access Token:", bg="#E6F0FA", font=("Helvetica", 8)).pack(pady=5)
        x_access_token_entry = tk.Entry(win, width=40, font=("Helvetica", 8))
        x_access_token_entry.insert(0, self.x_access_token)
        x_access_token_entry.pack(pady=5)

        tk.Label(win, text="X Access Token Secret:", bg="#E6F0FA", font=("Helvetica", 8)).pack(pady=5)
        x_access_token_secret_entry = tk.Entry(win, width=40, font=("Helvetica", 8))
        x_access_token_secret_entry.insert(0, self.x_access_token_secret)
        x_access_token_secret_entry.pack(pady=5)

        def save():
            self.chat_id = chat_id_entry.get().strip()
            tesseract_path = tesseract_entry.get().strip()
            self.tier = tier_entry.get().strip()
            self.x_api_key = x_api_key_entry.get().strip()
            self.x_api_secret = x_api_secret_entry.get().strip()
            self.x_access_token = x_access_token_entry.get().strip()
            self.x_access_token_secret = x_access_token_secret_entry.get().strip()

            if self.tier not in self.valid_tiers:
                messagebox.showerror("Error", f"Invalid tier! Choose from: {', '.join(self.valid_tiers)}")
                return

            if tesseract_path and (os.path.exists(tesseract_path) or shutil.which(tesseract_path)):
                pytesseract.pytesseract.tesseract_cmd = tesseract_path
            else:
                messagebox.showerror("Error", "Invalid Tesseract path!")
                return

            self.config['Telegram']['chat_id'] = self.chat_id
            self.config['Tesseract']['path'] = tesseract_path
            self.config['Subscription']['tier'] = self.tier
            self.config['SocialMedia']['x_api_key'] = self.x_api_key
            self.config['SocialMedia']['x_api_secret'] = self.x_api_secret
            self.config['SocialMedia']['x_access_token'] = self.x_access_token
            self.config['SocialMedia']['x_access_token_secret'] = self.x_access_token_secret

            try:
                with open(self.config_path, 'w') as f:
                    self.config.write(f)
                # Reinitialize X client
                self.x_client = tweepy.Client(
                    consumer_key=self.x_api_key,
                    consumer_secret=self.x_api_secret,
                    access_token=self.x_access_token,
                    access_token_secret=self.x_access_token_secret
                ) if tweepy and all([self.x_api_key, self.x_api_secret, self.x_access_token, self.x_access_token_secret]) else None
                self.max_bins = self.tier_limits[self.tier]['bins']
                self.max_labels = self.tier_limits[self.tier]['labels']
                messagebox.showinfo("Success", "Settings saved!")
                win.destroy()
                # Refresh GUI to reflect tier change
                self.root.nametowidget(".!frame.header_label").config(
                    text=f"Show ID: {self.show_id} | Tier: {self.tier} (Bins: {self.max_bins}, Labels: {'Unlimited' if self.max_labels == float('inf') else self.max_labels})"
                )
                self.update_footer()
            except Exception as e:
                messagebox.showerror("Error", f"Save failed: {str(e)}")

        tk.Button(win, text="Save Settings", command=save, bg="#2ECC71", fg="#FFFFFF", font=("Helvetica", 8)).pack(pady=5)
        tk.Label(win, text="Get X credentials at https://developer.x.com", bg="#E6F0FA", font=("Helvetica", 8)).pack(pady=5)

    def update_bin_display(self):
        """Update the bin display with the latest bidder information."""
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

    async def send_bin_number(self, username, bin_number):
        """Send bin number to Telegram chat asynchronously."""
        if not self.bot or not self.chat_id:
            return
        try:
            original_username = self.bidders.get(username, {}).get('original_username', username)
            await self.bot.send_message(chat_id=self.chat_id, text=f"Username: {original_username} | Bin: {bin_number}")
        except Exception as e:
            messagebox.showerror("Telegram Error", f"Failed to send: {str(e)}")

    async def post_to_x(self, message):
        """Post a message to X asynchronously."""
        if not self.x_client:
            messagebox.showerror("Error", "X credentials missing or tweepy not installed. Set in Settings.")
            return
        try:
            self.x_client.create_tweet(text=message)
            messagebox.showinfo("Success", f"Posted to X: {message}")
        except tweepy.errors.Unauthorized as e:
            messagebox.showerror("X Error", "Authentication failed: Invalid credentials. Please check X API settings.")
        except tweepy.errors.TooManyRequests as e:
            messagebox.showerror("X Error", "Rate limit exceeded: Too many posts. Wait and try again later.")
        except Exception as e:
            messagebox.showerror("X Error", f"Failed to post: {str(e)}")

    def post_promote(self):
        """Post promotion message to X."""
        if self.tier == 'Trial':
            messagebox.showerror("Error", "X posting is not available for Trial tier. Upgrade to Bronze, Silver, or Gold.")
            return
        message = self.promote_entry.get("1.0", tk.END).strip()
        if not message:
            messagebox.showerror("Error", "Promotion text required!")
            return
        if len(message) > 280:
            messagebox.showerror("Error", "Message exceeds 280 characters!")
            return
        # Log post to track API usage
        log_path = os.path.join(os.path.expanduser("~"), "Documents", "SwiftSale", "post_log.csv")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, 'a', newline='') as f:
            csv.writer(f).writerow([self.tier, datetime.datetime.now(), message])
        asyncio.create_task(self.run_coroutine(self.post_to_x(message)))

    def add_bidder(self):
        """Add a bidder with username, quantity, weight, and optional giveaway."""
        original_username = self.username_entry.get().strip()
        username = original_username.lower()
        qty_str = self.qty_entry.get().strip()
        weight = self.weight_entry.get() or ""
        is_giveaway = self.giveaway_var.get()
        if not username:
            messagebox.showerror("Error", "Username required!")
            return
        try:
            qty = int(qty_str) if qty_str else 1
            if qty <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Error", "Invalid quantity!")
            return
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
        if username in self.bidders:
            txn = {'bin': self.bidders[username]['bin'], 'qty': qty, 'weight': weight, 'giveaway': is_giveaway,
                   'giveaway_num': self.next_giveaway_num if is_giveaway else 0, 'timestamp': timestamp}
            self.bidders[username]['transactions'].append(txn)
            self.bidders[username]['total_items'] += qty
            if is_giveaway:
                self.next_giveaway_num += 1
        else:
            if self.next_bin > self.max_bins:
                messagebox.showerror("Error", f"Bin limit reached ({self.max_bins} bins).")
                return
            txn = {'bin': self.next_bin, 'qty': qty, 'weight': weight, 'giveaway': is_giveaway,
                   'giveaway_num': self.next_giveaway_num if is_giveaway else 0, 'timestamp': timestamp}
            self.bidders[username] = {
                'bin': self.next_bin,
                'transactions': [txn],
                'total_items': qty,
                'original_username': original_username
            }
            self.next_bin += 1
            if is_giveaway:
                self.next_giveaway_num += 1
        self.last_bidder = username
        self.current_bidder_label.config(text=f"Username: {original_username} | Bin: {txn['bin']}")
        self.latest_bin_assignment = f"{original_username} | Bin: {txn['bin']}"
        self.socketio.emit('update', {'data': self.latest_bin_assignment})
        if self.bot and self.chat_id:
            asyncio.create_task(self.run_coroutine(self.send_bin_number(username, txn['bin'])))
        self.username_entry.delete(0, tk.END)
        self.qty_entry.delete(0, tk.END)
        self.qty_entry.insert(0, "1")
        self.weight_entry.set("")
        self.giveaway_var.set(False)
        self.update_treeview()
        self.update_bin_display()
        self.update_footer()

    def show_avg_sell_rate(self):
        """Calculate and display the average time between item sales."""
        if not self.bidders:
            messagebox.showinfo("Info", "No transactions to analyze.")
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
            messagebox.showinfo("Info", "Need at least two transactions to calculate average sell rate.")
            return
        timestamps.sort()
        time_diffs = [(timestamps[i + 1] - timestamps[i]).total_seconds() for i in range(len(timestamps) - 1)]
        avg_seconds = sum(time_diffs) / len(time_diffs)
        minutes, seconds = divmod(int(avg_seconds), 60)
        time_str = f"{minutes} min {seconds} sec" if minutes > 0 else f"{seconds} sec"
        messagebox.showinfo("Average Sell Rate", f"Average time per sale: {time_str}")

    def import_labels(self):
        """Import usernames from a PDF for label generation using OCR if needed."""
        file_path = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf")])
        if not file_path:
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
                            print(f"Warning: OCR failed on page {page_num}: {e}")
                    matches = [m.strip().lower() for m in re.findall(r'\((.*?)\)', text or "")]
                    print(f"Extracted matches on page {page_num}: {matches}")
                    for username in self.bidders.keys():
                        if username in matches:
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
                    messagebox.showinfo("Notice", f"Imported {len(self.label_data)} labels. Some bidders not found in PDF: {', '.join(missing_display)}")
                else:
                    messagebox.showinfo("Success", f"Imported {len(self.label_data)} labels. All bidders matched.")
            else:
                messagebox.showwarning("Warning", "No matching usernames found in PDF.")
            self.update_footer()
        except Exception as e:
            progress_window.destroy()
            messagebox.showerror("Error", f"Import failed: {str(e)}")

    def clear_data(self):
        """Clear all bidder and label data to start a new show."""
        if messagebox.askyesno("Confirm", "Start new show?"):
            self.save_auction_history()
            self.bidders.clear()
            self.label_data.clear()
            self.last_bidder = None
            self.next_bin = 1
            self.next_giveaway_num = 1
            self.tree.delete(*self.tree.get_children())
            self.search_result.config(text="")
            self.current_bidder_label.config(text="")
            self.latest_bin_assignment = "Waiting for bidder..."
            self.socketio.emit('update', {'data': self.latest_bin_assignment})
            self.show_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            self.bidder_csv_path = os.path.join(os.path.expanduser("~"), "Documents", "SwiftSale", f"bidder_history_{self.show_id}.csv")
            os.makedirs(os.path.dirname(self.bidder_csv_path), exist_ok=True)
            self.update_bin_display()
            if self.bot and self.chat_id:
                asyncio.create_task(self.run_coroutine(
                    self.bot.send_message(self.chat_id, f"New show: {self.show_id}")
                ))
            messagebox.showinfo("Info", f"New show: {self.show_id}")
            self.update_footer()

    def search_bidders(self):
        """Search for bidders by username or transaction details."""
        q = self.search_entry.get().strip().lower()
        if not q:
            self.search_result.config(text="Enter query!")
            return
        if q in self.bidders:
            data = self.bidders[q]
            display_username = data.get('original_username', q)
            self.search_result.config(text=f"Username: {display_username} | Bin: {data['bin']} | Items: {data['total_items']}")
            return
        res = []
        for username, data in self.bidders.items():
            display_username = data.get('original_username', username)
            for t in data['transactions']:
                if q in username or q in str(t['qty']) or q in t['weight'].lower():
                    res.append(f"{display_username} | Bin {data['bin']} | {t['qty']}x | {t['weight']} | # {t['giveaway_num']} | {t['timestamp']}")
        self.search_result.config(text='\n'.join(res) if res else 'No matches!')

    def update_treeview(self):
        """Update the Treeview with bidder transactions."""
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
            iid = self.tree.identify_row(e.y)
            if iid:
                self.tree.item(iid, open=not self.tree.item(iid, 'open'))
        self.tree.bind('<ButtonRelease-1>', on_click)

    def print_bidders(self):
        """Export bidder data to a CSV file."""
        if not self.bidders:
            messagebox.showinfo("Info", "No bidders to export.")
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
        except Exception as e:
            messagebox.showerror("Error", f"Export failed: {str(e)}")

    def save_auction_history(self):
        """Save auction summary to a CSV file."""
        if not self.bidders:
            return
        csv_path = os.path.join(os.path.expanduser("~"), "Documents", "SwiftSale", f"auction_history_{self.show_id}.csv")
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
        except Exception as e:
            messagebox.showerror("Error", f"Save failed: {str(e)}")

    def print_labels(self):
        """Print labels for a specified bin range by adding bin numbers to PDF pages."""
        if not self.label_data:
            messagebox.showwarning("Warning", "No labels imported. Please import a PDF first.")
            return
        try:
            bin_range = self.bin_range_entry.get().strip() or f"1-{self.max_bins}"
            start_bin, end_bin = map(int, bin_range.split("-"))
            if end_bin > self.max_bins or start_bin < 1:
                messagebox.showerror("Error", f"Bin range must be between 1 and {self.max_bins}.")
                return
            labels = [l for l in sorted(self.label_data, key=lambda x: x["bin_number"]) if start_bin <= l["bin_number"] <= end_bin]
            if not labels:
                messagebox.showwarning("Warning", f"No labels found in bin range {start_bin}-{end_bin}.")
                return
            printed_count = 0
            for label in labels:
                doc = fitz.open(label["pdf_path"])
                page = doc[label["pdf_page"]]
                text_x = float(self.text_x_entry.get() or 50)
                text_y = float(self.text_y_entry.get() or 50)
                label_text = self.label_text_entry.get().format(bin_number=label["bin_number"])
                page.insert_text((text_x, text_y), label_text, fontsize=12, fontname="helv", color=(0, 0, 0))
                temp_doc = fitz.open()
                temp_doc.insert_pdf(doc, from_page=label["pdf_page"], to_page=label["pdf_page"])
                temp_pdf = os.path.join(os.path.expanduser("~"), "Documents", "SwiftSale", f"label_bin_{label['bin_number']}.pdf")
                os.makedirs(os.path.dirname(temp_pdf), exist_ok=True)
                temp_doc.save(temp_pdf)
                temp_doc.close()
                doc.close()
                if os.name == "nt":
                    os.startfile(temp_pdf, "print")
                else:
                    subprocess.run(["lp", temp_pdf], check=True)
                os.remove(temp_pdf)
                printed_count += 1
            messagebox.showinfo("Success", f"Printed {printed_count} labels for bins {start_bin}-{end_bin}.")
        except ValueError:
            messagebox.showerror("Error", "Invalid bin range format. Use 'start-end' (e.g., '1-25').")
        except Exception as e:
            messagebox.showerror("Error", f"Print failed: {str(e)}")

    def test_print_label(self):
        """Print a single test label using the first available label data."""
        if not self.label_data:
            messagebox.showwarning("Warning", "No labels imported. Please import a PDF first.")
            return
        try:
            label = sorted(self.label_data, key=lambda x: x["bin_number"])[0]
            doc = fitz.open(label["pdf_path"])
            page = doc[label["pdf_page"]]
            text_x = float(self.text_x_entry.get() or 50)
            text_y = float(self.text_y_entry.get() or 50)
            label_text = self.label_text_entry.get().format(bin_number=label["bin_number"])
            page.insert_text((text_x, text_y), label_text, fontsize=12, fontname="helv", color=(0, 0, 0))
            temp_doc = fitz.open()
            temp_doc.insert_pdf(doc, from_page=label["pdf_page"], to_page=label["pdf_page"])
            temp_pdf = os.path.join(os.path.expanduser("~"), "Documents", "SwiftSale", "test_label.pdf")
            os.makedirs(os.path.dirname(temp_pdf), exist_ok=True)
            temp_doc.save(temp_pdf)
            temp_doc.close()
            doc.close()
            if os.name == "nt":
                os.startfile(temp_pdf, "print")
            else:
                subprocess.run(["lp", temp_pdf], check=True)
            os.remove(temp_pdf)
            messagebox.showinfo("Success", f"Test label printed for bin {label['bin_number']}.")
        except Exception as e:
            messagebox.showerror("Error", f"Test print failed: {str(e)}")

    def preview_labels(self):
        """Preview a single label by displaying a PDF page with the bin number."""
        if not self.label_data:
            messagebox.showwarning("Warning", "No labels to preview. Please import a PDF first.")
            return
        try:
            label = sorted(self.label_data, key=lambda x: x["bin_number"])[0]
            doc = fitz.open(label["pdf_path"])
            page = doc[label["pdf_page"]]
            text_x = float(self.text_x_entry.get() or 50)
            text_y = float(self.text_y_entry.get() or 50)
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
        except Exception as e:
            messagebox.showerror("Error", f"Preview failed: {str(e)}")

    def export_labels(self):
        """Export label data to a CSV file."""
        if not self.label_data:
            messagebox.showwarning("Warning", "No labels to export. Please import a PDF first.")
            return
        try:
            csv_path = os.path.join(os.path.expanduser("~"), "Documents", "SwiftSale", f"labels_{self.show_id}.csv")
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
            messagebox.showinfo("Success", f"Labels exported to {csv_path}")
        except Exception as e:
            messagebox.showerror("Error", f"Export failed: {str(e)}")

    def show_printer_setup(self):
        """Display printer setup instructions."""
        messagebox.showinfo("Printer Setup Guide", """
        Printer Setup Instructions:
        1. Use Google Chrome for Whatnot label printing.
        2. Ensure pop-ups are enabled in Chrome settings.
        3. For thermal printers (4x6):
           - Set paper size to 4x6 inches.
           - Use portrait orientation.
        4. For standard printers (8.5x11):
           - Set paper size to Letter.
           - Enable 'Fit to Page' in print settings.
        5. If labels print sideways, adjust orientation in printer settings.
        6. Perform a test print before printing multiple labels.
        7. Ensure your default PDF viewer supports printing (e.g., Adobe Reader).
        """)

if __name__ == '__main__':
    root = tk.Tk()
    app = SCDWhatnotGUI(root)
    root.mainloop()