import os
import re
import json
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.constants import ParseMode
import gspread
from google.oauth2.service_account import Credentials
from collections import defaultdict
import google.generativeai as genai


# Configuration
SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
SPREADSHEET_NAME = "Anime Database"
SERVICE_ACCOUNT_FILE = "Credentials.json"
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "your channel id"))
AUTHORIZED_UPLOADERS = [ "your id" , " friend id"]
genai.configure(api_key=GEMINI_API_KEY)
class GeminiAssistant:
    def __init__(self):
        self.model = genai.GenerativeModel('gemini-2.0-flash')
        self.chat_sessions = {}
       
    def parse_bulk_upload(self, text):
        """Parse bulk upload with Gemini AI and regex fallback"""
        prompt = f"""Parse this bulk upload and extract anime information.
Return JSON array with fields: anime_name, season, episode, quality, audio, url
Message:
{text}
Example: [{{"anime_name": "365 Days to the Wedding", "season": "S01", "episode": "E01", "quality": "480p", "audio": "Single", "url": "https://..."}}]
Return ONLY valid JSON array."""
        try:
            response = self.model.generate_content(prompt)
            result = response.text.strip()
           
            if result.startswith('```json'):
                result = result[7:]
            if result.startswith('```'):
                result = result[3:]
            if result.endswith('```'):
                result = result[:-3]
           
            episodes = json.loads(result.strip())
            if isinstance(episodes, list) and len(episodes) > 0:
                return episodes
        except Exception as e:
            print(f"Gemini parsing failed: {e}")
       
        return self._regex_parse(text)
   
    def _regex_parse(self, text):
        """Enhanced regex parser supporting multiple formats"""
        episodes = []
        lines = text.split('\n')
        current_entry = ""
       
        for line in lines:
            line = re.sub(r'\*+', '', line)
            line = re.sub(r'`+', '', line)
            line = line.strip()
           
            if not line:
                continue
           
            if re.match(r'^\d+\.', line):
                if current_entry:
                    ep = self._parse_single_entry(current_entry)
                    if ep:
                        episodes.append(ep)
                current_entry = line
            else:
                current_entry += " " + line
       
        if current_entry:
            ep = self._parse_single_entry(current_entry)
            if ep:
                episodes.append(ep)
       
        return episodes
   
    def _parse_single_entry(self, entry):
        """Parse single episode entry - supports multiple formats"""
        try:
            # Extract URL first
            url_match = re.search(r'(https?://[^\s]+)', entry)
            if not url_match:
                return None
            url = url_match.group(1).strip('`)')
           
            entry_clean = entry.replace(url, '').strip()
           
            # Extract Season/Episode - support multiple formats
            # [S01-E01], [S01E01], [S1-E1], S01E01, 1x01, etc.
            se_match = re.search(r'$$ ?S(\d{1,2})-?E(\d{1,2}) $$?', entry_clean, re.IGNORECASE)
            if not se_match:
                # Try alternative format: 1x01
                se_match = re.search(r'(\d{1,2})x(\d{1,2})', entry_clean, re.IGNORECASE)
            if not se_match:
                # Try format: Episode 01 or Ep 01
                se_match = re.search(r'(?:Episode|Ep\.?)\s*(\d{1,2})', entry_clean, re.IGNORECASE)
                if se_match:
                    season = "01" # Default to season 1
                    episode = se_match.group(1).zfill(2)
                else:
                    return None
            else:
                season = se_match.group(1).zfill(2)
                episode = se_match.group(2).zfill(2)
           
            # Remove season/episode from entry
            entry_clean = re.sub(r'$$ ?S\d{1,2}-?E\d{1,2} $$?', '', entry_clean, flags=re.IGNORECASE)
            entry_clean = re.sub(r'\d{1,2}x\d{1,2}', '', entry_clean, flags=re.IGNORECASE)
            entry_clean = re.sub(r'(?:Episode|Ep\.?)\s*\d{1,2}', '', entry_clean, flags=re.IGNORECASE)
           
            # Extract Quality - support 480p, 720p, 1080p, 2160p, 4K, etc.
            quality = "720p" # Default
            qual_match = re.search(r'$$ ?(\d{3,4}p?|4K|2K) $$?', entry_clean, re.IGNORECASE)
            if qual_match:
                quality = qual_match.group(1).upper()
                if not quality.endswith('P') and quality not in ['4K', '2K']:
                    quality += 'p'
                entry_clean = entry_clean.replace(qual_match.group(0), '')
           
            # Extract Audio - support multiple terms
            audio = "Single" # Default
            audio_match = re.search(r'$$ ?(Dual|Single|Subbed|Dubbed|Sub|Dub|Multi) $$?', entry_clean, re.IGNORECASE)
            if audio_match:
                audio_term = audio_match.group(1).capitalize()
                # Normalize audio terms
                if audio_term in ['Sub', 'Subbed']:
                    audio = 'Single'
                elif audio_term in ['Dub', 'Dubbed']:
                    audio = 'Dubbed'
                elif audio_term == 'Multi':
                    audio = 'Dual'
                else:
                    audio = audio_term
                entry_clean = entry_clean.replace(audio_match.group(0), '')
           
            # Extract Anime Name - clean up
            anime_name = re.sub(r'^\d+\.', '', entry_clean) # Remove numbering
            anime_name = re.sub(r'\.(mkv|mp4|avi|webm).*$', '', anime_name, flags=re.IGNORECASE) # Remove extension
            anime_name = re.sub(r'@\w+', '', anime_name) # Remove telegram channels
            anime_name = re.sub(r'$$ .*? $$', '', anime_name) # Remove any remaining brackets
            anime_name = re.sub(r'$$ .*? $$', '', anime_name) # Remove parentheses
            anime_name = re.sub(r'\s*-\s*$', '', anime_name) # Remove trailing dash
            anime_name = re.sub(r'\s+', ' ', anime_name).strip() # Normalize spaces
           
            # Remove language tags at end (Tam, Tamil, Eng, etc.)
            anime_name = re.sub(r'\s+(Tam|Tamil|Eng|English|Hin|Hindi|Jap|Japanese)\s*$', '', anime_name, flags=re.IGNORECASE)
           
            if not anime_name or len(anime_name) < 2:
                return None
           
            return {
                'anime_name': anime_name,
                'season': f'S{season}',
                'episode': f'E{episode}',
                'quality': quality,
                'audio': audio,
                'url': url
            }
        except Exception as e:
            print(f"Parse error for entry: {e}")
            return None
    def interpret_query(self, query_text, available_anime):
        """Interpret natural language queries"""
        prompt = f"""Interpret anime query and return search parameters.
Available: {', '.join(available_anime[:20])}
Query: "{query_text}"
Return JSON:
{{"anime_name": "name or null", "season": "S1 or null", "episode": "E01 or null", "quality": "720p or null", "audio": "Dual or null", "intent": "search"}}
Return ONLY valid JSON."""
        try:
            response = self.model.generate_content(prompt)
            result = response.text.strip()
           
            if result.startswith('```json'):
                result = result[7:]
            if result.startswith('```'):
                result = result[3:]
            if result.endswith('```'):
                result = result[:-3]
           
            return json.loads(result.strip())
        except Exception as e:
            print(f"Query error: {e}")
            return {"intent": "search", "anime_name": query_text}
    def chat(self, user_id, message, database_context=None):
        """Chat with Gemini AI"""
        if user_id not in self.chat_sessions:
            self.chat_sessions[user_id] = self.model.start_chat(history=[])
       
        chat = self.chat_sessions[user_id]
        full_message = message
        if database_context:
            full_message = f"Database: {database_context}\n\nUser: {message}"
       
        try:
            response = chat.send_message(full_message)
            return response.text
        except Exception as e:
            return f"Error: {str(e)}"
   
    def clear_chat(self, user_id):
        """Clear chat history"""
        if user_id in self.chat_sessions:
            del self.chat_sessions[user_id]
            return True
        return False
    def format_response(self, results, query_context):
        """Format search results"""
        if not results:
            return "No results found."
        return self._simple_format(results)
   
    def _simple_format(self, results):
        """Format as numbered URL list grouped by quality"""
        grouped = {}
        for r in results:
            key = (r['anime_name'], r['season'], r['quality'])
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(r)
       
        output = []
        counter = 1
       
        for (anime, season, quality), episodes in sorted(grouped.items()):
            sorted_eps = sorted(episodes, key=lambda x: int(re.search(r'\d+', x['episode']).group()))
            for ep in sorted_eps:
                output.append(f"{counter}. {ep['url']}\n")
                counter += 1
       
        return "".join(output) if output else "No results."
