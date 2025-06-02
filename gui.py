import folium
from geopy.geocoders import Nominatim
import time
import os
import json
from branca.element import Element
from urllib.parse import quote


def gui(progress_callback=None):
    # Paths
    file_path = "routes.txt"
    cache_file = "geocode_cache.json"

    # Load cache
    if os.path.exists(cache_file):
        with open(cache_file, "r", encoding="utf-8") as f:
            geocode_cache = json.load(f)
    else:
        geocode_cache = {}

    # Optional filter
    filter_names = []

    # Load routes from JSON
    json_input_path = "station_routes.json"  # Output from earlier script
    with open(json_input_path, "r") as f:
        station_data = json.load(f)

    routes = []
    for entry in station_data:
        origin = entry["origin"]
        for ret in entry["returns"]:
            destination = ret["destination"]
            url = f"https://www.google.com/maps/dir/{quote(entry['origin_address'])}/{quote(ret['destination_address'])}"
            dates = ", ".join([f"{d['startDate']} - {d['endDate']}" for d in ret["available_dates"]])
            routes.append({
                "origin": origin,
                "destination": destination,
                "url": url,
                "dates": dates
            })


    # Set up geocoder
    geolocator = Nominatim(user_agent="route_mapper")

    # Create map centered in Europe
    m = folium.Map(location=[48.5, 9], zoom_start=5)

    # Geocoding with cache
    def geocode(city):
        if city in geocode_cache:
            return tuple(geocode_cache[city])
        try:
            location = geolocator.geocode(city)
            if location:
                coords = (location.latitude, location.longitude)
                geocode_cache[city] = coords
                with open(cache_file, "w", encoding="utf-8") as f:
                    json.dump(geocode_cache, f, ensure_ascii=False, indent=2)
                return coords
        except:
            pass
        return None

    # Add routes to map
    total = len(routes)
    for idx, route in enumerate(routes):
        origin_coords = geocode(route["origin"])
        destination_coords = geocode(route["destination"])
        if origin_coords and destination_coords:
            popup_html = f"""
            <b>{route['origin']} ➜ {route['destination']}</b><br>
            <a href="{route['url']}" target="_blank">Ver ruta en Google Maps</a><br>
            Fechas: {route['dates']}
            """
            feature = {
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
            }

            route_id = f"route{idx}"
            gj = folium.GeoJson(
                data=feature,
                style_function=lambda x: {"color": "blue", "weight": 5, "opacity": 1},
                name=route["origin"] + " ➜ " + route["destination"],
                
            )
            gj.add_child(folium.Popup(popup_html))

            # Link this layer to JS routeLayers dict
            gj.add_child(Element(f"""
            <script>
                routeLayers["{route_id}"] = {gj.get_name()};
            </script>
            """))

            gj.add_to(m)
            time.sleep(0.1)
        if progress_callback:
            percent = int((idx + 1) / total * 100)
            progress_callback(percent)

    # Unique city list for filtering
    unique_cities = sorted(set(
        [route["origin"] for route in routes] + [route["destination"] for route in routes]
    ))

    # Sidebar HTML with checkboxes for filtering
    sidebar_html = """
    <div id='route-sidebar'>
        <h3>Filtro de ciudades</h3>
        <div id="city-filters">
    """
    for city in unique_cities:
        city_id = city.replace(" ", "_").replace(",", "")
        sidebar_html += f"""
            <label>
                <input type="checkbox" class="city-filter" value="{city}" onchange="applyCityFilter()">
                {city}
            </label><br>
        """
    sidebar_html += "</div><hr><h3>Rutas</h3><ul id='route-list'>"

    for idx, route in enumerate(routes):
        route_id = f"route{idx}"
        sidebar_html += f"""
        <li class='route-item' data-origin="{route['origin']}" data-destination="{route['destination']}" data-id="{route_id}">
            <b>{route['origin']} ➜ {route['destination']}</b><br>
            <a href="{route['url']}" target="_blank">Ver ruta en Google Maps</a><br>
            Fechas: {route['dates']}
        </li>
        """

    sidebar_html += "</ul></div>"
    sidebar_styles_scripts = """
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


    m.get_root().html.add_child(Element(sidebar_styles_scripts))
    m.get_root().html.add_child(Element(sidebar_html))

    # Save to file
    m.save("rutas_interactivas.html")
    print("Mapa guardado como rutas_interactivas.html")
