import logging
from logging.handlers import RotatingFileHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, BotCommand, CallbackQuery
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, ContextTypes
from telegram.constants import ParseMode
import json
import time
import signal
import asyncio
from concurrent.futures import ThreadPoolExecutor
import sys
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Set
from pathlib import Path
from dotenv import load_dotenv
import os
from telegram_bot_calendar import DetailedTelegramCalendar

#from api_utils import get_stations_data
#from data_utils import print_routes_for_stations, get_stations_with_returns, save_output_to_json
from gui import RouteMapGenerator
from data_fetcher import StationDataFetcher, ImoovaDataFetcher, IndieCampersDataFetcher
import requests


load_dotenv()
DEBUG_MODE = False


async def _safe_edit(message, text: str) -> None:
    """Edit a Telegram message, silently ignoring 'not modified' errors."""
    try:
        await message.edit_text(text)
    except Exception as e:
        if "Message is not modified" not in str(e):
            pass  # swallow other transient errors from background thread dispatches

#class TelegramLogHandler(logging.Handler):
#    def __init__(self, bot_token: str, chat_id: str):
#        """
#        Custom logging handler to send log messages to a Telegram chat.
#
#        :param bot_token: Telegram bot token.
#        :param chat_id: Chat ID where logs will be sent.
#        """
#        super().__init__()
#        self.bot_token = bot_token
#        self.chat_id = chat_id
#        self.api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
#
#    def emit(self, record):
#        """
#        Sends a log message to the specified Telegram chat.
#        """
#        try:
#            log_entry = self.format(record)
#            payload = {"chat_id": self.chat_id, "text": log_entry}
#            requests.post(self.api_url, json=payload)
#        except Exception as e:
#            print(f"Failed to send log to Telegram: {e}")
            