class GoogleSheetsDB:
    def __init__(self, credentials_file, spreadsheet_name):
        self.creds = Credentials.from_service_account_file(credentials_file, scopes=SCOPES)
        self.client = gspread.authorize(self.creds)
        self.spreadsheet_name = spreadsheet_name
        self.sheet = None
        self.init_sheet()
   
    def init_sheet(self):
        """Initialize Google Sheet"""
        try:
            self.spreadsheet = self.client.open(self.spreadsheet_name)
            self.sheet = self.spreadsheet.sheet1
           
            first_row = self.sheet.row_values(1)
            if not first_row or first_row[0] != "Anime ID":
                headers = ["Anime ID", "Anime Name", "Season", "Episode", "Quality", "Audio", "Download URL", "Added Date", "Status"]
                self.sheet.insert_row(headers, 1)
                self.sheet.format('A1:I1', {
                    'backgroundColor': {'red': 0.27, 'green': 0.45, 'blue': 0.77},
                    'textFormat': {'bold': True, 'foregroundColor': {'red': 1, 'green': 1, 'blue': 1}},
                    'horizontalAlignment': 'CENTER'
                })
        except gspread.SpreadsheetNotFound:
            print(f"Spreadsheet '{self.spreadsheet_name}' not found!")
            raise
   
    def get_next_anime_id(self):
        """Generate next ID"""
        all_values = self.sheet.get_all_values()[1:]
        max_id = 0
        for row in all_values:
            if row and row[0]:
                match = re.search(r'AN(\d+)', row[0])
                if match:
                    max_id = max(max_id, int(match.group(1)))
        return f"AN{str(max_id + 1).zfill(3)}"
   
    def find_anime_id(self, anime_name):
        """Find ID by name"""
        all_values = self.sheet.get_all_values()[1:]
        for row in all_values:
            if row and len(row) > 1 and row[1].lower().strip() == anime_name.lower().strip():
                return row[0]
        return None
   
    def add_episode(self, anime_name, season, episode, quality, audio, url, status="Active"):
        """Add episode to database - allows multiple qualities for same episode"""
        try:
            anime_id = self.find_anime_id(anime_name)
            if not anime_id:
                anime_id = self.get_next_anime_id()
           
            # Check for exact duplicates (same anime, season, episode, quality, and URL)
            existing = self.query_anime(anime_name=anime_name, season=season, episode=episode, quality=quality)
            if existing:
                # Check if URL already exists
                for ep in existing:
                    if ep['url'] == url:
                        return None, "Exact duplicate"
                # Different URL with same quality is allowed (alternative source)
           
            date_added = datetime.now().strftime("%Y-%m-%d %H:%M")
            new_row = [anime_id, anime_name, season, episode, quality, audio, url, date_added, status]
            self.sheet.append_row(new_row)
            return anime_id, "Success"
        except Exception as e:
            print(f"Add error: {e}")
            return None, f"Error: {str(e)}"
   
    def query_anime(self, anime_name=None, season=None, episode=None, quality=None, audio=None):
        """Query episodes"""
        try:
            all_values = self.sheet.get_all_values()[1:]
            results = []
           
            for row in all_values:
                if not row or not row[0]:
                    continue
               
                match = True
                if anime_name and anime_name.lower() not in row[1].lower():
                    match = False
                if season and row[2].upper() != season.upper():
                    match = False
                if episode and row[3].upper() != episode.upper():
                    match = False
                if quality and quality.lower() not in row[4].lower():
                    match = False
                if audio and audio.lower() not in row[5].lower():
                    match = False
               
                if match and len(row) >= 7:
                    results.append({
                        'anime_id': row[0],
                        'anime_name': row[1],
                        'season': row[2],
                        'episode': row[3],
                        'quality': row[4],
                        'audio': row[5],
                        'url': row[6],
                        'date_added': row[7] if len(row) > 7 else 'N/A',
                        'status': row[8] if len(row) > 8 else 'Active'
                    })
            return results
        except Exception as e:
            print(f"Query error: {e}")
            return []
   
    def get_all_anime_names(self):
        """Get all anime names"""
        try:
            all_values = self.sheet.get_all_values()[1:]
            anime_set = set()
            for row in all_values:
                if row and len(row) > 1 and row[1]:
                    anime_set.add(row[1])
            return sorted(list(anime_set))
        except Exception as e:
            print(f"Get names error: {e}")
            return []
   
    def get_summary(self):
        """Get database summary"""
        anime_list = self.get_all_anime_names()
        return f"Available anime ({len(anime_list)}): {', '.join(anime_list[:10])}"
