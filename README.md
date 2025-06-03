# 🚐 Roadsurfer Rally Bot

A Telegram bot that helps users find and track available vehicle transfer routes between different Roadsurfer stations across Europe.

## Features

- 🔍 **Real-time Route Search**: Find available vehicle transfer routes between Roadsurfer stations
- 🗺️ **Interactive Map**: Visual representation of all available routes with filtering capabilities
- ⭐ **Favorites System**: Save and track your preferred stations
- 🔔 **Route Notifications**: Get notified when new routes are available for your favorite stations
- 📊 **Route Management**: View and manage all available routes
- 🌍 **Geocoding**: Automatic location handling for all stations

## Prerequisites

- Python 3.7+
- Telegram Bot Token (from [@BotFather](https://t.me/botfather))

## Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd rally_bot
```

2. Install required dependencies:
```bash
pip install -r requirements.txt
```

3. Create a `.env` file in the root directory and add your Telegram bot token:
```
BOT_TOKEN=your_bot_token_here
```

## Project Structure

```
rally_bot/
├── run_bot.py          # Main bot script
├── api_utils.py        # API interaction utilities
├── data_utils.py       # Data processing utilities
├── gui.py             # Interactive map generation
├── requirements.txt    # Project dependencies
├── .env               # Environment variables (not tracked)
└── README.md          # Project documentation
```

## Usage

1. Start the bot:
```bash
python run_bot.py
```

2. In Telegram, start a chat with your bot and use `/start` to begin.

## Available Commands

- `/start` - Start the bot and see the main menu
- `/actualizar_rutas` - Update the routes database
- `/ver_rutas` - View all available routes
- `/favoritos` - View your favorite stations
- `/agregar_favorito` - Add a station to favorites
- `/eliminar_favorito` - Remove a station from favorites
- `/descargar_mapa` - Download interactive map
- `/help` - Show help and available commands

## Interactive Map

The bot generates an interactive HTML map that shows:
- All available routes between stations
- Route details and dates
- Filtering options by city
- Direct links to Google Maps directions

## Data Storage

- `station_routes.json` - Stores current route information
- `user_favorites.json` - Stores user favorite stations
- `geocode_cache.json` - Caches geocoding data for performance
- `rutas_interactivas.html` - Generated interactive map

## Error Handling

The bot includes comprehensive error handling and logging:
- Input validation
- API error handling
- Geocoding fallbacks
- File operation safety checks

## Contributing

Feel free to submit issues, fork the repository, and create pull requests for any improvements.

## License

This project is licensed under the terms of the license included in the repository.

## Author

Created by @arlloren

## Acknowledgments

- [python-telegram-bot](https://python-telegram-bot.org/) for the Telegram bot framework