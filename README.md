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
git clone https://github.com/ArturoLlorente/rally_bot.git
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

We love your input! We want to make contributing to Roadsurfer Rally Bot as easy and transparent as possible. Here's how you can contribute:

### Development Process

1. Fork the repository:
   ```bash
   # Clone your fork
   git clone https://github.com/your-username/rally_bot.git
   cd rally_bot

   # Add the original repository as upstream
   git remote add upstream https://github.com/ArturoLlorente/rally_bot.git
   ```

2. Create a new branch:
   ```bash
   # Update your local main
   git checkout main
   git pull upstream main

   # Create a new feature branch
   git checkout -b feature/your-feature-name
   # or for bugfixes
   git checkout -b fix/your-fix-name
   ```

3. Make your changes:
   - Write meaningful commit messages
   - Keep commits atomic and small
   - Reference issues and pull requests liberally

4. Push your changes:
   ```bash
   git push origin feature/your-feature-name
   ```

5. Create a Pull Request:
   - Go to your fork on GitHub
   - Click 'Pull Request' button
   - Select your feature branch
   - Add a clear title and description
   - Link any relevant issues

### Branch Naming Convention

- `feature/*`: For new features
- `fix/*`: For bug fixes
- `docs/*`: For documentation changes
- `refactor/*`: For code refactoring
- `test/*`: For test additions or modifications

### Commit Message Guidelines

Structure your commit messages like this:
```
<type>(<scope>): <subject>

<body>

<footer>
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Code style changes (formatting, etc)
- `refactor`: Code refactoring
- `test`: Adding or modifying tests
- `chore`: Maintenance tasks

Example:
```
feat(map): add city filtering to interactive map

- Add checkbox filters for cities
- Implement dynamic route filtering
- Update map display based on selection

Closes #123
```

### Pull Request Process

1. Update the README.md with details of changes if needed
2. Update the requirements.txt if you add dependencies
3. Make sure all tests pass and add new ones if needed
4. Get a code review from maintainers

### Code Style

- Follow PEP 8 guidelines
- Use type hints where possible
- Add docstrings to functions and classes
- Keep functions small and focused
- Use meaningful variable names

### Questions or Problems?

Feel free to:
- Open an issue for discussion
- Ask questions in pull requests
- Contact the maintainers directly

We appreciate your contributions to making Roadsurfer Rally Bot better!

## License

This project is licensed under the terms of the license included in the repository.

## Author

Created by @arlloren

## Acknowledgments

- [python-telegram-bot](https://python-telegram-bot.org/) for the Telegram bot framework