class RoadsurferBot:
    def __init__(self, token: str, logger_token: str = None):
        self.token = token
        self.logger_token = logger_token
        self.db_path = Path("station_routes.json")
        self.favorites_path = Path("user_favorites.json")
        self.notification_history_path = Path("notification_history.json")
        self.date_filters_path = Path("user_date_filters.json")
        self.assets_folder = Path("assets")
        self.update_cooldown = 30 * 60  # in seconds
        self.trigger_update_cooldown = 5 * 60 # in seconds
        self.last_update_time = 0

        # Thread pool (1 worker so only one DB update runs at a time)
        self._update_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='db_update')
        # asyncio lock – prevents a manual trigger overlapping the auto-update
        self._update_lock = asyncio.Lock()
        self._is_updating = False
        

        self.logger = logging.getLogger(__name__)
        
        self.data_fetcher = StationDataFetcher(self.logger)
        self.imoova_fetcher = ImoovaDataFetcher(self.logger)
        self.indie_campers_fetcher = IndieCampersDataFetcher(self.logger)
        
        # Load data
        self.stations_with_returns = self._load_stations()
        self.user_favorites = self._load_user_favorites()
        self.user_date_filters = self._load_date_filters()
        self.notification_history = self._load_notification_history()
        
        # Initialize application with job queue
        builder = ApplicationBuilder().token(self.token).concurrent_updates(True)        
        
        self.application = builder.build()
        
        # Setup handlers
        self._setup_handlers()
        
        # Setup auto-update job (runs continuously: reschedules itself after each run)
        if self.application.job_queue:
            if DEBUG_MODE:
                self.logger.info("Skipping auto-update job in debug mode")
            else:
                self.application.job_queue.run_once(
                    self._job_update_database,
                    when=10,
                    name='database_update'
                )
                self.logger.info("Auto-update job scheduled (continuous mode)")
        else:
            self.logger.error("Job queue not available. Auto-updates will not work.")
            
    @staticmethod
    def create_progress_bar(progress: int, total: int = 100, length: int = 20) -> str:
        """Create a pretty progress bar with percentage"""
        filled_length = int(length * progress / total)
        bar = '█' * filled_length + '░' * (length - filled_length)
        percentage = f"{progress}%".rjust(4)
        return f"[{bar}] {percentage}"

    def _load_stations(self) -> List[Dict]:
        """Load stations data from JSON file"""
        try:
            if self.db_path.exists():
                with open(self.db_path, 'r') as f:
                    return json.load(f)
            return []
        except Exception as e:
            self.logger.error(f"Error loading stations: {e}")
            return []

    def _load_user_favorites(self) -> Dict[str, Set[str]]:
        """Load user favorites from JSON file"""
        try:
            if self.favorites_path.exists():
                with open(self.favorites_path, 'r') as f:
                    # Convert lists back to sets
                    data = json.load(f)
                    return {user_id: set(stations) for user_id, stations in data.items()}
            return {}
        except Exception as e:
            self.logger.error(f"Error loading favorites: {e}")
            return {}
        
    def _load_date_filters(self) -> Dict[str, List]:
        """Load user date filters from JSON file.
        Format: {user_id: [{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}, ...]}
        """
        try:
            if self.date_filters_path.exists():
                with open(self.date_filters_path, 'r') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            self.logger.error(f"Error loading date filters: {e}")
            return {}

    def _save_date_filters(self) -> None:
        """Persist user date filters to JSON file"""
        try:
            with open(self.date_filters_path, 'w') as f:
                json.dump(self.user_date_filters, f, indent=4)
        except Exception as e:
            self.logger.error(f"Error saving date filters: {e}")

    def _route_passes_date_filter(self, user_id: str, ret: Dict) -> bool:
        """Return True if the route's dates overlap with any user-configured range.
        If the user has no filters set, all routes pass."""
        ranges = self.user_date_filters.get(user_id, [])
        if not ranges:
            return True  # No filter → everything passes

        for date_entry in ret.get('available_dates', []):
            try:
                route_start = datetime.strptime(date_entry['startDate'], "%d/%m/%Y")
                route_end   = datetime.strptime(date_entry['endDate'],   "%d/%m/%Y")
            except (ValueError, KeyError):
                continue

            for r in ranges:
                try:
                    f_start = datetime.strptime(r['start'], "%Y-%m-%d")
                    f_end   = datetime.strptime(r['end'],   "%Y-%m-%d")
                except (ValueError, KeyError):
                    continue
                # Overlap when route starts before filter ends AND route ends after filter starts
                if route_start <= f_end and route_end >= f_start:
                    return True

        return False

    def _load_notification_history(self) -> Dict[str, List[Dict]]:
        """Load notification history from JSON file"""
        try:
            with open(self.notification_history_path, 'r') as f:
                return json.load(f)
            
        except Exception as e:
            self.logger.error(f"Error loading notification history: {e}")
            return {}


    async def _setup_commands(self) -> None:
        """Set up the bot commands in Telegram"""
        commands = [
            BotCommand("start", "🚀 Iniciar el bot"),
            BotCommand("ver_rutas", "📊 Ver todas las rutas disponibles"),
            BotCommand("favoritos", "⭐ Ver tus estaciones favoritas"),
            BotCommand("agregar_favorito", "➕ Añadir estación favorita"),
            BotCommand("eliminar_favorito", "➖ Eliminar estación favorita"),
            BotCommand("send_json", "📄 Descargar archivo JSON con todas las rutas"),
            BotCommand("descargar_mapa", "🗺️ Descargar mapa interactivo"),
            BotCommand("check_new_routes", "🔔 Comprobar nuevas rutas para tus favoritos"),
            BotCommand("set_date_filter", "🗓️ Configurar filtros de fecha para notificaciones"),
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
        self.application.add_handler(CommandHandler("ver_rutas", self.show_routes))
        self.application.add_handler(CommandHandler("favoritos", self.show_favorites))
        self.application.add_handler(CommandHandler("agregar_favorito", self.add_favorite))
        self.application.add_handler(CommandHandler("eliminar_favorito", self.remove_favorite))
        self.application.add_handler(CommandHandler("descargar_mapa", self.send_html_file))
        self.application.add_handler(CommandHandler("send_json", self.send_json_file))
        self.application.add_handler(CommandHandler("check_new_routes", self._check_deleted_routes))
        self.application.add_handler(CommandHandler("set_date_filter", self.set_date_filter))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CallbackQueryHandler(self.handle_callback))

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle the /start command"""
        self.logger.info(f"Received /start command from user {update.effective_user.first_name} (ID: {update.effective_user.id})")
        
        # Set up commands when user starts the bot
        await self._setup_commands()
                
        keyboard = [
            [InlineKeyboardButton(" Ver todas las rutas", callback_data="show_routes")],
            [InlineKeyboardButton("⭐ Ver favoritos", callback_data="show_favorites")],
            [InlineKeyboardButton("➕ Añadir estación favorita", callback_data="add_favorite")],
            [InlineKeyboardButton("➖ Eliminar estación favorita", callback_data="remove_favorite")],
            [InlineKeyboardButton("🗓️ Configurar filtros de fecha", callback_data="set_date_filter")],
            [InlineKeyboardButton("🗺️ Descargar mapa interactivo", callback_data="send_html_file")],
            [InlineKeyboardButton("❓ Ayuda", callback_data="help_command")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        sent_message = await update.message.reply_text(
            f"¡Bienvenido usuario {update.effective_user.first_name} Bot de Roadsurfer Rally patrocinado \n"
            "por Arturo (@arlloren) the Machine! 🚐\n\n"
            "Aquí puedes:\n"
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
                          f" {update.effective_user.first_name}, (ID: {update.effective_user.id})"))

        # If a background update is already running, just inform the user
        if self._is_updating:
            await message.reply_text(
                "🔄 La base de datos se está actualizando en segundo plano ahora mismo.\n"
                "Recibirás notificaciones en cuanto se descubran nuevas rutas.\n"
                "Recibirás notificaciones en cuanto se descubran nuevas rutas."
            )
            return
        
        if current_time - self.last_update_time < self.trigger_update_cooldown:
            remaining = int((self.trigger_update_cooldown - (current_time - self.last_update_time)) // 60) + 1
            await message.reply_text(
                f"⚠️ La base de datos fue actualizada hace menos de {self.trigger_update_cooldown // 60} minutos. "
                f"Por favor espera {remaining} minutos."
            )
            self.logger.info(f"Update request ignored. Last update was {current_time - self.last_update_time} seconds ago.")
            return

        async with self._update_lock:
            self._is_updating = True
            status_message = await message.reply_text(
                "🔄 Iniciando actualización de rutas...\n" + self.create_progress_bar(0)
            )

            try:
                loop = asyncio.get_event_loop()

                # ---- Build thread-safe callbacks --------------------------------
                last_percent = {'value': -1}

                def sync_progress_callback(percent: int):
                    if percent == last_percent['value']:
                        return
                    last_percent['value'] = percent
                    progress_text = (
                        f"🔄 Actualizando base de datos de rutas...\n"
                        f"{self.create_progress_bar(percent)}\n"
                        f"Por favor espera..."
                    )
                    asyncio.run_coroutine_threadsafe(
                        _safe_edit(status_message, progress_text),
                        loop
                    )

                def sync_route_callback(route_data: Dict):
                    asyncio.run_coroutine_threadsafe(
                        self._check_and_notify_route(route_data, context),
                        loop
                    )

                # ---- Fetch imoova relocations (first) ----
                try:
                    await _safe_edit(status_message, "🔄 Obteniendo rutas de Imoova...")
                except Exception:
                    pass

                imoova_data = await loop.run_in_executor(
                    self._update_executor,
                    lambda: self.imoova_fetcher.sync_full_update(
                        route_callback=sync_route_callback,
                    )
                )

                # Save after Imoova
                merged = list(imoova_data or [])
                self.stations_with_returns = merged
                self.data_fetcher.output_data = merged
                self.data_fetcher.save_output_to_json(self.db_path)

                # ---- Fetch Indie Campers deals ----
                try:
                    await _safe_edit(status_message, "🔄 Obteniendo rutas de Indie Campers...")
                except Exception:
                    pass

                indie_data = await loop.run_in_executor(
                    self._update_executor,
                    lambda: self.indie_campers_fetcher.sync_full_update(
                        route_callback=sync_route_callback,
                    )
                )

                # Save after Indie Campers
                merged = merged + (indie_data or [])
                self.stations_with_returns = merged
                self.data_fetcher.output_data = merged
                self.data_fetcher.save_output_to_json(self.db_path)

                # ---- Fetch Roadsurfer routes ----
                output_data = await loop.run_in_executor(
                    self._update_executor,
                    lambda: self.data_fetcher.sync_full_update(
                        progress_callback=sync_progress_callback,
                        route_callback=sync_route_callback,
                    )
                )

                # Final merge and save
                merged = merged + (output_data or [])
                if not merged:
                    raise Exception("No se encontraron rutas disponibles")

                # ---- Fetching complete ----------------------------------------
                try:
                    await status_message.edit_text(
                        "✅ Estaciones obtenidas\n"
                        f"{self.create_progress_bar(100)}\n"
                        "🔍 Guardando y enviando notificaciones..."
                    )
                except Exception:
                    pass

                self.stations_with_returns = merged
                self.data_fetcher.output_data = merged
                self.data_fetcher.save_output_to_json(self.db_path)

                current_stations = self._load_stations()
                if current_stations:
                    await self._check_deleted_routes(current_stations, context)

                self.last_update_time = current_time

                await status_message.edit_text(
                    "✅ Actualización completada\n"
                    f"{self.create_progress_bar(100)}\n"
                    f"📊 Se encontraron {len(self.stations_with_returns)} estaciones con rutas disponibles."
                )

            except Exception as e:
                self.logger.error(f"Error updating database: {e}", exc_info=True)
                await status_message.edit_text(
                    "❌ Error actualizando la base de datos.\n"
                    "Por favor, intenta de nuevo más tarde."
                )
            finally:
                self._is_updating = False

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
                
        try:
            with open(self.notification_history_path, 'w') as f:
                json.dump(self.notification_history, f, indent=4)
        except Exception as e:
            self.logger.error(f"Error saving notification history: {e}")

    async def _check_and_notify_route(self, route: Dict, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Check if a single route matches any user favorites and notify immediately
        
        Args:
            route: Single route data with format {'origin': str, 'origin_address': str, 'returns': [dict]}
            context: Telegram context for sending messages
        """
        try:
            origin = route.get('origin')
            if not origin or not route.get('returns'):
                return
            
            self.logger.debug(f"Checking route: {origin} -> {route.get('returns', [{}])[0].get('destination') if route.get('returns') else 'N/A'}")
            
            # Check all users for matching favorites
            for user_id, favorite_stations in self.user_favorites.items():
                # Check if origin is in favorites
                if origin in favorite_stations:
                    # Filter returns by date
                    filtered_returns = [r for r in route.get('returns', []) if self._route_passes_date_filter(user_id, r)]
                    if filtered_returns:
                        filtered_route = {**route, 'returns': filtered_returns}
                        if self._is_new_route(user_id, filtered_route):
                            self.logger.info(f"Sending notification to user {user_id} for new route from {origin}")
                            sent = await self._notify_user(user_id, filtered_route, context, is_origin=True)
                            if sent:
                                self._mark_route_as_notified(user_id, filtered_route)
                        else:
                            self.logger.debug(f"Route from {origin} already notified to user {user_id}")
                
                # Check if destination is in favorites
                for ret in route.get('returns', []):
                    destination = ret.get('destination')
                    if destination and destination in favorite_stations:
                        if not self._route_passes_date_filter(user_id, ret):
                            self.logger.debug(f"Route to {destination} filtered out by date filter for user {user_id}")
                            continue
                        # Create route data for this specific destination match
                        dest_route = {
                            'origin': origin,
                            'origin_address': route.get('origin_address'),
                            'returns': [ret]
                        }
                        if self._is_new_route(user_id, dest_route):
                            self.logger.info(f"Sending notification to user {user_id} for new route to {destination}")
                            sent = await self._notify_user(user_id, dest_route, context, is_origin=False)
                            if sent:
                                self._mark_route_as_notified(user_id, dest_route)
                        else:
                            self.logger.debug(f"Route to {destination} already notified to user {user_id}")
        
        except Exception as e:
            self.logger.error(f"Error checking route for notifications: {e}", exc_info=True)

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
                            sent = await self._notify_user(user_id, single_route, context, is_origin=True)
                            if sent:
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
                            sent = await self._notify_user(user_id, matching_route, context, is_origin=False)
                            if sent:
                                self._mark_route_as_notified(user_id, matching_route)
        
        # After checking all users, check for deleted routes
        current_stations = self._load_stations()
        if current_stations:
            await self._check_deleted_routes(current_stations, context)

    async def _check_deleted_routes(self, current_stations, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Check for routes that have been deleted and notify users."""
        
        # Create a set of all currently available route IDs
        current_route_ids = set()
        for station in current_stations:
            origin = station['origin']
            for ret in station.get('returns', []):
                route_id = f"{origin}_{ret['destination']}"
                for date in ret.get('available_dates', []):
                    route_id += f"_{date['startDate']}_{date['endDate']}"
                current_route_ids.add(route_id)

        # Compare with the notification history
        for user_id, notified_routes in self.notification_history.items():
            self.notification_history[user_id] = [
                route for route in notified_routes if route in current_route_ids
            ]
            
        
        self.logger.info(f"Updated notification history for {self.notification_history} users.")

        try:
            with open(self.notification_history_path, 'w') as f:
                json.dump(self.notification_history, f, indent=4)
        except Exception as e:
            self.logger.error(f"Error saving notification history: {e}")


    async def _notify_user(self, user_id: str, station: Dict, context: ContextTypes.DEFAULT_TYPE, is_origin: bool = True) -> bool:
        """Send notification to user about new routes using the same format as show_routes.
        Returns True if the message was delivered, False otherwise."""
        try:
            self.logger.info(f"Preparing notification for user {user_id}: {station.get('origin')} -> {station.get('returns', [{}])[0].get('destination') if station.get('returns') else 'N/A'}")
            msg, image_path = self.format_station_html(station)
            self.logger.info(f"Sending photo to user {user_id} with image: {image_path}")
            success = await self.send_jpeg_file(update=None, context=context, image_path=image_path, msg=msg, user_id=user_id)
            if success:
                self.logger.info(f"✅ Successfully sent notification to user {user_id}")
            else:
                self.logger.warning(f"⚠️ Failed to deliver notification to user {user_id}")
            # Rate-limit: wait between notifications to avoid Telegram flood control
            await asyncio.sleep(1.5)
            return success
        except Exception as e:
            self.logger.error(f"Error sending notification to {user_id}: {e}", exc_info=True)
            return False

    async def show_routes(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show all available routes"""
        # Get the appropriate message object based on update type
        message = update.message or update.callback_query.message
        
        if not self.stations_with_returns:
            await message.reply_text(
                "No hay rutas disponibles. La base de datos se actualiza automáticamente."
            )
            return

        if self._is_updating:
            await message.reply_text(
                "ℹ️ La base de datos se está actualizando en segundo plano. "
                "Mostrando los datos más recientes disponibles."
            )

        for station in self.stations_with_returns:
            msg, image_path = self.format_station_html(station)
            #await message.reply_text(msg, parse_mode=ParseMode.HTML)
            await self.send_jpeg_file(update, context, image_path=image_path, msg=msg)
            
        self.logger.info(f"Sent {len(self.stations_with_returns)} routes to user {update.effective_user.first_name} (ID: {update.effective_user.id})")

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
        
        self.logger.info(f"Sent favorites to user {update.effective_user.first_name} (ID: {update.effective_user.id})")

    async def add_favorite(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Add stations to favorites using a grid interface"""
        user_id = str(update.effective_user.id)
        # Support both command and inline-keyboard (callback) invocations
        reply_message = update.message or update.callback_query.message
        
        # Initialize user favorites if needed
        if user_id not in self.user_favorites:
            self.user_favorites[user_id] = set()
            
            
        try:
            # Attempt to get stations from the data fetcher
            if self.data_fetcher.valid_stations:
                # Extract station names from the valid_stations dictionaries
                all_stations = sorted([station.get('name') for station in self.data_fetcher.valid_stations if station.get('name')])
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
                await reply_message.reply_text("❌ Error al cargar la lista de estaciones.")
                return

        # Filter out stations that are already in favorites
        available_stations = [s for s in all_stations if s not in self.user_favorites[user_id]]

        if not available_stations:
            await reply_message.reply_text("Ya tienes todas las estaciones en favoritos.")
            self.logger.info(f"No available stations to add for user {update.effective_user.first_name} (ID: {user_id})")
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
        
        message = await reply_message.reply_text(
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
        
        self.logger.info(f"Displayed add favorite grid for user {update.effective_user.first_name}, (ID: {user_id})")

    async def remove_favorite(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Remove stations from favorites using a grid interface"""
        user_id = str(update.effective_user.id)
        # Support both command and inline-keyboard (callback) invocations
        reply_message = update.message or update.callback_query.message
        
        if user_id not in self.user_favorites or not self.user_favorites[user_id]:
            await reply_message.reply_text(
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
        
        message = await reply_message.reply_text(
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
        
        self.logger.info(f"Displayed remove favorite grid for user {update.effective_user.first_name}, (ID: {user_id})")

    async def set_date_filter(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show the date filter management menu"""
        user_id = str(update.effective_user.id)
        if update.callback_query:
            await self._show_date_filter_menu(update.callback_query.message, user_id, edit=True)
        else:
            await self._show_date_filter_menu(update.message, user_id, edit=False)

    async def _show_date_filter_menu(self, message, user_id: str, edit: bool = True) -> None:
        """Build and send/edit the date filter menu showing all current ranges"""
        ranges = self.user_date_filters.get(user_id, [])
        
        text = "🗓️ <b>Filtros de fecha para notificaciones</b>\n"
        text += "Solo recibirás notificaciones de rutas cuyas fechas coincidan con algún rango.\n"
        
        keyboard = []
        
        if ranges:
            text += "\n<b>Rangos activos:</b>\n"
            for i, r in enumerate(ranges):
                try:
                    start_display = datetime.strptime(r['start'], "%Y-%m-%d").strftime("%d/%m/%Y")
                    end_display   = datetime.strptime(r['end'],   "%Y-%m-%d").strftime("%d/%m/%Y")
                except (ValueError, KeyError):
                    start_display, end_display = r.get('start', '?'), r.get('end', '?')
                text += f"  {i+1}. {start_display} → {end_display}\n"
                keyboard.append([
                    InlineKeyboardButton(f"🗑️ Eliminar {start_display} → {end_display}", callback_data=f"date_delete_{i}")
                ])
            keyboard.append([InlineKeyboardButton("❌ Eliminar todos", callback_data="date_clear")])
        else:
            text += "\n<i>Sin filtros — recibirás notificaciones de todas las fechas.</i>\n"
        
        keyboard.append([InlineKeyboardButton("➕ Añadir rango", callback_data="date_add")])
        
        markup = InlineKeyboardMarkup(keyboard)
        if edit:
            await message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        else:
            await message.reply_text(text, reply_markup=markup, parse_mode="HTML")

    async def handle_date_filter(self, query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle date filter callbacks (date_add, date_clear, date_delete_N)"""
        user_id = str(query.from_user.id)
        action = query.data  # e.g. "date_add", "date_clear", "date_delete_0"

        if action == "date_clear":
            self.user_date_filters.pop(user_id, None)
            self._save_date_filters()
            await self._show_date_filter_menu(query.message, user_id, edit=True)
            return

        if action.startswith("date_delete_"):
            try:
                idx = int(action.split("_")[-1])
                ranges = self.user_date_filters.get(user_id, [])
                if 0 <= idx < len(ranges):
                    ranges.pop(idx)
                    if ranges:
                        self.user_date_filters[user_id] = ranges
                    else:
                        self.user_date_filters.pop(user_id, None)
                    self._save_date_filters()
            except (ValueError, IndexError):
                pass
            await self._show_date_filter_menu(query.message, user_id, edit=True)
            return

        if action == "date_add":
            # Kick off start-date calendar
            context.user_data['date_step'] = 'start'
            context.user_data.pop('date_start', None)
            calendar, _ = DetailedTelegramCalendar(min_date=datetime.now().date()).build()
            await query.message.edit_text(
                "📅 Selecciona la <b>fecha de inicio</b> del rango:",
                reply_markup=calendar,
                parse_mode="HTML"
            )

    async def handle_calendar_selection(self, query: CallbackQuery, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle calendar date selection for date-range building"""
        user_id = str(query.from_user.id)
        step = context.user_data.get('date_step', 'start')

        result, key, _ = DetailedTelegramCalendar(min_date=datetime.now().date()).process(query.data)

        if not result and key:
            # Still navigating the calendar
            label = "inicio" if step == 'start' else "fin"
            await query.message.edit_text(
                f"📅 Selecciona la <b>fecha de {label}</b> del rango:",
                reply_markup=key,
                parse_mode="HTML"
            )
            return

        if result:
            if step == 'start':
                context.user_data['date_start'] = result
                context.user_data['date_step'] = 'end'
                calendar, _ = DetailedTelegramCalendar(
                    min_date=result + timedelta(days=1)
                ).build()
                await query.message.edit_text(
                    f"📅 Inicio: <b>{result.strftime('%d/%m/%Y')}</b>\nAhora selecciona la <b>fecha de fin</b>:",
                    reply_markup=calendar,
                    parse_mode="HTML"
                )
            else:  # step == 'end'
                start_date = context.user_data.get('date_start')
                end_date = result
                
                if start_date:
                    if user_id not in self.user_date_filters:
                        self.user_date_filters[user_id] = []
                    self.user_date_filters[user_id].append({
                        'start': start_date.strftime('%Y-%m-%d'),
                        'end': end_date.strftime('%Y-%m-%d')
                    })
                    self._save_date_filters()
                
                # Clean up temp data
                context.user_data.pop('date_step', None)
                context.user_data.pop('date_start', None)
                
                await self._show_date_filter_menu(query.message, user_id, edit=True)

    async def send_html_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Send the interactive map HTML file"""
        # Get the appropriate message object based on update type
        message = update.message or update.callback_query.message
        
        try:
            if not self.stations_with_returns:
                await message.reply_text(
                    "No hay rutas disponibles. La base de datos se actualiza automáticamente."
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
            
        self.logger.info(f"Sent interactive map to user {update.effective_user.first_name} (ID: {update.effective_user.id})")
        
    async def send_jpeg_file(self, update: Update = None, context: ContextTypes.DEFAULT_TYPE = None, image_path: str = "", msg: str = "", user_id: str = None) -> bool:
        """Send the JPEG image of the map, either as a reply (when update is present) or directly to a user_id.
        Returns True if the message was delivered, False otherwise."""
        has_image = image_path and os.path.isfile(image_path)

        for attempt in range(3):
            try:
                if has_image:
                    with open(image_path, "rb") as f:
                        if update is not None:
                            message = update.message or update.callback_query.message
                            await message.reply_photo(
                                photo=InputFile(f, filename="image_path"),
                                caption=msg,
                                parse_mode=ParseMode.HTML
                            )
                        elif user_id is not None:
                            await context.bot.send_photo(
                                chat_id=user_id,
                                photo=InputFile(f, filename="image_path"),
                                caption=msg,
                                parse_mode=ParseMode.HTML
                            )
                        else:
                            self.logger.error("send_jpeg_file called without update or user_id.")
                            return False
                else:
                    # No valid image — send text only
                    if image_path:
                        self.logger.warning(f"Image file not found, sending text only: {image_path}")
                    if update is not None:
                        message = update.message or update.callback_query.message
                        await message.reply_text(msg, parse_mode=ParseMode.HTML)
                    elif user_id is not None:
                        await context.bot.send_message(chat_id=user_id, text=msg, parse_mode=ParseMode.HTML)
                    else:
                        self.logger.error("send_jpeg_file called without update or user_id.")
                        return False
                return True  # success
            except Exception as e:
                err_str = str(e)
                # Retry on flood control or timeout
                if ("Flood control" in err_str or "Timed out" in err_str) and attempt < 2:
                    import re as _re
                    wait = 5
                    m = _re.search(r"Retry in (\d+)", err_str)
                    if m:
                        wait = int(m.group(1)) + 1
                    self.logger.warning(f"Telegram rate limit, retrying in {wait}s (attempt {attempt+1}): {e}")
                    await asyncio.sleep(wait)
                    continue
                self.logger.error(f"Error sending message: {e}")
                # Last-resort fallback: try text-only
                try:
                    if update is not None:
                        message = update.message or update.callback_query.message
                        await message.reply_text(msg, parse_mode=ParseMode.HTML)
                    elif user_id is not None:
                        await context.bot.send_message(chat_id=user_id, text=msg, parse_mode=ParseMode.HTML)
                    return True  # fallback succeeded
                except Exception as e2:
                    self.logger.error(f"Error sending fallback message: {e2}")
                return False

        
    async def send_json_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Send the JSON file with all routes"""
        # Get the appropriate message object based on update type
        message = update.message or update.callback_query.message
        
        try:
            if not self.stations_with_returns:
                await message.reply_text(
                    "No hay rutas disponibles. La base de datos se actualiza automáticamente."
                )
                return
            
            with open(self.db_path, "rb") as f:
                await message.reply_document(
                    document=InputFile(f, filename="station_routes.json"),
                    caption="📄 Aquí tienes el archivo JSON con todas las rutas."
                )
        except Exception as e:
            self.logger.error(f"Error sending JSON file: {e}")
            await message.reply_text("❌ Error al enviar el archivo.")
            
        self.logger.info(f"Sent JSON file to user {update.effective_user.first_name} (ID: {update.effective_user.id})")

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

        self.logger.info(f"Sent help message to user {update.effective_user.first_name} (ID: {update.effective_user.id})")
        
        await message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN_V2)
        


    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle callback queries from inline keyboard"""
        query = update.callback_query
        await query.answer()

        try:
            if query.data == "show_routes":
                await self.show_routes(update, context)
            elif query.data == "show_favorites":
                await self.show_favorites(update, context)
            elif query.data in ("descargar_mapa", "send_html_file"):
                await self.send_html_file(update, context)
            elif query.data in ("help", "help_command"):
                await self.help_command(update, context)
            elif query.data == "add_favorite":
                await self.add_favorite(update, context)
            elif query.data == "remove_favorite":
                await self.remove_favorite(update, context)
            elif query.data.startswith("toggle_add_") or query.data.startswith("toggle_remove_"):
                await self._handle_station_toggle(query, context)
            elif query.data == "save_favorites":
                await self._handle_save_favorites(query, context)
            elif query.data == "set_date_filter":
                await self.set_date_filter(update, context)
            elif query.data.startswith("date_"):
                await self.handle_date_filter(query, context)
            elif query.data.startswith("cbcal_"):
                await self.handle_calendar_selection(query, context)
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

        # Save user favorites
        try:
            # Convert sets to lists for JSON serialization
            data = {user_id: list(stations) for user_id, stations in self.user_favorites.items()}
            with open(self.favorites_path, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            self.logger.error(f"Error saving favorites: {e}")
        
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
        image_path = ""
        
        for ret in station.get("returns", []):
            lines.append(f"🔁 <b>Destino</b>: <b>{ret['destination']}</b>")
            for d in ret.get("available_dates", []):
                date_line = f"📅 <code>{d['startDate']} → {d['endDate']}</code>"
                duration = d.get("duration")
                if duration:
                    date_line += f"  ⏱ {duration}"
                rate = d.get("rate")
                extra_rate = d.get("extra_rate")
                currency = d.get("currency", "EUR")
                sym = "£" if currency == "GBP" else "€"
                if rate is not None:
                    date_line += f"  💰 {sym}{rate:.2f}/n"
                    if extra_rate is not None and extra_rate > 0:
                        date_line += f" (+{sym}{extra_rate:.2f}/n extra)"
                lines.append(date_line)
            
            lines.append(f"🚐{ret.get('model_name', 'Modelo desconocido')}")
            booking_url = ret.get('roadsurfer_url', '#')
            if "indiecampers.com" in booking_url:
                link_label = "Ver en Indie Campers"
            elif "imoova.com" in booking_url:
                link_label = "Ver en Imoova"
            else:
                link_label = "Ver en Roadsurfer"
            lines.append(f"🌐 <a href='{booking_url}'>{link_label}</a>")
            model_image = ret.get("model_image", "")
            if model_image:
                image_path = os.path.join(self.assets_folder, model_image)
            
        
        return "\n".join(lines), image_path

    async def _job_update_database(self, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Job to automatically update the database – runs the heavy fetching in a
        background thread so the asyncio event loop (and all other handlers) stay
        fully responsive during the update."""
        if self._is_updating:
            self.logger.info("Auto-update skipped: another update is already in progress.")
            # Still reschedule so we check again soon
            if context.job_queue and not DEBUG_MODE:
                context.job_queue.run_once(self._job_update_database, when=60, name='database_update')
            return

        async with self._update_lock:
            self._is_updating = True
            try:
                if self.last_update_time == 0:
                    await initializer_message(self)
                self.logger.info("Starting automatic database update (background thread)...")

                loop = asyncio.get_event_loop()

                # ---- Build thread-safe callbacks --------------------------------
                last_percent = {'value': -1}

                def sync_progress_callback(percent: int):
                    if percent != last_percent['value']:
                        last_percent['value'] = percent
                        print(f"\rAuto-update progress: {percent}%", end="", flush=True)

                def sync_route_callback(route_data: Dict):
                    """Called from the worker thread; dispatches notification onto the event loop."""
                    asyncio.run_coroutine_threadsafe(
                        self._check_and_notify_route(route_data, context),
                        loop
                    )

                # ---- Fetch imoova relocations (first) ----
                self.logger.info("Auto-update: fetching imoova relocations...")
                imoova_data = await loop.run_in_executor(
                    self._update_executor,
                    lambda: self.imoova_fetcher.sync_full_update(
                        route_callback=sync_route_callback,
                    )
                )

                # Save after Imoova
                merged = list(imoova_data or [])
                self.stations_with_returns = merged
                self.data_fetcher.output_data = merged
                self.data_fetcher.save_output_to_json(self.db_path)

                # ---- Fetch Indie Campers deals ----
                self.logger.info("Auto-update: fetching Indie Campers deals...")
                indie_data = await loop.run_in_executor(
                    self._update_executor,
                    lambda: self.indie_campers_fetcher.sync_full_update(
                        route_callback=sync_route_callback,
                    )
                )

                # Save after Indie Campers
                merged = merged + (indie_data or [])
                self.stations_with_returns = merged
                self.data_fetcher.output_data = merged
                self.data_fetcher.save_output_to_json(self.db_path)

                # ---- Fetch Roadsurfer routes ----
                self.logger.info("Auto-update: fetching Roadsurfer routes...")
                output_data = await loop.run_in_executor(
                    self._update_executor,
                    lambda: self.data_fetcher.sync_full_update(
                        progress_callback=sync_progress_callback,
                        route_callback=sync_route_callback,
                    )
                )

                # Final merge and save
                merged = merged + (output_data or [])
                if not merged:
                    raise Exception("No se encontraron rutas disponibles")

                # ---- Back on the event loop: update shared state ----------------
                self.stations_with_returns = merged
                self.data_fetcher.output_data = merged
                self.data_fetcher.save_output_to_json(self.db_path)
                self.last_update_time = time.time()

                self.logger.info(
                    "Base de datos actualizada automaticamente — "
                    f"{len(self.stations_with_returns)} estaciones con rutas."
                )

                current_stations = self._load_stations()
                if current_stations:
                    await self._check_deleted_routes(current_stations, context)

            except Exception as e:
                self.logger.error(f"Error in auto-update job: {e}", exc_info=True)
            finally:
                self._is_updating = False
                # Reschedule immediately after completion (continuous loop)
                if context.job_queue and not DEBUG_MODE:
                    context.job_queue.run_once(
                        self._job_update_database,
                        when=300,
                        name='database_update'
                    )
                    self.logger.info("Next database update scheduled in 5 minutes")
            
    async def notify_all_users(self, message: str):
        """Send a message to all users in user_favorites"""
        with open(self.notification_history_path, 'r') as f:
            for user_id in json.load(f).keys():
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

    def _check_date_filters(self, user_id: str, date_str: str) -> bool:
        """Check if a date passes the user's filters"""
        if user_id not in self.user_date_filters:
            return True
            
        try:
            date = datetime.strptime(date_str, "%d/%m/%Y")
            filters = self.user_date_filters[user_id]
            
            # Check exclude filters first
            if 'exclude' in filters:
                exclude_start = datetime.strptime(filters['exclude']['start'], '%Y-%m-%d')
                exclude_end = datetime.strptime(filters['exclude']['end'], '%Y-%m-%d')
                if exclude_start <= date <= exclude_end:
                    return False
                    
            # Then check include filters
            if 'include' in filters:
                include_start = datetime.strptime(filters['include']['start'], '%Y-%m-%d')
                include_end = datetime.strptime(filters['include']['end'], '%Y-%m-%d')
                return include_start <= date <= include_end
                
            return True
        except Exception as e:
            self.logger.error(f"Error checking date filters: {e}")
            return True

async def initializer_message(bot: RoadsurferBot):
    """Send an initializer message asynchronously."""
    try:
        await bot.notify_all_users("✅ El bot está nuevamente en línea. ¡Ya puedes usarlo!")
    except Exception as e:
        bot.logger.error(f"Error sending initializer message: {e}")

async def shutdown_message(bot: RoadsurferBot):
    """Handle shutdown message asynchronously."""
    bot.logger.info("SIGINT or CTRL-C detected. Notifying users and shutting down...")
    try:
        await bot.notify_all_users("⚠️ El bot está en mantenimiento hasta nuevo aviso.")
    except Exception as e:
        bot.logger.error(f"Error notifying users: {e}")
        
        
def handle_sigint(bot: RoadsurferBot):
    """Handle SIGINT signal."""
    async def shutdown_and_exit():
        if not DEBUG_MODE:
            await shutdown_message(bot)
        sys.exit(0)

    loop = asyncio.get_event_loop()
    loop.create_task(shutdown_and_exit())
    
if __name__ == "__main__":
    """Main entry point for the bot."""
    # Initialize logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            RotatingFileHandler(
                'bot.log',
                encoding='utf-8',
                maxBytes=1 * 1024 * 1024,  # 1 MB per file
                backupCount=2,             # keep bot.log + 2 rotated backups
            ),
        ]
    )

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext").setLevel(logging.INFO)
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    LOGGER_TOKEN = os.getenv("LOGGER_TOKEN")
    TELEGRAM_LOG_CHAT_ID = os.getenv("LOGGER_CHAT_ID")

    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN not found in environment variables")

    
    bot = RoadsurferBot(BOT_TOKEN, LOGGER_TOKEN)

    # Register the SIGINT handler for graceful shutdown
    signal.signal(signal.SIGINT, lambda signal_received, frame: handle_sigint(bot))

    try:
        bot.run(),  # Start the bot polling
        
    except Exception as e:
        logging.error(f"Error starting bot: {e}", exc_info=True)