# Initialize
gemini = GeminiAssistant()
db = GoogleSheetsDB(SERVICE_ACCOUNT_FILE, SPREADSHEET_NAME)
# Logging
async def log_to_channel(context, user_id, username, action, details=""):
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_message = f"""
📊 <b>Bot Activity Log</b>
👤 <b>User ID:</b> <code>{user_id}</code>
👤 <b>Username:</b> @{username if username else 'No username'}
⚡ <b>Action:</b> {action}
🕐 <b>Timestamp:</b> {timestamp}
{details}
"""
        await context.bot.send_message(chat_id=LOG_CHANNEL_ID, text=log_message, parse_mode=ParseMode.HTML)
    except Exception as e:
        print(f"Log failed: {e}")
async def log_upload_to_channel(context, user_id, username, episodes, added, skipped):
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        url_list = [f"{idx}. {ep['url']}" for idx, ep in enumerate(episodes[:20], 1)]
        urls_formatted = "\n".join(url_list)
        if len(episodes) > 20:
            urls_formatted += f"\n... and {len(episodes) - 20} more"
       
        log_message = f"""
📦 <b>Bulk Upload Log</b>
👤 <b>User:</b> {username} (<code>{user_id}</code>)
🕐 <b>Time:</b> {timestamp}
✅ <b>Added:</b> {added}
⚠️ <b>Skipped:</b> {skipped}
📺 <b>Total:</b> {len(episodes)}
🔗 <b>URLs:</b>
{urls_formatted}
"""
        await context.bot.send_message(chat_id=LOG_CHANNEL_ID, text=log_message, parse_mode=ParseMode.HTML)
    except Exception as e:
        print(f"Upload log failed: {e}")
