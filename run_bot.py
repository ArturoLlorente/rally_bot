import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, BotCommand, CallbackQuery
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes, CallbackContext
from telegram.constants import ParseMode
import json
import time
from typing import Dict, List, Set
from pathlib import Path
from dotenv import load_dotenv
import os

from api_utils import get_stations_data
from data_utils import print_routes_for_stations, get_stations_with_returns, save_output_to_json
from gui import gui

# Load environment variables
load_dotenv()

def create_progress_bar(progress: int, total: int = 100, length: int = 20) -> str:
    """Create a pretty progress bar with percentage"""
    filled_length = int(length * progress / total)
    bar = '█' * filled_length + '░' * (length - filled_length)
    percentage = f"{progress}%".rjust(4)
    return f"[{bar}] {percentage}"

class RoadsurferBot:
    def __init__(self, token: str):
        self.token = token
        self.DB_PATH = Path("station_routes.json")
        self.FAVORITES_PATH = Path("user_favorites.json")
        self.UPDATE_COOLDOWN = 15 * 60  # 15 minutes in seconds
        self.last_update_time = 0
        
        # Initialize logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler('bot.log')
            ]
        )
        self.logger = logging.getLogger(__name__)
        
        # Load data
        self.stations_with_returns = self._load_stations()
        self.user_favorites = self._load_user_favorites()
        
        # Initialize application
        self.application = ApplicationBuilder().token(self.token).build()
        self._setup_handlers()

    def _load_stations(self) -> List[Dict]:
        """Load stations data from JSON file"""
        try:
            if self.DB_PATH.exists():
                with open(self.DB_PATH, 'r') as f:
                    return json.load(f)
            return []
        except Exception as e:
            self.logger.error(f"Error loading stations: {e}")
            return []

    def _load_user_favorites(self) -> Dict[str, Set[str]]:
        """Load user favorites from JSON file"""
        try:
            if self.FAVORITES_PATH.exists():
                with open(self.FAVORITES_PATH, 'r') as f:
                    # Convert lists back to sets
                    data = json.load(f)
                    return {user_id: set(stations) for user_id, stations in data.items()}
            return {}
        except Exception as e:
            self.logger.error(f"Error loading favorites: {e}")
            return {}

    def _save_user_favorites(self) -> None:
        """Save user favorites to JSON file"""
        try:
            # Convert sets to lists for JSON serialization
            data = {user_id: list(stations) for user_id, stations in self.user_favorites.items()}
            with open(self.FAVORITES_PATH, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            self.logger.error(f"Error saving favorites: {e}")

    async def _setup_commands(self) -> None:
        """Set up the bot commands in Telegram"""
        commands = [
            BotCommand("start", "🚀 Iniciar el bot"),
            BotCommand("actualizar_rutas", "🔄 Actualizar base de datos de rutas"),
            BotCommand("ver_rutas", "📊 Ver todas las rutas disponibles"),
            BotCommand("favoritos", "⭐ Ver tus estaciones favoritas"),
            BotCommand("agregar_favorito", "➕ Añadir estación favorita"),
            BotCommand("eliminar_favorito", "➖ Eliminar estación favorita"),
            BotCommand("descargar_mapa", "🗺️ Descargar mapa interactivo"),
            BotCommand("help", "❓ Mostrar ayuda y comandos disponibles"),
        ]
        try:
            await self.application.bot.set_my_commands(commands)
            self.logger.info("Bot commands set up successfully")
        except Exception as e:
            self.logger.error(f"Error setting up bot commands: {e}")

    def _setup_handlers(self) -> None:
        """Set up all command and callback handlers"""
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("actualizar_rutas", self.update_database))
        self.application.add_handler(CommandHandler("ver_rutas", self.show_routes))
        self.application.add_handler(CommandHandler("favoritos", self.show_favorites))
        self.application.add_handler(CommandHandler("agregar_favorito", self.agregar_favorito))
        self.application.add_handler(CommandHandler("eliminar_favorito", self.remove_favorite))
        self.application.add_handler(CommandHandler("descargar_mapa", self.send_html_file))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle the /start command"""
        # Set up commands when user starts the bot
        await self._setup_commands()
        
        keyboard = [
            [InlineKeyboardButton("🚀 Actualizar base de datos", callback_data="update_database")],
            [InlineKeyboardButton("📊 Ver todas las rutas", callback_data="show_routes")],
            [InlineKeyboardButton("⭐ Ver favoritos", callback_data="show_favorites")],
            [InlineKeyboardButton("🗺️ Descargar mapa interactivo", callback_data="descargar_mapa")],
            [InlineKeyboardButton("❓ Ayuda", callback_data="help")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        sent_message = await update.message.reply_text(
            "¡Bienvenido al Bot de Roadsurfer Rally by Arturo! 🚐\n\n"
            "Aquí puedes:\n"
            "• Ver rutas disponibles\n"
            "• Guardar estaciones favoritas\n"
            "• Recibir notificaciones de nuevas rutas\n"
            "• Descargar mapa interactivo\n\n"
            "Selecciona una opción:",
            reply_markup=reply_markup
        )

        try:
            chat = await context.bot.get_chat(update.effective_chat.id)
            if chat.pinned_message is None:
                await context.bot.pin_chat_message(
                    chat_id=update.effective_chat.id,
                    message_id=sent_message.message_id,
                    disable_notification=True
                )
        except Exception as e:
            self.logger.error(f"Could not pin message: {e}")

    async def update_database(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Update the stations database"""
        # Get the appropriate message object based on update type
        message = update.message or update.callback_query.message
        current_time = time.time()
        
        if current_time - self.last_update_time < self.UPDATE_COOLDOWN:
            remaining = int((self.UPDATE_COOLDOWN - (current_time - self.last_update_time)) // 60) + 1
            await message.reply_text(
                f"⚠️ La base de datos fue actualizada hace menos de 15 minutos. "
                f"Por favor espera {remaining} minutos."
            )
            return

        status_message = await message.reply_text(
            "🔄 Iniciando actualización de rutas...\n" + create_progress_bar(0)
        )
        
        try:
            async def progress_callback(percent):
                progress_text = (
                    f"🔄 Actualizando base de datos de rutas...\n"
                    f"{create_progress_bar(percent)}\n"
                    f"Por favor espera..."
                )
                await status_message.edit_text(progress_text)
            
            stations_json = get_stations_data()
            if not stations_json:
                raise Exception("No se pudieron obtener los datos de las estaciones")
            
            self.logger.info(f"Received {len(stations_json)} stations")
            self.logger.debug(f"First station data: {stations_json[0] if stations_json else 'None'}")
                
            self.logger.info("Getting stations with returns...")
            new_stations = await get_stations_with_returns(stations_json, progress_callback)
            if not new_stations:
                raise Exception("No se encontraron rutas disponibles")

            # Check for new routes for favorites
            #
            
            
            self.logger.info("Processing routes for stations...")
            output_data = print_routes_for_stations(new_stations)
            self.stations_with_returns = output_data
            await self._check_new_routes(output_data, context)
            
            self.logger.info("Saving output to JSON...")
            save_output_to_json(output_data)
            
            self.last_update_time = current_time
            
            final_message = (
                "✅ Base de datos actualizada\n"
                f"{create_progress_bar(100)}\n"
                f"📊 Se encontraron {len(new_stations)} estaciones con rutas disponibles."
            )
            await status_message.edit_text(final_message)
            
        except Exception as e:
            self.logger.error(f"Error updating database: {e}", exc_info=True)
            error_message = (
                "❌ Error actualizando la base de datos.\n"
                "Por favor, intenta de nuevo más tarde."
            )
            await status_message.edit_text(error_message)

    async def _check_new_routes(self, new_stations: List[Dict], context: ContextTypes.DEFAULT_TYPE) -> None:
        """Check for new routes matching users' favorite stations"""
        for user_id, favorite_stations in self.user_favorites.items():
            for station in new_stations:
                if station['origin'] in favorite_stations:
                    # Check if this is a new route
                    await self._notify_user(user_id, station, context)

    async def _notify_user(self, user_id: str, station: Dict, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Send notification to user about new routes"""
        try:
            message = f"🔔 ¡Nueva ruta disponible desde tu estación favorita!\n\n"
            message += self.format_station_html(station)
            await context.bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            self.logger.error(f"Error sending notification to {user_id}: {e}")

    async def show_routes(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show all available routes"""
        # Get the appropriate message object based on update type
        message = update.message or update.callback_query.message
        
        if not self.stations_with_returns:
            await message.reply_text(
                "No hay rutas disponibles. Usa /actualizar_rutas primero."
            )
            return

        for station in self.stations_with_returns:
            msg = self.format_station_html(station)
            await message.reply_text(msg, parse_mode=ParseMode.HTML)

    async def show_favorites(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show user's favorite stations"""
        # Get the appropriate message object based on update type
        message = update.message or update.callback_query.message
        user_id = str(update.effective_user.id)
        
        if user_id not in self.user_favorites or not self.user_favorites[user_id]:
            await message.reply_text(
                "No tienes estaciones favoritas. Usa /agregar_favorito <nombre_estacion> para añadir una."
            )
            return

        text = "⭐ Tus estaciones favoritas:\n\n"
        for station in self.user_favorites[user_id]:
            text += f"• {station}\n"
        await message.reply_text(text)

    async def agregar_favorito(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Add stations to favorites using a grid interface"""
        user_id = str(update.effective_user.id)
        
        # Initialize user favorites if needed
        if user_id not in self.user_favorites:
            self.user_favorites[user_id] = set()

        # If no arguments, show the grid of available stations
        try:
            with open("geocode_cache.json", "r", encoding='utf-8') as f:
                all_stations = sorted(json.load(f).keys())
        except Exception as e:
            self.logger.error(f"Error loading geocode cache: {e}")
            await update.message.reply_text("❌ Error al cargar la lista de estaciones.")
            return

        # Filter out stations that are already in favorites
        available_stations = [s for s in all_stations if s not in self.user_favorites[user_id]]

        if not available_stations:
            await update.message.reply_text("Ya tienes todas las estaciones en favoritos.")
            return

        # Create buttons in a 3-column grid
        keyboard = []
        row = []
        for station in available_stations:
            # ⭐ indicates selected state
            row.append(InlineKeyboardButton(f"☆ {station}", callback_data=f"toggle_add_{station}"))
            if len(row) == 3:
                keyboard.append(row)
                row = []
        
        # Add any remaining buttons
        if row:
            keyboard.append(row)

        # Add Done button at the bottom
        keyboard.append([InlineKeyboardButton("✅ Guardar Selección", callback_data="save_favorites")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Initialize selection_messages in bot_data if it doesn't exist
        if 'selection_messages' not in context.bot_data:
            context.bot_data['selection_messages'] = {}
        
        message = await update.message.reply_text(
            "Selecciona las estaciones para añadir a favoritos:\n"
            "(Puedes seleccionar varias antes de guardar)",
            reply_markup=reply_markup
        )
        
        # Store the message info and initial selection state
        context.bot_data['selection_messages'][message.message_id] = {
            'type': 'add',
            'selected': set(),
            'available': set(available_stations),
            'user_id': user_id
        }

    async def remove_favorite(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Remove stations from favorites using a grid interface"""
        user_id = str(update.effective_user.id)
        
        if user_id not in self.user_favorites or not self.user_favorites[user_id]:
            await update.message.reply_text(
                "No tienes estaciones favoritas para eliminar."
            )
            return

        # Create buttons in a 3-column grid
        keyboard = []
        row = []
        for station in sorted(self.user_favorites[user_id]):
            # ★ indicates selected state
            row.append(InlineKeyboardButton(f"★ {station}", callback_data=f"toggle_remove_{station}"))
            if len(row) == 3:
                keyboard.append(row)
                row = []
        
        # Add any remaining buttons
        if row:
            keyboard.append(row)

        # Add Done button at the bottom
        keyboard.append([InlineKeyboardButton("✅ Guardar Cambios", callback_data="save_favorites")])

        reply_markup = InlineKeyboardMarkup(keyboard)
        
        # Initialize selection_messages in bot_data if it doesn't exist
        if 'selection_messages' not in context.bot_data:
            context.bot_data['selection_messages'] = {}
        
        message = await update.message.reply_text(
            "Selecciona las estaciones para eliminar de favoritos:\n"
            "(Puedes seleccionar varias antes de guardar)",
            reply_markup=reply_markup
        )
        
        # Store the message info and initial selection state
        context.bot_data['selection_messages'][message.message_id] = {
            'type': 'remove',
            'selected': set(),
            'available': self.user_favorites[user_id].copy(),
            'user_id': user_id
        }

    async def send_html_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Send the interactive map HTML file"""
        # Get the appropriate message object based on update type
        message = update.message or update.callback_query.message
        
        try:
            # First run the GUI function to generate/update the HTML file
            await message.reply_text("🔄 Generando mapa interactivo...")
            gui()
            
            with open("rutas_interactivas.html", "rb") as f:
                await message.reply_document(
                    document=InputFile(f, filename="rutas_interactivas.html"),
                    caption="📄 Aquí tienes el mapa interactivo con todas las conexiones posibles."
                )
        except Exception as e:
            self.logger.error(f"Error sending HTML file: {e}")
            await message.reply_text("❌ Error al enviar el archivo.")

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show detailed help message with all available commands"""
        # Get the appropriate message object based on update type
        message = update.message or update.callback_query.message
        
        help_text = (
            "🚐 *Roadsurfer Rally Bot \\- Comandos Disponibles*\n\n"
            "*Comandos Principales:*\n"
            "🚀 /start \\- Iniciar el bot y ver menú principal\n"
            "🔄 /actualizar\\_rutas \\- Actualizar base de datos de rutas\n"
            "📊 /ver\\_rutas \\- Ver todas las rutas disponibles\n\n"
            "*Gestión de Favoritos:*\n"
            "⭐ /favoritos \\- Ver tus estaciones favoritas\n"
            "➕ /agregar\\_favorito \\- Añadir estación a favoritos\n"
            "➖ /eliminar\\_favorito \\- Eliminar estación de favoritos\n\n"
            "*Otras Funciones:*\n"
            "🗺️ /descargar\\_mapa \\- Descargar mapa interactivo\n"
            "❓ /help \\- Mostrar este mensaje de ayuda\n\n"
            "📱 *Tip:* Usa el menú de comandos de Telegram\n\n."
            "Creado por @arlloren \\"
        )
        try:
            await message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            # If markdown fails, try sending without formatting
            self.logger.error(f"Error sending help with markdown: {e}")
            plain_text = (
                "🚐 Roadsurfer Rally Bot - Comandos Disponibles\n\n"
                "Comandos Principales:\n"
                "🚀 /start - Iniciar el bot y ver menú principal\n"
                "🔄 /actualizar_rutas - Actualizar base de datos de rutas\n"
                "📊 /ver_rutas - Ver todas las rutas disponibles\n\n"
                "Gestión de Favoritos:\n"
                "⭐ /favoritos - Ver tus estaciones favoritas\n"
                "➕ /agregar_favorito - Añadir estación a favoritos\n"
                "➖ /eliminar_favorito - Eliminar estación de favoritos\n\n"
                "Otras Funciones:\n"
                "🗺️ /descargar_mapa - Descargar mapa interactivo\n"
                "❓ /help - Mostrar este mensaje de ayuda\n\n"
                "📱 Tip: Usa el menú de comandos de Telegram."
                "Creado por @arlloren"
            )
            await message.reply_text(plain_text)

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle callback queries from inline keyboard"""
        query = update.callback_query
        await query.answer()  # Answer the callback query to stop the loading state

        try:
            if query.data == "update_database":
                await self.update_database(update, context)
            elif query.data == "show_routes":
                await self.show_routes(update, context)
            elif query.data == "show_favorites":
                await self.show_favorites(update, context)
            elif query.data == "descargar_mapa":
                await self.send_html_file(update, context)
            elif query.data == "help":
                await self.help_command(update, context)
            elif query.data.startswith("toggle_add_") or query.data.startswith("toggle_remove_"):
                await self._handle_station_toggle(query, context)
            elif query.data == "save_favorites":
                await self._handle_save_favorites(query, context)
        except Exception as e:
            self.logger.error(f"Error handling callback {query.data}: {e}")
            try:
                await query.message.reply_text(
                    "❌ Lo siento, ha ocurrido un error. Por favor, intenta de nuevo."
                )
            except Exception as e2:
                self.logger.error(f"Error sending error message: {e2}")

    async def _handle_station_toggle(self, query: CallbackQuery, context: ContextTypes) -> None:
        """Handle toggling station selection"""
        if 'selection_messages' not in context.bot_data:
            await query.message.edit_text("❌ Sesión expirada. Por favor, inicia una nueva selección.")
            return

        message_data = context.bot_data['selection_messages'].get(query.message.message_id)
        if not message_data:
            await query.message.edit_text("❌ Sesión expirada. Por favor, inicia una nueva selección.")
            return

        station_name = query.data.replace("toggle_add_", "").replace("toggle_remove_", "")
        
        # Toggle selection
        if station_name in message_data['selected']:
            message_data['selected'].remove(station_name)
        else:
            message_data['selected'].add(station_name)

        # Rebuild keyboard with updated selection states
        keyboard = []
        row = []
        for station in sorted(message_data['available']):
            is_selected = station in message_data['selected']
            symbol = "★" if is_selected else "☆"
            prefix = "toggle_add_" if message_data['type'] == 'add' else "toggle_remove_"
            row.append(InlineKeyboardButton(
                f"{symbol} {station}", 
                callback_data=f"{prefix}{station}"
            ))
            if len(row) == 3:
                keyboard.append(row)
                row = []
        
        if row:
            keyboard.append(row)
        
        keyboard.append([InlineKeyboardButton("✅ Guardar Cambios", callback_data="save_favorites")])
        
        await query.message.edit_reply_markup(reply_markup=InlineKeyboardMarkup(keyboard))

    async def _handle_save_favorites(self, query: CallbackQuery, context: ContextTypes) -> None:
        """Handle saving the selected favorites"""
        if 'selection_messages' not in context.bot_data:
            await query.message.edit_text("❌ Sesión expirada. Por favor, inicia una nueva selección.")
            return

        message_data = context.bot_data['selection_messages'].get(query.message.message_id)
        if not message_data:
            await query.message.edit_text("❌ Sesión expirada. Por favor, inicia una nueva selección.")
            return

        user_id = message_data['user_id']
        selected = message_data['selected']
        
        if message_data['type'] == 'add':
            # Add selected stations to favorites
            if user_id not in self.user_favorites:
                self.user_favorites[user_id] = set()
            self.user_favorites[user_id].update(selected)
        else:  # remove
            # Remove selected stations from favorites
            if user_id in self.user_favorites:
                self.user_favorites[user_id].difference_update(selected)

        self._save_user_favorites()
        
        # Clean up the message data
        del context.bot_data['selection_messages'][query.message.message_id]
        
        # Show confirmation message
        action = "añadidas a" if message_data['type'] == 'add' else "eliminadas de"
        if selected:
            stations_list = "\n".join(f"• {station}" for station in sorted(selected))
            await query.message.edit_text(
                f"✅ Estaciones {action} favoritos:\n\n{stations_list}\n\n"
                "Usa /favoritos para ver tu lista completa."
            )
        else:
            await query.message.edit_text("ℹ️ No se realizaron cambios en tus favoritos.")

    @staticmethod
    def format_station_html(station: dict) -> str:
        """Format station information as HTML"""
        lines = [f"📦 <b>Origen</b>: <b>{station['origin']}</b>"]
        
        for ret in station.get("returns", []):
            lines.append(f"🔁 <b>Destino</b>: <b>{ret['destination']}</b>")
            for d in ret.get("available_dates", []):
                lines.append(f"📅 <code>{d['startDate']} → {d['endDate']}</code>")
        
        return "\n".join(lines)

    def run(self) -> None:
        """Run the bot"""
        self.logger.info("Starting bot...")
        self.application.run_polling()


if __name__ == "__main__":
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN not found in environment variables")
    bot = RoadsurferBot(BOT_TOKEN)
    bot.run()
