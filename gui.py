import folium
from geopy.geocoders import Nominatim
import time
import json
from branca.element import Element
from urllib.parse import quote
import logging
from typing import Dict, List, Tuple, Optional, Callable
from pathlib import Path

# Initialize logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class RouteMapGenerator:
    def __init__(self):
        self.DB_PATH = Path("station_routes.json")
        self.CACHE_PATH = Path("geocode_cache.json")
        self.OUTPUT_PATH = Path("rutas_interactivas.html")
        self.geocode_cache: Dict[str, Tuple[float, float]] = {}
        self.geolocator = Nominatim(user_agent="route_mapper", timeout=10)
        self.routes: List[Dict] = []
        
    def _load_cache(self) -> None:
        """Load geocoding cache from file"""
        try:
            if self.CACHE_PATH.exists():
                with open(self.CACHE_PATH, "r", encoding="utf-8") as f:
                    self.geocode_cache = json.load(f)
                logger.info(f"Loaded {len(self.geocode_cache)} cached locations")
        except Exception as e:
            logger.error(f"Error loading geocode cache: {e}")
            self.geocode_cache = {}

    def _save_cache(self) -> None:
        """Save geocoding cache to file"""
        try:
            with open(self.CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump(self.geocode_cache, f, ensure_ascii=False, indent=2)
            logger.info("Geocode cache saved successfully")
        except Exception as e:
            logger.error(f"Error saving geocode cache: {e}")

    def _load_routes(self) -> None:
        """Load routes from JSON file"""
        try:
            if not self.DB_PATH.exists():
                raise FileNotFoundError(f"Routes file not found: {self.DB_PATH}")
                
            with open(self.DB_PATH, "r") as f:
                station_data = json.load(f)

            self.routes = []
            for entry in station_data:
                origin = entry["origin"]
                for returns in entry["returns"]:
                    destination = returns["destination"]
                    url = f"https://www.google.com/maps/dir/{quote(entry['origin_address'])}/{quote(returns['destination_address'])}"
                    dates = ", ".join([f"{d['startDate']} - {d['endDate']}" for d in returns["available_dates"]])
                    self.routes.append({
                        "origin": origin,
                        "origin_address": entry["origin_address"],
                        "destination": destination,
                        "destination_address": returns["destination_address"],
                        "url": url,
                        "dates": dates
                    })
            logger.info(f"Loaded {len(self.routes)} routes")
        except Exception as e:
            logger.error(f"Error loading routes: {e}")
            raise

    def _geocode(self, address: str, city: str) -> Optional[Tuple[float, float]]:
        """Geocode an address and city with caching and fallback."""
        # Check if the city is already cached
        if city in self.geocode_cache:
            return tuple(self.geocode_cache[city])
        
        try:
            # Attempt to geocode using the full address (address + city)
            location = self.geolocator.geocode(f"{address}, {city}")
            if location:
                coords = (location.latitude, location.longitude)
                self.geocode_cache[city] = coords  # Cache the result
                self._save_cache()  # Save cache to file
                logger.debug(f"Geocoded {city} ({address}): {coords}")
                return coords
            else:
                logger.warning(f"Primary geocoding failed for '{address}, {city}'. Trying fallback...")
            
            location = self.geolocator.geocode(address)
            if location:
                coords = (location.latitude, location.longitude)
                self.geocode_cache[city] = coords
                self._save_cache()
                logger.debug(f"Geocoded {city} (address only): {coords}")
                return coords

            # Fallback: Try geocoding with just the city name
            location = self.geolocator.geocode(city)
            if location:
                coords = (location.latitude, location.longitude)
                self.geocode_cache[city] = coords  # Cache the result
                self._save_cache()  # Save cache to file
                logger.debug(f"Fallback geocoded {city}: {coords}")
                return coords
            else:
                logger.warning(f"Fallback geocoding failed for city '{city}'. No coordinates found.")

        except Exception as e:
            logger.error(f"Error geocoding '{address}, {city}': {e}")

        # If all attempts fail, return None
        return None


    def _create_route_feature(self, route: Dict, idx: int) -> Optional[Dict]:
        """Create a GeoJSON feature for a route"""
        origin_coords = self._geocode(route['origin_address'], route['origin'])
        #time.sleep(1)
        destination_coords = self._geocode(route['destination_address'], route['destination'])
        #time.sleep(1)
        
        if not origin_coords or not destination_coords:
            #logger.warning(f"\033[92mCould not geocode coordinates for route: {route["origin_address"]}, {route['origin']} -> {route['destination_address']}, {route['destination']}\033[0m")
            logger.warning(origin_coords, destination_coords)
            return None

        popup_html = f"""
        <b>{route['origin']} ➜ {route['destination']}</b><br>
        <a href="{route['url']}" target="_blank">Ver ruta en Google Maps</a><br>
        Fechas: {route['dates']}
        """

        return {
            "feature": {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [origin_coords[1], origin_coords[0]],
                        [destination_coords[1], destination_coords[0]]
                    ]
                },
                "properties": {
                    "popupContent": popup_html
                }
            },
            "route_id": f"route{idx}",
            "popup_html": popup_html,
            "name": f"{route['origin']} ➜ {route['destination']}"
        }

    def _create_sidebar_html(self, unique_cities: List[str]) -> str:
        """Create HTML for the sidebar with city filters and route list"""
        sidebar_html = """
        <div id='route-sidebar'>
            <h3>Filtro de ciudades</h3>
            <div id="city-filters">
        """
        
        # Add city filters
        for city in unique_cities:
            city_id = city.replace(" ", "_").replace(",", "")
            sidebar_html += f"""
                <label>
                    <input type="checkbox" class="city-filter" value="{city}" onchange="applyCityFilter()">
                    {city}
                </label><br>
            """
            
        sidebar_html += "</div><hr><h3>Rutas</h3><ul id='route-list'>"
        
        # Add route list
        for idx, route in enumerate(self.routes):
            route_id = f"route{idx}"
            sidebar_html += f"""
            <li class='route-item' data-origin="{route['origin']}" data-destination="{route['destination']}" data-id="{route_id}">
                <b>{route['origin']} ➜ {route['destination']}</b><br>
                <a href="{route['url']}" target="_blank">Ver ruta en Google Maps</a><br>
                Fechas: {route['dates']}
            </li>
            """
            
        sidebar_html += "</ul></div>"
        return sidebar_html

    def _get_styles_and_scripts(self) -> str:
        """Get CSS styles and JavaScript for the map"""
        return """
        <style>
            #map {
                position: absolute;
                top: 0;
                left: 0;
                right: 300px;
                bottom: 0;
                z-index: 0;
            }
            #route-sidebar {
                position: absolute;
                top: 0;
                right: 0;
                width: 300px;
                height: 100%;
                overflow-y: auto;
                background: white;
                z-index: 1000;
                border-left: 1px solid #ccc;
                padding: 10px;
                font-family: Arial, sans-serif;
                font-size: 14px;
            }
            #route-sidebar ul {
                list-style: none;
                padding-left: 0;
            }
            #route-sidebar li {
                cursor: pointer;
                margin: 5px 0;
                padding: 5px;
                border-bottom: 1px solid #eee;
            }
            #route-sidebar li:hover {
                background-color: #f0f0f0;
            }
        </style>

        <script>
            var routeLayers = {};

            function highlightRoute(key) {
                for (const k in routeLayers) {
                    routeLayers[k].setStyle({ color: 'blue' });
                }
                if (routeLayers[key]) {
                    routeLayers[key].setStyle({ color: 'green' });
                    routeLayers[key].bringToFront();
                }
            }

            function applyCityFilter() {
                const checkboxes = document.querySelectorAll('.city-filter:checked');
                const selectedCities = Array.from(checkboxes).map(cb => cb.value);

                const routeItems = document.querySelectorAll('.route-item');
                for (let item of routeItems) {
                    const origin = item.getAttribute('data-origin');
                    const destination = item.getAttribute('data-destination');
                    const id = item.getAttribute('data-id');
                    const match = selectedCities.length === 0 || selectedCities.includes(origin) || selectedCities.includes(destination);

                    item.style.display = match ? 'block' : 'none';

                    if (routeLayers[id]) {
                        routeLayers[id].setStyle({ opacity: match ? 1 : 0 });
                    }
                }
            }
        </script>
        """

    def generate_map(self, progress_callback: Optional[Callable[[int], None]] = None) -> None:
        """Generate the interactive map with routes"""
        try:
            # Load data
            self._load_cache()
            self._load_routes()
            
            # Create base map
            m = folium.Map(location=[48.5, 9], zoom_start=5)
            
            # Add routes to map
            total = len(self.routes)
            for idx, route in enumerate(self.routes):
                route_data = self._create_route_feature(route, idx)
                if route_data:
                    gj = folium.GeoJson(
                        data=route_data["feature"],
                        style_function=lambda x: {"color": "blue", "weight": 5, "opacity": 1},
                        name=route_data["name"]
                    )
                    gj.add_child(folium.Popup(route_data["popup_html"]))
                    gj.add_child(Element(f"""
                    <script>
                        routeLayers["{route_data['route_id']}"] = {gj.get_name()};
                    </script>
                    """))
                    gj.add_to(m)
                    time.sleep(0.1)

                if progress_callback:
                    percent = int((idx + 1) / total * 100)
                    progress_callback(percent)

            # Get unique cities for filtering
            unique_cities = sorted(set(
                [route["origin"] for route in self.routes] + 
                [route["destination"] for route in self.routes]
            ))

            # Add sidebar and styles
            sidebar_html = self._create_sidebar_html(unique_cities)
            styles_scripts = self._get_styles_and_scripts()
            
            m.get_root().html.add_child(Element(styles_scripts))
            m.get_root().html.add_child(Element(sidebar_html))

            # Save map
            m.save(str(self.OUTPUT_PATH))
            logger.info(f"Map saved successfully as {self.OUTPUT_PATH}")
            
        except Exception as e:
            logger.error(f"Error generating map: {e}")
            raise

def gui(progress_callback: Optional[Callable[[int], None]] = None) -> None:
    """Main function to generate the interactive map"""
    try:
        map_generator = RouteMapGenerator()
        map_generator.generate_map(progress_callback)
    except Exception as e:
        logger.error(f"Error in gui function: {e}")
        raise