# Bot Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
   
    await log_to_channel(context, user_id, username, "Started Bot")
   
    keyboard = [
        [InlineKeyboardButton("📚 Browse", callback_data="browse")],
        [InlineKeyboardButton("🔍 Search", callback_data="search")],
        [InlineKeyboardButton("💬 Chat", callback_data="chat_mode")],
        [InlineKeyboardButton("ℹ️ Help", callback_data="help")]
    ]
   
    welcome = """🎌 <b>AI Anime Database Bot</b>
Powered by Google Gemini & Google Sheets
<b>Features:</b>
• Auto-parse bulk uploads
• Natural language search
• AI chat assistant
• Multiple quality support
<b>Commands:</b>
/search <i>anime name</i>
/chat <i>question</i>
/myid
Paste your bulk upload!"""
   
    await update.message.reply_text(welcome, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.HTML)
async def chat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
   
    if not context.args:
        await update.message.reply_text(
            "💬 <b>Chat with AI</b>\n\n"
            "Usage: /chat your question\n"
            "Example: /chat recommend action anime",
            parse_mode=ParseMode.HTML
        )
        return
   
    message = ' '.join(context.args)
    db_context = db.get_summary()
    response = gemini.chat(user_id, message, db_context)
   
    await update.message.reply_text(f"🤖 {response}", parse_mode=ParseMode.HTML)
async def myid_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or "No username"
    first_name = update.effective_user.first_name
    is_authorized = "✅ Yes" if user_id in AUTHORIZED_UPLOADERS else "❌ No"
   
    await update.message.reply_text(
        f"👤 <b>Your Info</b>\n\n"
        f"<b>ID:</b> <code>{user_id}</code>\n"
        f"<b>Username:</b> @{username}\n"
        f"<b>Name:</b> {first_name}\n"
        f"<b>Upload Access:</b> {is_authorized}",
        parse_mode=ParseMode.HTML
    )
async def authorize_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
   
    if user_id not in AUTHORIZED_UPLOADERS:
        await update.message.reply_text("⛔ Admin only.")
        return
   
    if not context.args:
        await update.message.reply_text("Usage: /authorize <user_id>")
        return
   
    try:
        new_user_id = int(context.args[0])
        if new_user_id in AUTHORIZED_UPLOADERS:
            await update.message.reply_text(f"✅ User {new_user_id} already authorized.")
        else:
            AUTHORIZED_UPLOADERS.append(new_user_id)
            await log_to_channel(context, user_id, username, "Authorization", f"Authorized: {new_user_id}")
            await update.message.reply_text(
                f"✅ <b>Authorized!</b>\n\n"
                f"User ID: <code>{new_user_id}</code>",
                parse_mode=ParseMode.HTML
            )
    except ValueError:
        await update.message.reply_text("❌ Invalid ID.")
