import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, BotCommand, CallbackQuery
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode
import json
import time
import signal
import asyncio
import sys
from datetime import datetime
from typing import Dict, List, Set
from pathlib import Path
from dotenv import load_dotenv
import os

#from api_utils import get_stations_data
#from data_utils import print_routes_for_stations, get_stations_with_returns, save_output_to_json
from gui import RouteMapGenerator
from data_fetcher import StationDataFetcher

# Initialize logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log')
    ]
)

load_dotenv()

#PORT = int(os.getenv('PORT', '10000'))  # Render uses port 10000 for free tier
#WEBHOOK_SECRET = os.getenv('WEBHOOK_SECRET', '')  # For webhook security
DEBUG_MODE = False

# Initialize Flask app for webhook
#flask_app = Flask(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.INFO)
#flask_app.logger.propagate = False

class RoadsurferBot:
    def __init__(self, token: str):
        self.token = token
        self.DB_PATH = Path("station_routes.json")
        self.FAVORITES_PATH = Path("user_favorites.json")
        self.NOTIFICATION_HISTORY_PATH = Path("notification_history.json")
        self.USER_NAMES = Path("user_names.json")
        self.ASSETS_FOLDER = Path("assets")
        self.UPDATE_COOLDOWN = 30 * 60  # 30 minutes in seconds
        self.UPDATE_COOLDOWN_LOCAL = 5 * 60 # 5 minutes in seconds
        self.last_update_time = 0
        

        self.logger = logging.getLogger(__name__)
        
        self.data_fetcher = StationDataFetcher(self.logger)
        
        # Load data
        self.stations_with_returns = self._load_stations()
        self.user_favorites = self._load_user_favorites()
        self.notification_history = self._load_notification_history()
        
        # Initialize application with job queue
        builder = ApplicationBuilder().token(self.token).concurrent_updates(True)        
        
        self.application = builder.build()
        
        # Setup handlers
        self._setup_handlers()
        
        # Setup auto-update job
        if self.application.job_queue:
            if DEBUG_MODE:
                self.logger.info("Skipping auto-update job in debug mode")
            else:
                self.application.job_queue.run_repeating(
                    self._job_update_database,
                    interval=self.UPDATE_COOLDOWN,
                    first=10,
                    name='database_update'
                )
                self.logger.info("Auto-update job scheduled successfully")
        else:
            self.logger.error("Job queue not available. Auto-updates will not work.")
            
    @staticmethod
    def create_progress_bar(progress: int, total: int = 100, length: int = 20) -> str:
        """Create a pretty progress bar with percentage"""
        filled_length = int(length * progress / total)
        bar = '█' * filled_length + '░' * (length - filled_length)
        percentage = f"{progress}%".rjust(4)
        return f"[{bar}] {percentage}"

    def _check_user_id_name(self, user_id: str) -> str:
        """Check if user ID is in database"""
        try:
            if not self.USER_NAMES.exists():
                return "No user names database found."
            
            with open(self.USER_NAMES, 'r') as f:
                user_names = json.load(f)
                if user_id in user_names:
                    return user_names[user_id]
                else:
                    self.user_idx = len(user_names) + 1
                    self.logger.warning(f"User ID {user_id} not found in user names database, using USER_{self.user_idx} as default.")
                    json.dump({user_id: f"USER_{self.user_idx}"}, self.USER_NAMES, indent=4)
                    
                    return f"USER_{self.user_idx}"
                    
        except Exception as e:
            self.logger.error(f"Error checking user ID {user_id}: {e}")
            return user_id
        

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

    def _load_notification_history(self) -> Dict[str, List[Dict]]:
        """Load notification history from JSON file"""
        try:
            with open(self.NOTIFICATION_HISTORY_PATH, 'r') as f:
                return json.load(f)
            
        except Exception as e:
            self.logger.error(f"Error loading notification history: {e}")
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

    def _save_notification_history(self) -> None:
        """Save notification history to JSON file"""
        try:
            with open(self.NOTIFICATION_HISTORY_PATH, 'w') as f:
                json.dump(self.notification_history, f, indent=4)
        except Exception as e:
            self.logger.error(f"Error saving notification history: {e}")

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
        self.application.add_handler(CommandHandler("agregar_favorito", self.add_favorite))
        self.application.add_handler(CommandHandler("eliminar_favorito", self.remove_favorite))
        self.application.add_handler(CommandHandler("descargar_mapa", self.send_html_file))
        self.application.add_handler(CommandHandler("last_update", self.check_last_update_time_time))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle the /start command"""
        self.logger.info(f"Received /start command from user {self._check_user_id_name(str(update.effective_user.id))}")
        
        # Set up commands when user starts the bot
        await self._setup_commands()
        
        user_name = self._check_user_id_name(str(update.effective_user.id))
        
        keyboard = [
            [InlineKeyboardButton("🚀 Actualizar base de datos", callback_data="update_database")],
            [InlineKeyboardButton("📊 Ver todas las rutas", callback_data="show_routes")],
            [InlineKeyboardButton("⭐ Ver favoritos", callback_data="show_favorites")],
            [InlineKeyboardButton("➕ Añadir estación favorita", callback_data="add_favorite")],
            [InlineKeyboardButton("➖ Eliminar estación favorita", callback_data="remove_favorite")],
            [InlineKeyboardButton("🗺️ Descargar mapa interactivo", callback_data="send_html_file")],
            [InlineKeyboardButton("❓ Ayuda", callback_data="help_command")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        sent_message = await update.message.reply_text(
            f"¡Bienvenido usuario {user_name} Bot de Roadsurfer Rally patrocinado \n"
            "por Arturo (@arlloren) the Machine! 🚐\n\n"
            "Aquí puedes:\n"
            "• Actualizar la base de datos de rutas: \n"
            "• Ver rutas disponibles\n"
            "• Gestionar (Añadir/eliminar/ver) estaciones favoritas\n"
            "• Descargar mapa interactivo con las rutas \n\n"
            "Para sugerencias sobre como mejorar el bot, contactame por telegram.\n\n"
            "Selecciona una opción:",
            reply_markup=reply_markup
        )

    async def update_database(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Update the stations database"""
        # Get the appropriate message object based on update type
        message = update.message or update.callback_query.message
        current_time = time.time()
        
        self.logger.info((f"Recibido request para actualizar rutas por el usuario"
                          f" '{self._check_user_id_name(str(update.effective_user.id))}' "))
        
        if current_time - self.last_update_time < self.UPDATE_COOLDOWN_LOCAL:
            remaining = int((self.UPDATE_COOLDOWN_LOCAL - (current_time - self.last_update_time)) // 60) + 1
            await message.reply_text(
                f"⚠️ La base de datos fue actualizada hace menos de {self.UPDATE_COOLDOWN_LOCAL // 60} minutos. "
                f"Por favor espera {remaining} minutos."
            )
            self.logger.info(f"Update request ignored. Last update was {current_time - self.last_update_time} seconds ago.")
            return

        status_message = await message.reply_text(
            "🔄 Iniciando actualización de rutas...\n" + self.create_progress_bar(0)
        )
        
        try:
            async def progress_callback(percent):
                progress_text = (
                    f"🔄 Actualizando base de datos de rutas...\n"
                    f"{self.create_progress_bar(percent)}\n"
                    f"Por favor espera..."
                )
                await status_message.edit_text(progress_text)
            
            self.data_fetcher.get_stations_data()
            
            await self.data_fetcher.get_stations_with_returns(progress_callback)
            if not self.data_fetcher.stations_with_returns:
                raise Exception("No se encontraron rutas disponibles")

            self.logger.info("Processing routes for stations...")
            output_data = self.data_fetcher.print_routes_for_stations()
            self.stations_with_returns = output_data
                        
            self.data_fetcher.save_output_to_json(self.DB_PATH)
            await self._check_new_routes(output_data, context)
            
            self.last_update_time = current_time
            
            final_message = (
                "✅ Base de datos actualizada\n"
                f"{self.create_progress_bar(100)}\n"
                f"📊 Se encontraron {len(self.stations_with_returns)} estaciones con rutas disponibles."
            )
            
            await status_message.edit_text(final_message)
            
        except Exception as e:
            self.logger.error(f"Error updating database: {e}", exc_info=True)
            error_message = (
                "❌ Error actualizando la base de datos.\n"
                "Por favor, intenta de nuevo más tarde."
            )
            await status_message.edit_text(error_message)

    def _is_new_route(self, user_id: str, station: Dict) -> bool:
        """Check if this route is new for the user"""
        if user_id not in self.notification_history:
            return True

        # Create unique identifiers for each origin-destination pair
        route_ids = []
        for ret in station.get('returns', []):
            route_id = f"{station['origin']}_{ret['destination']}"
            for date in ret.get('available_dates', []):
                route_id += f"_{date['startDate']}_{date['endDate']}"
            route_ids.append(route_id)

        # Check if any of these routes have been notified before
        notified_routes = self.notification_history[user_id]
        return not any(rid in notified_routes for rid in route_ids)

    def _mark_route_as_notified(self, user_id: str, station: Dict) -> None:
        """Mark a route as notified for a user"""
        if user_id not in self.notification_history:
            self.notification_history[user_id] = []

        # Create unique identifiers for each origin-destination pair
        for ret in station.get('returns', []):
            route_id = f"{station['origin']}_{ret['destination']}"
            for date in ret.get('available_dates', []):
                route_id += f"_{date['startDate']}_{date['endDate']}"
            
            # Add to notification history if not already there
            if route_id not in self.notification_history[user_id]:
                self.notification_history[user_id].append(route_id)
                
        self._save_notification_history()

    async def _check_new_routes(self, new_stations: List[Dict], context: ContextTypes.DEFAULT_TYPE) -> None:
        """Check for new routes matching users' favorite stations (both as origin and destination)"""
        for user_id, favorite_stations in self.user_favorites.items():
            for station in new_stations:
                # Check if origin is in favorites
                if station['origin'] in favorite_stations:
                    # Check each destination separately
                    for ret in station.get('returns', []):
                        single_route = {
                            'origin': station['origin'],
                            'returns': [ret]  # Only include this specific destination
                        }
                        if self._is_new_route(user_id, single_route):
                            await self._notify_user(user_id, single_route, context, is_origin=True)
                            self._mark_route_as_notified(user_id, single_route)
                
                # Check if any destination is in favorites
                for ret in station.get('returns', []):
                    if ret['destination'] in favorite_stations:
                        # For destination matches, create a simplified route with just this destination
                        matching_route = {
                            'origin': station['origin'],
                            'returns': [{
                                'destination': ret['destination'],
                                'available_dates': ret['available_dates']
                            }]
                        }
                        if self._is_new_route(user_id, matching_route):
                            await self._notify_user(user_id, matching_route, context, is_origin=False)
                            self._mark_route_as_notified(user_id, matching_route)

    async def _notify_user(self, user_id: str, station: Dict, context: ContextTypes.DEFAULT_TYPE, is_origin: bool = True) -> None:
        """Send notification to user about new routes"""
        try:
            if is_origin:
                message = f"🔔 ¡Nueva ruta disponible desde tu estación favorita {station['origin']}!\n\n"
                # For origin notifications, use the standard format
                msg_output, image_path = self.format_station_html(station)
                message += msg_output
            else:
                # For destination notifications, we know there's exactly one matching destination
                dest = station['returns'][0]['destination']
                message = f"🔔 ¡Nueva ruta disponible hacia tu estación favorita {dest}!\n\n"
                message += f"📦 <b>Origen</b>: <b>{station['origin']}</b>\n"
                message += f"🔁 <b>Destino</b>: <b>{dest}</b>\n"
                for d in station['returns'][0]['available_dates']:
                    message += f"📅 <code>{d['startDate']} → {d['endDate']}</code>\n"

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
            msg, image_path = self.format_station_html(station)
            await message.reply_text(msg, parse_mode=ParseMode.HTML)
            await self.send_jpeg_file(update, context, image_path=image_path)
            
        self.logger.info(f"Sent {len(self.stations_with_returns)} routes to user {self._check_user_id_name(str(update.effective_user.id))}")

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
        
        self.logger.info(f"Sent favorites to user '{self._check_user_id_name(user_id)}'")

    async def add_favorite(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Add stations to favorites using a grid interface"""
        user_id = str(update.effective_user.id)
        
        # Initialize user favorites if needed
        if user_id not in self.user_favorites:
            self.user_favorites[user_id] = set()
            
            
        try:
            # Attempt to get stations from the data fetcher
            if self.data_fetcher.valid_stations:
                all_stations = sorted(self.data_fetcher.valid_stations)
            else:
                raise ValueError("No valid stations in data fetcher.")
        except Exception as e:
            self.logger.info(f"Error loading valid stations from data fetcher: {e}. Trying to load from cache.")
            try:
                # Fallback: Load stations from geocode_cache.json
                with open("geocode_cache.json", "r", encoding="utf-8") as f:
                    all_stations = sorted(json.load(f).keys())
            except Exception as e2:
                self.logger.error(f"Error loading geocode cache: {e2}")
                await update.message.reply_text("❌ Error al cargar la lista de estaciones.")
                return

        # Filter out stations that are already in favorites
        available_stations = [s for s in all_stations if s not in self.user_favorites[user_id]]

        if not available_stations:
            await update.message.reply_text("Ya tienes todas las estaciones en favoritos.")
            self.logger.info(f"No available stations to add for user '{self._check_user_id_name(user_id)}'")
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
        
        self.logger.info(f"Displayed add favorite grid for user '{self._check_user_id_name(user_id)}'")

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
        
        self.logger.info(f"Displayed remove favorite grid for user '{self._check_user_id_name(user_id)}'")

    async def send_html_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Send the interactive map HTML file"""
        # Get the appropriate message object based on update type
        message = update.message or update.callback_query.message
        
        try:
            if not self.stations_with_returns:
                await message.reply_text(
                    "No hay rutas disponibles. Usa /actualizar_rutas primero."
                )
                return    
            map_generator = RouteMapGenerator(self.logger)
            map_generator.generate_map()
            
            with open("rutas_interactivas.html", "rb") as f:
                await message.reply_document(
                    document=InputFile(f, filename="rutas_interactivas.html"),
                    caption="📄 Aquí tienes el mapa interactivo con todas las conexiones posibles."
                )
        except Exception as e:
            self.logger.error(f"Error sending HTML file: {e}")
            await message.reply_text("❌ Error al enviar el archivo.")
            
        self.logger.info(f"Sent interactive map to user {self._check_user_id_name(str(update.effective_user.id))}")
        
    async def send_jpeg_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE, image_path: str = r"C:\Users\llorenteanaa\Documents\rally_bot\assets\phpBJKKBh_small.jpeg") -> None:
        """Send the JPEG image of the map"""
        # Get the appropriate message object based on update type
        message = update.message or update.callback_query.message
        
        try:
            
            with open(image_path, "rb") as f:
                await message.reply_photo(
                    photo=InputFile(f, filename="image_path", ),
                )
        except Exception as e:
            self.logger.error(f"Error sending JPEG file: {e}")
            await message.reply_text("❌ Error al enviar la imagen.")
            
        self.logger.info(f"Sent interactive map image to user {self._check_user_id_name(str(update.effective_user.id))}")
        
    async def check_last_update_time(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show the last update time of the database"""
        # Get the appropriate message object based on update type
        message = update.message or update.callback_query.message
        
        if self.last_update_time == 0:
            await message.reply_text("La base de datos aún no ha sido actualizada.")
            return
        
        last_update = datetime.fromtimestamp(self.last_update_time).strftime('%Y-%m-%d %H:%M:%S')
        await message.reply_text(f"La base de datos fue actualizada por última vez el {last_update}.")
        
        self.logger.info(f"Last update time sent to user {self._check_user_id_name(str(update.effective_user.id))}")

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show detailed help message with all available commands"""
        # Get the appropriate message object based on update type
        message = update.message or update.callback_query.message
        
        help_text = (       
            "❓ *Ayuda del Bot de Roadsurfer Rally*\n\n"
            "Este bot te permite estar al día con las rutas de Roadsurfer Rally\\.\n\n"
            "Para errores, dudas o sugerencias sobre cómo mejorar el bot, contáctame por Telegram a @arlloren, "
            "escríbeme por LinkedIn [arturo\\-llorente](https://www.linkedin.com/in/arturo-llorente/) "
            "o, si pilotas de GitHub y quieres ayudar a mejorar este bot, crea una PR en mi repo público "
            "[rally\\_bot](https://github.com/ArturoLlorente/rally_bot)\\.\n\n"
        )

        self.logger.info(f"Sent help message to user {self._check_user_id_name(str(update.effective_user.id))}")
        
        await message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN_V2)
        


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

    def format_station_html(self, station: dict) -> str:
        """Format station information as HTML"""
        lines = [f"📦 <b>Origen</b>: <b>{station['origin']}</b>"]
        
        for ret in station.get("returns", []):
            lines.append(f"🔁 <b>Destino</b>: <b>{ret['destination']}</b>")
            for d in ret.get("available_dates", []):
                lines.append(f"📅 <code>{d['startDate']} → {d['endDate']}</code>")
            
            lines.append(f"🚐 <code>{ret.get('model_name', 'Modelo desconocido')}</code>")
            image_path = os.path.join(self.ASSETS_FOLDER, ret.get("model_image"))
        
        return "\n".join(lines), image_path

    async def _job_update_database(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Job to automatically update the database"""
        try:
            current_time = time.time()
            self.logger.info("Starting automatic database update...")
            
            # Get new data
            self.data_fetcher.get_stations_data()
                        
            # Process stations
            async def progress_callback(percent):
                if percent % 10 == 0:
                    self.logger.info(f"Auto-update progress: {percent}%")
            
            await self.data_fetcher.get_stations_with_returns(progress_callback)
            if not self.data_fetcher.stations_with_returns:
                raise Exception("No se encontraron rutas disponibles")
            
            # Process and save routes
            output_data = self.data_fetcher.print_routes_for_stations()
            # Update stations data
            self.stations_with_returns = output_data
            
            
            self.data_fetcher.save_output_to_json(self.DB_PATH)
            
            # Update timestamp
            self.last_update_time = time.time()
            
            self.logger.info(
                "✅ Base de datos actualizada automaticamente\n"
                f"{self.create_progress_bar(100)}\n"
                f"📊 Se encontraron {len(self.stations_with_returns)} estaciones con rutas disponibles."
            )
            
            # Check for new routes and notify users
            await self._check_new_routes(output_data, context)
            
            self.last_update_time = current_time
            
        except Exception as e:
            self.logger.error(f"Error in auto-update job: {e}", exc_info=True)
            
    async def notify_all_users(self, message: str):
            """Send a message to all users in user_favorites"""
            for user_id in json.load(self.USER_NAMES).keys():
                try:
                    await self.application.bot.send_message(chat_id=user_id, text=message)
                except Exception as e:
                    self.logger.error(f"Error notifying user {user_id}: {e}")

    def run(self) -> None:
        """Run the bot"""
        self.logger.info("Starting bot...")
        try:
            # Run with polling in local environment
            self.logger.info("Starting polling...")
            self.application.run_polling(drop_pending_updates=True)
        except Exception as e:
            self.logger.error(f"Error running bot: {e}", exc_info=True)
            raise

# Webhook endpoint
#@flask_app.route("/webhook", methods=["POST"])
#async def webhook_handler():
#    """Handle incoming webhook requests"""
#    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
#        return {"ok": False, "error": "Invalid token"}, 403
#
#    try:
#        await bot.application.update_queue.put(Update.de_json(request.get_json(), bot.application.bot))
#        return {"ok": True, "error": ""}, 200
#    except Exception as e:
#        logging.error(f"Error processing update: {e}")
#        return {"ok": False, "error": str(e)}, 500

if __name__ == "__main__":
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN not found in environment variables")
    
    try:
        bot = RoadsurferBot(BOT_TOKEN)
        def handle_sigint(signal_received, frame):
            print("SIGINT or CTRL-C detected. Notifying users and shutting down...")
            try:
                asyncio.run(bot.notify_all_users("⚠️ El bot está en mantenimiento hasta nuevo aviso."))
            except Exception as e:
                print(f"Error notifying users: {e}")
            sys.exit(0)
        
        bot.run()
        asyncio.run(bot.notify_all_users("✅ El bot está nuevamente en línea. ¡Ya puedes usarlo!"))
        signal.signal(signal.SIGINT, handle_sigint)
    except Exception as e:
        logging.error(f"Error starting bot: {e}", exc_info=True)