async def listauth_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
   
    if user_id not in AUTHORIZED_UPLOADERS:
        await update.message.reply_text("⛔ Admin only.")
        return
   
    text = f"👥 <b>Authorized Users ({len(AUTHORIZED_UPLOADERS)})</b>\n\n"
    for idx, uid in enumerate(AUTHORIZED_UPLOADERS, 1):
        text += f"{idx}. <code>{uid}</code>\n"
   
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)
async def upload_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /upload command for better control"""
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
   
    if user_id not in AUTHORIZED_UPLOADERS:
        await update.message.reply_text(
            "⛔ <b>Unauthorized</b>\n\n"
            "Contact admin for access.\n"
            f"Your ID: <code>{user_id}</code>",
            parse_mode=ParseMode.HTML
        )
        return
   
    # Check if command has text argument or is a reply
    text = None
   
    # Option 1: Reply to a message with /upload
    if update.message.reply_to_message and update.message.reply_to_message.text:
        text = update.message.reply_to_message.text
   
    # Option 2: Send anime list after /upload command
    elif context.args:
        text = ' '.join(context.args)
   
    # Option 3: Usage instructions
    else:
        await update.message.reply_text(
            "📤 <b>Upload Command</b>\n\n"
            "<b>Method 1:</b> Reply to your anime list with /upload\n"
            "<b>Method 2:</b> Type /upload followed by your list\n"
            "<b>Method 3:</b> Just paste your list (auto-detects)\n\n"
            "<b>Supported Formats:</b>\n"
            "<code>1. [S01-E01] Anime [480p] [Single].mkv\n"
            "https://link</code>\n\n"
            "<code>2. Anime Name 1x01 720p\n"
            "https://link</code>\n\n"
            "✅ Multiple qualities OK (480p, 720p, 1080p, 4K)",
            parse_mode=ParseMode.HTML
        )
        return
   
    await update.message.reply_text(f"⏳ Parsing {len(text.split(chr(10)))} lines...")
   
    episodes = gemini.parse_bulk_upload(text)
   
    if not episodes:
        await log_to_channel(context, user_id, username, "Parse Failed")
        await update.message.reply_text(
            "❌ <b>Parse Failed</b>\n\n"
            "Could not extract episodes. Check format:\n"
            "<code>1. [S01-E01] Anime [480p] [Single].mkv\n"
            "https://link</code>\n\n"
            "Or try copying the exact format above.",
            parse_mode=ParseMode.HTML
        )
        return
   
    added = 0
    skipped = 0
    anime_ids = set()
    errors = []
    quality_counts = defaultdict(int)
   
    for ep in episodes:
        try:
            anime_id, status = db.add_episode(
                ep['anime_name'], ep['season'], ep['episode'],
                ep['quality'], ep['audio'], ep['url']
            )
            if status == "Success":
                added += 1
                quality_counts[ep['quality']] += 1
                if anime_id:
                    anime_ids.add(anime_id)
            else:
                skipped += 1
        except Exception as e:
            errors.append(f"{ep['anime_name']} {ep['season']}{ep['episode']}: {str(e)[:50]}")
   
    await log_upload_to_channel(context, user_id, username, episodes, added, skipped)
   
    result_msg = f"✅ <b>Upload Complete!</b>\n\n"
    result_msg += f"👤 Uploader: {username}\n"
    result_msg += f"✅ Added: {added} episodes\n"
    result_msg += f"⚠️ Skipped: {skipped} (duplicates)\n"
    result_msg += f"📺 Series: {len(anime_ids)}\n"
   
    # Show quality breakdown
    if quality_counts:
        result_msg += f"\n<b>Quality Breakdown:</b>\n"
        for qual, count in sorted(quality_counts.items()):
            result_msg += f" • {qual}: {count} eps\n"
   
    result_msg += f"\n🔗 Check Google Sheet!"
   
    if errors and len(errors) <= 5:
        result_msg += f"\n\n<b>Errors:</b>\n"
        for err in errors[:5]:
            result_msg += f"• {err}\n"
   
    await update.message.reply_text(result_msg, parse_mode=ParseMode.HTML)
async def smart_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
   
    if not context.args:
        await update.message.reply_text(
            "🔍 <b>Search</b>\n\n"
            "Usage: /search <i>query</i>\n"
            "Examples:\n"
            "• /search Naruto\n"
            "• /search One Piece S1 720p",
            parse_mode=ParseMode.HTML
        )
        return
   
    query_text = ' '.join(context.args)
    await log_to_channel(context, user_id, username, "Search", f"Query: {query_text}")
   
    available_anime = db.get_all_anime_names()
    if not available_anime:
        await update.message.reply_text("📭 Database is empty!")
        return
   
    params = gemini.interpret_query(query_text, available_anime)
    results = db.query_anime(
        anime_name=params.get('anime_name'),
        season=params.get('season'),
        episode=params.get('episode'),
        quality=params.get('quality'),
        audio=params.get('audio')
    )
   
    formatted = gemini.format_response(results, query_text)
    await log_to_channel(context, user_id, username, "Results", f"Found: {len(results)}")
    await update.message.reply_text(formatted, parse_mode=ParseMode.HTML)
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
   
    # Check if message contains bulk upload pattern
    is_bulk_upload = re.search(r'\d+\.\s*\[?S\d+-?E\d+.*https?://', text, re.IGNORECASE)
   
    if is_bulk_upload:
        # Auto-detect bulk upload - suggest using /upload command for better control
        if user_id not in AUTHORIZED_UPLOADERS:
            await log_to_channel(context, user_id, username, "Unauthorized Upload")
            await update.message.reply_text(
                "⛔ <b>Unauthorized</b>\n\n"
                "Contact admin for access.\n"
                f"Your ID: <code>{user_id}</code>",
                parse_mode=ParseMode.HTML
            )
            return
       
        # Suggest using /upload command for better control
        await update.message.reply_text(
            "📤 <b>Bulk Upload Detected</b>\n\n"
            "To upload, reply to this message with:\n"
            "<code>/upload</code>\n\n"
            "Or use /upload command directly with your list.",
            parse_mode=ParseMode.HTML
        )
    else:
        # Regular chat with Gemini
        await log_to_channel(context, user_id, username, "Chat", text[:50])
        db_context = db.get_summary()
        response = gemini.chat(user_id, text, db_context)
        await update.message.reply_text(f"🤖 {response}", parse_mode=ParseMode.HTML)
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
   
    if query.data == "chat_mode":
        await query.edit_message_text(
            "💬 <b>Chat Active</b>\n\nType your questions!",
            parse_mode=ParseMode.HTML
        )
    elif query.data == "clear_chat":
        user_id = query.from_user.id
        if gemini.clear_chat(user_id):
            await query.edit_message_text("✅ Chat cleared!")
        else:
            await query.edit_message_text("ℹ️ No history.")
    elif query.data == "search":
        await query.edit_message_text("🔍 Type: /search <i>anime</i>", parse_mode=ParseMode.HTML)
    elif query.data == "browse":
        anime_list = db.get_all_anime_names()
        if anime_list:
            text = f"📚 <b>Database ({len(anime_list)})</b>\n\n"
            text += "\n".join([f"• {anime}" for anime in anime_list[:20]])
            if len(anime_list) > 20:
                text += f"\n\n...{len(anime_list) - 20} more"
        else:
            text = "📭 Empty!"
        await query.edit_message_text(text, parse_mode=ParseMode.HTML)
    elif query.data == "help":
        help_text = """
📖 <b>Help</b>
<b>All Users:</b>
/start - Main menu
/search - Search anime
/chat - Talk with AI
/myid
<b>Uploaders:</b>
Reply to bulk upload with /upload
<b>Admin:</b>
/authorize - Add uploader
/listauth - List users
"""
        await query.edit_message_text(help_text, parse_mode=ParseMode.HTML)
def main():
    try:
        app = Application.builder().token(TELEGRAM_TOKEN).build()
       
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("search", smart_search))
        app.add_handler(CommandHandler("chat", chat_command))
        app.add_handler(CommandHandler("myid", myid_command))
        app.add_handler(CommandHandler("authorize", authorize_command))
        app.add_handler(CommandHandler("listauth", listauth_command))
        app.add_handler(CommandHandler("upload", upload_command))
        app.add_handler(CallbackQueryHandler(button_callback))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
       
        print("🤖 Bot Running!")
        print("📊 Google Sheets Connected")
        print("🧠 Gemini AI Active")
        print(f"🔐 {len(AUTHORIZED_UPLOADERS)} authorized users")
        print("📤 /upload command enabled")
        print("✅ Multiple quality support enabled")
        print("\n✅ Ready!\n")
       
        app.run_polling()
    except Exception as e:
        print(f"❌ Startup error: {e}")
        raise
if __name__ == "__main__":
    main()
